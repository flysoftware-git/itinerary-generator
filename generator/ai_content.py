"""
ai_content.py — Multi-provider LLM content generation.

CRITICAL: AI must NEVER generate URLs. This module produces names,
descriptions, schedules, and structured content only. All URLs are
discovered separately by url_discovery.py after this stage completes.
"""
from __future__ import annotations
from datetime import datetime
from difflib import SequenceMatcher
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from generator.llm_client import MultiLLMClient

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class AIContentGenerator:
    _CHAIN_NAME_TOKENS = {
        "mcdonald",
        "burger king",
        "chick-fil-a",
        "chick fil a",
        "taco bell",
        "subway",
        "kfc",
        "wendy",
        "domino",
        "pizza hut",
        "papa john",
        "little caesars",
        "starbucks",
        "dunkin",
        "chipotle",
        "panera",
        "arby",
        "sonic",
        "jack in the box",
        "dairy queen",
        "popeyes",
    }

    _FAST_FOOD_TOKENS = {
        "fast food",
        "quick service",
        "drive-thru",
        "drive thru",
        "fried chicken chain",
        "chain restaurant",
    }

    def __init__(
        self,
        config_path: Path | str = "config.yaml",
        llm_client: MultiLLMClient | None = None,
    ) -> None:
        import yaml
        with Path(config_path).open() as f:
            self._config = yaml.safe_load(f)
        self._llm = llm_client or MultiLLMClient(config_path)
        self._system_prompt = (PROMPTS_DIR / "system_prompt.txt").read_text(encoding="utf-8")
        self._dest_template = (PROMPTS_DIR / "destination_content.txt").read_text(encoding="utf-8")
        self._drives_template = (PROMPTS_DIR / "scenic_drives.txt").read_text(encoding="utf-8")
        self._what_to_know_template = (PROMPTS_DIR / "what_to_know.txt").read_text(encoding="utf-8")
        self._weather_cache: dict[tuple[float, float, int], tuple[int, int] | None] = {}
        self._enable_url_candidate_experiment = bool(
            self._config.get("ai", {}).get("enable_url_candidate_experiment", False)
        )

    def generate_destination_content(self, trip: dict[str, Any]) -> None:
        """Generate AI content for every destination. Attaches 'ai_content' in-place."""
        destinations = trip.get("destinations", [])
        prev_names = ["none"] + [d["name"] for d in destinations[:-1]]
        next_names = [d["name"] for d in destinations[1:]] + [""]

        def _one(args: tuple[int, dict]) -> None:
            i, dest = args
            logger.info("Generating AI content for '%s'…", dest["name"])
            dest["ai_content"] = self._generate_for_destination(dest, trip["trip"], prev_names[i], next_names[i])
            dest["what_to_know"] = self._generate_what_to_know(dest, trip["trip"], prev_names[i], next_names[i])

        with ThreadPoolExecutor(max_workers=min(len(destinations), 4)) as pool:
            futures = [pool.submit(_one, (i, d)) for i, d in enumerate(destinations)]
            for f in as_completed(futures):
                f.result()

    def generate_scenic_drive_descriptions(self, trip: dict[str, Any]) -> None:
        """Generate scenic drive popup descriptions. Attaches 'scenic_drives' in-place."""
        destinations = trip.get("destinations", [])

        def _one(dest: dict) -> None:
            logger.info("Generating scenic drives for '%s'…", dest["name"])
            result = self._generate_drives(dest)
            dest["scenic_drives"] = result
            logger.debug(f"  Set scenic_drives for {dest['name']}: {len(result)} drives")

        with ThreadPoolExecutor(max_workers=min(len(destinations), 4)) as pool:
            futures = [pool.submit(_one, d) for d in destinations]
            for f in as_completed(futures):
                f.result()
        
        # Verify all destinations have scenic_drives
        for dest in destinations:
            count = len(dest.get("scenic_drives", []))
            logger.info(f"✓ {dest['name']}: {count} scenic_drives")

    def generate_all(self, trip: dict[str, Any]) -> None:
        self.generate_destination_content(trip)
        self.generate_scenic_drive_descriptions(trip)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def _generate_what_to_know(
        self,
        dest: dict[str, Any],
        trip_meta: dict[str, Any],
        previous_destination: str,
        next_destination: str,
    ) -> dict[str, str]:
        season = self._season_from_dates(dest.get("dates", ""))
        trip_type = str(trip_meta.get("subtitle", "") or trip_meta.get("title", "") or "road trip").strip()
        nearby_days = self._nearby_day_window(dest.get("dates", ""))

        prompt = self._render_prompt_template(
            self._what_to_know_template,
            destination_name=dest.get("name", ""),
            dates=dest.get("dates", ""),
            season=season,
            nearby_days=nearby_days,
            trip_type=trip_type,
            previous_destination=previous_destination,
            next_destination=next_destination or "none",
            budget_guidance=self._build_budget_guidance(trip_meta),
        )

        try:
            payload = self._llm.generate_json(
                system_prompt=self._system_prompt,
                user_prompt=prompt,
                operation=f"what_to_know:{dest['id']}",
                temperature=0.4,
                max_tokens=1400,
            )
        except Exception as exc:
            logger.warning("What-to-Know generation failed for '%s': %s", dest.get("name", ""), exc)
            payload = {}

        return self._normalize_what_to_know(payload, dest)

    @staticmethod
    def _render_prompt_template(template: str, **values: Any) -> str:
        """Replace only known {placeholders} and leave literal JSON braces intact."""
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered

    def _normalize_what_to_know(self, payload: Any, dest: dict[str, Any]) -> dict[str, str]:
        if not isinstance(payload, dict):
            payload = {}

        required_fields = [
            "summary",
            "local_customs",
            "best_times_of_day",
            "transportation_quirks",
            "safety_considerations",
            "crowd_patterns",
            "local_etiquette",
        ]

        normalized: dict[str, str] = {}
        for field in required_fields:
            normalized[field] = str(payload.get(field, "") or "").strip()

        if not normalized["summary"]:
            season = self._season_from_dates(dest.get("dates", ""))
            normalized["summary"] = (
                f"{dest.get('name', 'This destination')} in {season} can vary by hour and conditions; "
                "confirm weather, trail status, and operating hours the day before arrival."
            )

        fallback_defaults = {
            "local_customs": "Follow posted rules, respect quiet areas, and support local businesses with patience during peak windows.",
            "best_times_of_day": "Early morning and late afternoon usually provide easier parking and calmer conditions.",
            "transportation_quirks": "Plan for limited parking in core areas and possible shuttle, permit, or reservation constraints.",
            "safety_considerations": "Carry water, layers, and navigation backup; check alerts and avoid pushing exposure during heat or storms.",
            "crowd_patterns": "Midday is often busiest near major trailheads and viewpoints; quieter windows are usually early and late.",
            "local_etiquette": "Yield politely on narrow paths, keep noise low at viewpoints, and pack out all trash.",
        }
        for key, fallback in fallback_defaults.items():
            if not normalized[key]:
                normalized[key] = fallback

        return normalized

    def _season_from_dates(self, dates: str) -> str:
        month = self._extract_month_index(dates)
        if month in (12, 1, 2):
            return "winter"
        if month in (3, 4, 5):
            return "spring"
        if month in (6, 7, 8):
            return "summer"
        if month in (9, 10, 11):
            return "fall"
        return "the current season"

    def _nearby_day_window(self, dates: str) -> str:
        inferred = self._infer_day_count(dates)
        if inferred <= 1:
            return "single-day stop"
        if inferred == 2:
            return "two-day stay"
        return f"{inferred}-day stay"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def _generate_for_destination(
        self, dest: dict[str, Any], trip_meta: dict[str, Any], prev: str, next_dest: str
    ) -> dict[str, Any]:
        seeds = dest.get("seeds", [])
        prompt = self._dest_template.format(
            destination_name=dest["name"],
            dates=dest["dates"],
            trip_title=trip_meta["title"],
            previous_destination=prev,
            next_destination=next_dest or "none",
            budget_guidance=self._build_budget_guidance(trip_meta),
            seeds="\n  ".join(f"- {s}" for s in seeds) if seeds else "  (none — generate full recommendations)",
        )
        result = self._llm.generate_json(
            system_prompt=self._system_prompt,
            user_prompt=prompt,
            operation=f"destination_content:{dest['id']}",
            temperature=self._config.get("ai", {}).get("temperature", self._config.get("azure_openai", {}).get("temperature", 0.7)),
            max_tokens=self._config.get("ai", {}).get("max_tokens", self._config.get("azure_openai", {}).get("max_tokens", 4096)),
        )
        if self._enable_url_candidate_experiment:
            result = self._augment_with_url_candidates(result, dest)
        return self._normalize_destination_content(
            result,
            dest.get("dates", ""),
            dest,
            trip_meta,
            prev,
            next_dest,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def _generate_drives(self, dest: dict[str, Any]) -> list[dict[str, Any]]:
        # Derive region from destination name
        region_map = {"utah": "Utah", "colorado": "Colorado", "new mexico": "New Mexico",
                      "arizona": "Arizona", "nevada": "Nevada", "california": "California"}
        name_lower = dest["name"].lower()
        region = next((v for k, v in region_map.items() if k in name_lower), "Western United States")

        prompt = self._drives_template.format(
            destination_name=dest["name"],
            dates=dest["dates"],
            region=region,
        )
        data = self._llm.generate_json(
            system_prompt=self._system_prompt,
            user_prompt=prompt,
            operation=f"scenic_drives:{dest['id']}",
            temperature=self._config.get("ai", {}).get("temperature", self._config.get("azure_openai", {}).get("temperature", 0.7)),
            max_tokens=2048,
        )
        return data.get("scenic_drives", [])

    def _build_budget_guidance(self, trip_meta: dict[str, Any]) -> str:
        budget = trip_meta.get("budget")
        if budget in (None, "", {}):
            return "No explicit trip budget provided."

        if isinstance(budget, str):
            return f"Budget preference: {budget}"

        if isinstance(budget, (int, float)):
            return f"Budget cap noted: {budget}"

        if isinstance(budget, dict):
            parts: list[str] = []
            for key, value in budget.items():
                label = str(key).replace("_", " ")
                parts.append(f"{label}={value}")
            if parts:
                return "Budget guidance: " + "; ".join(parts)

        return f"Budget guidance: {budget}"

    def _normalize_destination_content(
        self,
        payload: dict[str, Any],
        dates: str,
        dest: dict[str, Any],
        trip_meta: dict[str, Any],
        previous_destination: str,
        next_destination: str,
    ) -> dict[str, Any]:
        seed_names = [str(s or "").strip() for s in (dest.get("seeds", []) or []) if str(s or "").strip()]
        payload["expected_environment"] = self._normalize_environment(
            payload.get("expected_environment", {}),
            dates,
            dest,
        )
        payload["getting_here"] = self._normalize_getting_here(
            payload.get("getting_here", {}),
            dest.get("name", ""),
        )
        normalized_attractions = self._normalize_attractions(payload.get("top_attractions", []))
        normalized_attractions = self._ensure_seed_attractions(normalized_attractions, seed_names)
        payload["top_attractions"] = self._remove_enroute_stops_from_attractions(
            normalized_attractions,
            payload.get("getting_here", {}),
            protected_names=seed_names,
        )
        payload["possible_daily_schedule"] = self._normalize_schedule(
            payload.get("possible_daily_schedule", {}),
            payload.get("dinner_recommendations", []),
            dates,
            payload.get("getting_here", {}),
            previous_destination,
            next_destination,
            str(trip_meta.get("departure", "") or "").strip(),
            str(trip_meta.get("return", "") or "").strip(),
        )
        payload["dinner_recommendations"] = self._normalize_restaurants(
            payload.get("dinner_recommendations", []),
            trip_meta.get("budget"),
        )
        return payload

    def _remove_enroute_stops_from_attractions(
        self,
        attractions: list[dict[str, Any]],
        getting_here: dict[str, Any],
        protected_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        stops = getting_here.get("en_route_stops", []) if isinstance(getting_here, dict) else []
        stop_names = [str(s.get("name", "") or "").strip() for s in stops if isinstance(s, dict)]
        stop_names = [s for s in stop_names if s]
        if not attractions or not stop_names:
            return attractions

        def norm(text: str) -> str:
            n = text.lower().strip()
            n = re.sub(r"[^a-z0-9\s]", " ", n)
            n = re.sub(r"\b(trail|road|highway|route|state\s+park|national\s+park|park|overlook|viewpoint)\b", " ", n)
            n = re.sub(r"\s+", " ", n).strip()
            return n

        stop_norm = [norm(name) for name in stop_names]
        stop_norm = [s for s in stop_norm if s]
        if not stop_norm:
            return attractions

        protected = {
            self._canonical_seed_name(name)
            for name in (protected_names or [])
            if self._canonical_seed_name(name)
        }

        filtered: list[dict[str, Any]] = []
        for attraction in attractions:
            attr_name = str(attraction.get("name", "") or "").strip()
            if self._canonical_seed_name(attr_name) in protected:
                filtered.append(attraction)
                continue
            attr_norm = norm(attr_name)
            if not attr_norm:
                filtered.append(attraction)
                continue

            is_enroute_match = False
            for stop_name in stop_norm:
                if attr_norm == stop_name:
                    is_enroute_match = True
                    break
                if attr_norm in stop_name or stop_name in attr_norm:
                    is_enroute_match = True
                    break
                if SequenceMatcher(None, attr_norm, stop_name).ratio() >= 0.9:
                    is_enroute_match = True
                    break

            if not is_enroute_match:
                filtered.append(attraction)

        return filtered

    @staticmethod
    def _canonical_seed_name(text: str) -> str:
        n = (text or "").lower().strip()
        n = re.sub(r"[^a-z0-9\s]", " ", n)
        n = re.sub(r"\b(trail|road|highway|route|state\s+park|national\s+park|park|overlook|viewpoint)\b", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        return n

    def _ensure_seed_attractions(self, attractions: list[dict[str, Any]], seed_names: list[str]) -> list[dict[str, Any]]:
        if not seed_names:
            return attractions

        existing = {
            self._canonical_seed_name(str(item.get("name", "") or ""))
            for item in attractions
            if isinstance(item, dict)
        }

        out = list(attractions)
        for seed in seed_names:
            if self._canonical_seed_name(seed) in existing:
                continue

            seed_lower = seed.lower()
            inferred_type = "hike" if re.search(r"\b(trail|hike|loop|arch|falls|summit|narrows)\b", seed_lower) else "attraction"
            out.append(
                {
                    "name": seed,
                    "type": inferred_type,
                    "difficulty": "N/A",
                    "duration": "",
                    "must_see": False,
                    "description": "Traveler-specified seed attraction; details will be refined by linked references.",
                    "practical_note": "",
                }
            )
            existing.add(self._canonical_seed_name(seed))

        return self._normalize_attractions(out)

    def _normalize_getting_here(self, getting_here: Any, dest_name: str) -> dict[str, Any]:
        if not isinstance(getting_here, dict):
            return {}
        out = dict(getting_here)
        normalized_stops: list[dict[str, Any]] = []
        for stop in out.get("en_route_stops", []) or []:
            if not isinstance(stop, dict):
                continue
            item = dict(stop)
            if item.get("detour_distance_miles") in (None, ""):
                item["detour_distance_miles"] = 0
            if item.get("detour_time_minutes") in (None, ""):
                item["detour_time_minutes"] = 0
            normalized_stops.append(item)
        out["en_route_stops"] = normalized_stops
        if not out.get("route_summary") and out.get("drive_time"):
            out["route_summary"] = f"Arrival leg into {dest_name} typically takes about {out.get('drive_time')}."
        return out

    def _normalize_environment(self, environment: Any, dates: str, dest: dict[str, Any]) -> Any:
        if not isinstance(environment, dict):
            return environment

        month = self._extract_month_index(dates)
        if month is None:
            return environment

        normals = self._get_monthly_temperature_normals(dest.get("lat"), dest.get("lng"), month)
        if not normals:
            return environment

        high_f, low_f = normals
        environment["temperature_high_f"] = high_f
        environment["temperature_low_f"] = low_f

        month_name = datetime(2000, month, 1).strftime("%B")
        grounded_sentence = f"Typical {month_name} temperatures are around {high_f}°F daytime and {low_f}°F overnight."
        summary = str(environment.get("summary", "") or "").strip()

        if summary:
            summary_without_temp = self._remove_temperature_claims(summary)
            environment["summary"] = (
                grounded_sentence if not summary_without_temp
                else f"{grounded_sentence} {summary_without_temp}"
            )
        else:
            environment["summary"] = grounded_sentence

        return environment

    def _get_monthly_temperature_normals(
        self,
        lat: Any,
        lng: Any,
        month: int,
    ) -> tuple[int, int] | None:
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError):
            return None

        key = (round(lat_f, 2), round(lng_f, 2), month)
        if key in self._weather_cache:
            return self._weather_cache[key]

        try:
            resp = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude": lat_f,
                    "longitude": lng_f,
                    "start_date": "2014-01-01",
                    "end_date": "2023-12-31",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "temperature_unit": "fahrenheit",
                    "timezone": "UTC",
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json().get("daily", {})
            times = data.get("time", [])
            max_vals = data.get("temperature_2m_max", [])
            min_vals = data.get("temperature_2m_min", [])

            month_max: list[float] = []
            month_min: list[float] = []
            for day, max_v, min_v in zip(times, max_vals, min_vals):
                if not day or max_v is None or min_v is None:
                    continue
                if int(day[5:7]) != month:
                    continue
                month_max.append(float(max_v))
                month_min.append(float(min_v))

            if not month_max or not month_min:
                self._weather_cache[key] = None
                return None

            result = (round(sum(month_max) / len(month_max)), round(sum(month_min) / len(month_min)))
            self._weather_cache[key] = result
            return result
        except Exception as exc:
            logger.warning("Weather normals lookup failed for %.3f, %.3f month=%s: %s", lat_f, lng_f, month, exc)
            self._weather_cache[key] = None
            return None

    def _extract_month_index(self, dates: str) -> int | None:
        month_lookup = {
            "january": 1,
            "february": 2,
            "march": 3,
            "april": 4,
            "may": 5,
            "june": 6,
            "july": 7,
            "august": 8,
            "september": 9,
            "october": 10,
            "november": 11,
            "december": 12,
        }
        for token in re.findall(r"[A-Za-z]+", dates or ""):
            idx = month_lookup.get(token.lower())
            if idx:
                return idx
        return None

    def _remove_temperature_claims(self, text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        temp_pattern = re.compile(
            r"(°\s*[FC]|fahrenheit|celsius|temperature|temperatures|\bhighs?\b|\blows?\b|\b\d+\s*[-–]\s*\d+\s*°?F\b)",
            flags=re.IGNORECASE,
        )
        kept = [s.strip() for s in sentences if s.strip() and not temp_pattern.search(s)]
        return " ".join(kept).strip()

    def _normalize_attractions(self, attractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        must_see_budget = 2
        difficulty_rank = {"strenuous": 0, "moderate": 1, "easy": 2, "n/a": 3, "": 4}
        for attraction in attractions:
            item = dict(attraction)
            item_type = str(item.get("type", "attraction") or "attraction").lower()
            item["type"] = item_type
            item["url_candidates"] = self._normalize_url_candidates(item.get("url_candidates", []))
            if item.get("must_see") and must_see_budget > 0:
                must_see_budget -= 1
                item["must_see"] = True
            else:
                item["must_see"] = False
            normalized.append(item)

        normalized = self._dedupe_attractions(normalized)

        # Keep genuinely highlighted items first, then order by challenge level.
        normalized.sort(
            key=lambda x: (
                0 if x.get("must_see") else 1,
                difficulty_rank.get(str(x.get("difficulty", "")).lower(), 4),
                str(x.get("name", "")).lower(),
            )
        )
        return normalized

    def _dedupe_attractions(self, attractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def canonical(name: str) -> str:
            n = (name or "").lower()
            n = n.replace("kolb", "kolob")
            n = re.sub(r"\b(road|rd|trail|hike|loop|route|area|point)\b", " ", n)
            n = re.sub(r"\s+", " ", n).strip()
            return n

        deduped: list[dict[str, Any]] = []
        for item in attractions:
            name = str(item.get("name", "") or "")
            key = canonical(name)
            merged = False
            for existing in deduped:
                existing_key = canonical(str(existing.get("name", "") or ""))
                if not key or not existing_key:
                    continue
                sim = SequenceMatcher(None, key, existing_key).ratio()
                if key == existing_key or sim >= 0.92:
                    if len(str(item.get("description", ""))) > len(str(existing.get("description", ""))):
                        existing["description"] = item.get("description", "")
                    if item.get("must_see"):
                        existing["must_see"] = True
                    if not existing.get("duration") and item.get("duration"):
                        existing["duration"] = item.get("duration")
                    if not existing.get("practical_note") and item.get("practical_note"):
                        existing["practical_note"] = item.get("practical_note")
                    merged = True
                    break
            if not merged:
                deduped.append(item)
        return deduped

    def _normalize_schedule(
        self,
        schedule: Any,
        restaurants: list[dict[str, Any]],
        dates: str,
        getting_here: dict[str, Any],
        previous_destination: str,
        next_destination: str,
        trip_origin: str = "",
        trip_return: str = "",
    ) -> list[dict[str, Any]]:
        restaurant_names = [r.get("name", "") for r in restaurants if r.get("name")]

        def clean_text(text: str) -> str:
            cleaned = str(text)
            cleaned = re.sub(r"^\s*[🌅☀️🌙🗺️]\s*", "", cleaned)
            cleaned = re.sub(r"^\s*(morning|afternoon|evening|plan)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"^\s*\d{1,2}:\d{2}\s*(?:am|pm)?\s*[—-]\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if not cleaned:
                return ""
            if "dinner" in cleaned.lower() and restaurant_names:
                mentions_restaurant = any(name.lower() in cleaned.lower() for name in restaurant_names)
                if not mentions_restaurant:
                    cleaned = re.sub(
                        r"dinner[^.]*",
                        f"dinner at {restaurant_names[0]}",
                        cleaned,
                        count=1,
                        flags=re.IGNORECASE,
                    )
            return cleaned

        if isinstance(schedule, list):
            normalized_days: list[dict[str, Any]] = []
            for index, day in enumerate(schedule, start=1):
                if isinstance(day, dict) and day.get("periods"):
                    periods = []
                    for period in day.get("periods", []):
                        label = str(period.get("period", "")).title()
                        summary = clean_text(period.get("summary", ""))
                        if label and summary:
                            periods.append({"period": label, "summary": summary})
                    if periods:
                        normalized_days.append({
                            "day_label": day.get("day_label") or f"Day {index}",
                            "periods": periods,
                        })
                elif isinstance(day, str):
                    normalized_days.append({
                        "day_label": f"Day {index}",
                        "periods": [{"period": "Plan", "summary": clean_text(day)}],
                    })
            day_count = self._infer_day_count(dates)
            normalized_days = self._expand_days(normalized_days, day_count)
            normalized_days = self._ensure_day_period_coverage(normalized_days, restaurant_names)
            return self._inject_travel_realism(
                normalized_days,
                getting_here,
                previous_destination,
                next_destination,
                trip_origin,
                trip_return,
            )

        if isinstance(schedule, dict):
            periods = []
            for key in ["morning", "afternoon", "evening"]:
                value = clean_text(schedule.get(key, ""))
                if value:
                    periods.append({"period": key.title(), "summary": value})
            if periods:
                expanded = self._expand_days([{"day_label": "Day 1", "periods": periods}], self._infer_day_count(dates))
                expanded = self._ensure_day_period_coverage(expanded, restaurant_names)
                return self._inject_travel_realism(
                    expanded,
                    getting_here,
                    previous_destination,
                    next_destination,
                    trip_origin,
                    trip_return,
                )

        return []

    def _ensure_day_period_coverage(
        self,
        days: list[dict[str, Any]],
        restaurant_names: list[str],
    ) -> list[dict[str, Any]]:
        if not days:
            return days

        required = ["Morning", "Afternoon", "Evening"]
        fallback_by_period: dict[str, str] = {}
        for day in days:
            for period in day.get("periods", []) or []:
                label = str(period.get("period", "")).title()
                summary = str(period.get("summary", "") or "").strip()
                if label in required and summary and label not in fallback_by_period:
                    fallback_by_period[label] = summary

        dinner_name = restaurant_names[0] if restaurant_names else "a listed local restaurant"
        generic = {
            "Morning": "Start early at a priority attraction to avoid midday crowds and parking pressure.",
            "Afternoon": "Continue with a second major stop and keep transition time for parking and trailhead logistics.",
            "Evening": f"Wrap with sunset viewpoints, then dinner at {dinner_name}.",
        }

        out: list[dict[str, Any]] = []
        for idx, day in enumerate(days, start=1):
            existing: dict[str, str] = {}
            for period in day.get("periods", []) or []:
                label = str(period.get("period", "")).title()
                summary = str(period.get("summary", "") or "").strip()
                if label in required and summary:
                    existing[label] = summary

            completed_periods: list[dict[str, str]] = []
            for label in required:
                summary = existing.get(label) or fallback_by_period.get(label) or generic[label]
                completed_periods.append({"period": label, "summary": summary})

            out.append({
                "day_label": day.get("day_label") or f"Day {idx}",
                "periods": completed_periods,
            })

        return self._dedupe_schedule_day_content(out)

    def _dedupe_schedule_day_content(self, days: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ensure each day has at least one meaningful content difference from prior days."""
        if len(days) <= 1:
            return days

        period_variation_suffix = {
            "Morning": "Prioritize a different trailhead or district than previous days.",
            "Afternoon": "Shift focus to a different area to avoid repeating the same loop.",
            "Evening": "Choose a different sunset zone or dining pocket than earlier nights.",
        }

        seen_summaries: set[str] = set()
        for day in days:
            periods = day.get("periods", []) or []
            normalized_day_summaries = [
                str(p.get("summary", "") or "").strip().lower()
                for p in periods
                if str(p.get("summary", "") or "").strip()
            ]

            # Day is too repetitive if every summary is already seen.
            if normalized_day_summaries and all(s in seen_summaries for s in normalized_day_summaries):
                for period in periods:
                    label = str(period.get("period", "")).title()
                    summary = str(period.get("summary", "") or "").strip()
                    if not summary:
                        continue
                    suffix = period_variation_suffix.get(label, "Vary stops and pacing from previous days.")
                    if suffix.lower() not in summary.lower():
                        period["summary"] = f"{summary} {suffix}".strip()
                    break

            for period in periods:
                summary = str(period.get("summary", "") or "").strip().lower()
                if summary:
                    seen_summaries.add(summary)

        return days

    def _inject_travel_realism(
        self,
        days: list[dict[str, Any]],
        getting_here: dict[str, Any],
        previous_destination: str,
        next_destination: str,
        trip_origin: str = "",
        trip_return: str = "",
    ) -> list[dict[str, Any]]:
        if not days:
            return days

        is_first_destination = str(previous_destination or "").strip().lower() in {"", "none"}
        is_last_destination = not str(next_destination or "").strip()

        def _set_period_summary(day: dict[str, Any], label: str, summary: str) -> None:
            periods = day.get("periods", []) or []
            for period in periods:
                if str(period.get("period", "")).title() == label:
                    period["summary"] = summary
                    return

        if is_first_destination:
            origin_label = trip_origin or "trip origin"
            _set_period_summary(
                days[0],
                "Morning",
                f"Travel from {origin_label}.",
            )

        drive_time = str(getting_here.get("drive_time", "") or "").strip()
        first = days[0]
        first_periods = first.get("periods", [])
        if first_periods and not is_first_destination and (drive_time or previous_destination.lower() != "none"):
            existing = str(first_periods[0].get("summary", "") or "")
            if re.search(r"\b(arrive|arrival|drive|driving|route|i-\d+|us-\d+)\b", existing, re.IGNORECASE):
                arrival_note = ""
            else:
                arrival_note = f"Travel from {previous_destination}"
            if arrival_note:
                first_periods[0]["summary"] = f"{arrival_note}. {existing}".strip()

        if is_last_destination:
            return_label = trip_return or "base"
            last = days[-1]
            _set_period_summary(
                last,
                "Afternoon",
                f"Reserved for return travel to {return_label}; begin checkout and departure logistics.",
            )
            _set_period_summary(
                last,
                "Evening",
                f"Reserved for return travel to {return_label}; plan buffer time for traffic, stops, and arrival.",
            )
        elif len(days) > 1 and next_destination:
            last = days[-1]
            last_periods = last.get("periods", [])
            if last_periods:
                last_periods[-1]["summary"] = (
                    f"Wrap key stops early and prepare for onward drive to {next_destination}. "
                    f"{last_periods[-1].get('summary', '')}"
                ).strip()
        return days

    def _infer_day_count(self, dates: str) -> int:
        text = (dates or "").replace("–", "-")
        # "October 17-21, 2026" or "October 17, 2026"
        m = re.search(r"[A-Za-z]+\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?(?:,\s*\d{4})?", text)
        if m:
            start = int(m.group(1))
            end = int(m.group(2) or m.group(1))
            if end >= start:
                return max(1, min(5, end - start + 1))
            return 1
        # ISO range fallback: 2026-10-17 to 2026-10-21
        iso = re.findall(r"(\d{4}-\d{2}-\d{2})", text)
        if len(iso) >= 2:
            try:
                from datetime import datetime as _dt
                d0 = _dt.strptime(iso[0], "%Y-%m-%d")
                d1 = _dt.strptime(iso[1], "%Y-%m-%d")
                if d1 >= d0:
                    return max(1, min(5, (d1 - d0).days + 1))
            except ValueError:
                return 1
        return 1

    def _expand_days(self, days: list[dict[str, Any]], day_count: int) -> list[dict[str, Any]]:
        if not days:
            return days
        if day_count <= 1:
            return days[:1]
        if len(days) > day_count:
            return days[:day_count]
        if day_count <= 1:
            return days
        if len(days) >= day_count:
            return days

        base_periods = days[0].get("periods", [])
        if not base_periods:
            return days

        expanded = []
        for idx in range(day_count):
            if idx < len(days):
                expanded.append(days[idx])
                continue
            # Spread existing period ideas across additional days with lightweight variation.
            period_template = base_periods[idx % len(base_periods)]
            expanded.append({
                "day_label": f"Day {idx + 1}",
                "periods": [{
                    "period": period_template.get("period", "Plan"),
                    "summary": period_template.get("summary", "Continue with priority attractions and logistics.")
                }],
            })
        return expanded

    def _normalize_restaurants(self, restaurants: list[dict[str, Any]], budget: Any = None) -> list[dict[str, Any]]:
        normalized = []
        price_rank = {"$": 0, "$$": 1, "$$$": 2, "$$$$": 3}
        budget_text = str(budget or "").lower()
        low_budget = any(k in budget_text for k in ["budget", "cheap", "economy", "value", "frugal"])
        high_budget = any(k in budget_text for k in ["luxury", "premium", "high", "splurge", "upscale"])

        for restaurant in restaurants:
            item = dict(restaurant)
            item["price_range"] = item.get("price_range") or item.get("price") or ""
            item["url_candidates"] = self._normalize_url_candidates(item.get("url_candidates", []))
            name = str(item.get("name", "") or "")
            cuisine = str(item.get("cuisine", "") or "")
            description = str(item.get("description", "") or "")
            if self._is_chain_or_fast_food(name, cuisine, description):
                continue

            tier = str(item.get("price_range", "")).strip()

            if low_budget and tier in {"$$$", "$$$$"}:
                # Keep at most one splurge option for low-budget trips.
                if any(str(r.get("price_range", "")).strip() in {"$$$", "$$$$"} for r in normalized):
                    continue

            if high_budget and tier in {"$", "$$"}:
                # Keep at most one casual option for high-budget trips.
                if any(str(r.get("price_range", "")).strip() in {"$", "$$"} for r in normalized):
                    continue

            normalized.append(item)

        # Sort from inexpensive to expensive and keep cuisine variety visible.
        normalized.sort(
            key=lambda r: (
                price_rank.get(str(r.get("price_range", "")).strip(), 99),
                str(r.get("cuisine", "")).lower(),
                str(r.get("name", "")).lower(),
            )
        )
        return normalized

    @staticmethod
    def _normalize_url_candidates(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            candidate = str(item or "").strip()
            if not candidate:
                continue
            lower = candidate.lower()
            if lower.startswith("www."):
                candidate = "https://" + candidate
                lower = candidate.lower()
            if not lower.startswith(("http://", "https://")):
                continue
            if lower in seen:
                continue
            seen.add(lower)
            out.append(candidate)
            if len(out) >= 3:
                break
        return out

    def _augment_with_url_candidates(self, payload: dict[str, Any], dest: dict[str, Any]) -> dict[str, Any]:
        attractions = payload.get("top_attractions", []) if isinstance(payload.get("top_attractions", []), list) else []
        restaurants = payload.get("dinner_recommendations", []) if isinstance(payload.get("dinner_recommendations", []), list) else []
        if not attractions and not restaurants:
            return payload

        attraction_lines: list[str] = []
        for idx, attraction in enumerate(attractions, start=1):
            if not isinstance(attraction, dict):
                continue
            name = str(attraction.get("name", "") or "").strip()
            if not name:
                continue
            item_type = str(attraction.get("type", "attraction") or "attraction").strip()
            desc = str(attraction.get("description", "") or "").strip()
            attraction_lines.append(f"{idx}. {name} | type={item_type} | {desc}")

        restaurant_lines: list[str] = []
        for idx, restaurant in enumerate(restaurants, start=1):
            if not isinstance(restaurant, dict):
                continue
            name = str(restaurant.get("name", "") or "").strip()
            if not name:
                continue
            cuisine = str(restaurant.get("cuisine", "") or "").strip()
            restaurant_lines.append(f"{idx}. {name} | cuisine={cuisine}")

        if not attraction_lines and not restaurant_lines:
            return payload

        user_prompt = (
            "Suggest URL candidates for these destination items.\n"
            f"Destination: {dest.get('name', '')}\n"
            "Rules:\n"
            "- Return JSON only with exact keys: attraction_url_candidates, restaurant_url_candidates.\n"
            "- Each item: {name: string, url_candidates: [up to 3 absolute URLs]}.\n"
            "- Use canonical, directly relevant URLs only.\n"
            "- For hike/trail-like attractions, prefer alltrails.com/trail/... URLs.\n"
            "- For restaurants, prefer official site or google maps place/search or tripadvisor.\n"
            "- If uncertain, return an empty url_candidates array.\n\n"
            "Attractions:\n"
            + ("\n".join(attraction_lines) if attraction_lines else "(none)")
            + "\n\nRestaurants:\n"
            + ("\n".join(restaurant_lines) if restaurant_lines else "(none)")
        )
        system_prompt = (
            "You produce structured URL candidate lists for known place names. "
            "Return valid JSON only, no markdown. Do not include explanatory text."
        )

        try:
            candidate_payload = self._llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                operation=f"url_candidates:{dest.get('id', dest.get('name', 'destination'))}",
                temperature=0.1,
                max_tokens=1800,
            )
        except Exception as exc:
            logger.warning("URL candidate augmentation failed for '%s': %s", dest.get("name", ""), exc)
            return payload

        if not isinstance(candidate_payload, dict):
            return payload

        attr_map: dict[str, list[str]] = {}
        for row in candidate_payload.get("attraction_url_candidates", []) or []:
            if not isinstance(row, dict):
                continue
            key = self._canonical_seed_name(str(row.get("name", "") or ""))
            if not key:
                continue
            attr_map[key] = self._normalize_url_candidates(row.get("url_candidates", []))

        rest_map: dict[str, list[str]] = {}
        for row in candidate_payload.get("restaurant_url_candidates", []) or []:
            if not isinstance(row, dict):
                continue
            key = self._canonical_seed_name(str(row.get("name", "") or ""))
            if not key:
                continue
            rest_map[key] = self._normalize_url_candidates(row.get("url_candidates", []))

        for attraction in attractions:
            if not isinstance(attraction, dict):
                continue
            key = self._canonical_seed_name(str(attraction.get("name", "") or ""))
            if key in attr_map and attr_map[key]:
                attraction["url_candidates"] = attr_map[key]

        for restaurant in restaurants:
            if not isinstance(restaurant, dict):
                continue
            key = self._canonical_seed_name(str(restaurant.get("name", "") or ""))
            if key in rest_map and rest_map[key]:
                restaurant["url_candidates"] = rest_map[key]

        payload["top_attractions"] = attractions
        payload["dinner_recommendations"] = restaurants
        return payload

    def _is_chain_or_fast_food(self, name: str, cuisine: str, description: str) -> bool:
        hay = " ".join([name, cuisine, description]).lower()
        if any(token in hay for token in self._FAST_FOOD_TOKENS):
            return True
        if any(token in hay for token in self._CHAIN_NAME_TOKENS):
            return True
        return False
