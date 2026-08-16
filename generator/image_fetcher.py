"""
image_fetcher.py — Fetch destination images from NPS API and Wikimedia Commons.

Strategy:
  1. NPS API first for national parks (requires nps_park_code)
  2. Wikimedia MediaSearch for all destinations
  3. Automatic fallback query sequence (up to 4 attempts) on failure
    4. Warn (do not hard fail) if < min_per_destination verified images found

Images are embedded as data URIs in the HTML (base64) OR stored as
relative paths in output/images/ depending on config.
"""
from __future__ import annotations
import html as html_lib
import hashlib, json, logging, os, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
from typing import Any
import requests

logger = logging.getLogger(__name__)
NPS_API_BASE = "https://developer.nps.gov/api/v1"
WIKIMEDIA_SEARCH = "https://commons.wikimedia.org/w/api.php"
THUMB_WIDTH = 960
MAX_FALLBACK_ATTEMPTS = 4
REQUEST_DELAY = 1.5
NOISY_CAPTION_MARKERS = (
    "when reusing",
    "please credit me",
    "contact me at",
    "layouttemplate",
    "external text",
    "mw-file",
    "cc-by-sa",
    "wikimedia.org/wiki/file",
    "info icon",
)
MARINE_HARD_REJECT_TERMS = {
    "coral",
    "underwater",
    "ocean",
    "sea",
    "scuba",
    "snorkel",
    "tropical",
    "marine",
    "reef fish",
    "anemone",
    "stingray",
    "manta",
    "jellyfish",
    "sea turtle",
    "kelp",
    "scuba diving",
}
GLOBAL_IMAGE_BLACKLIST_TERMS = {
    "underwater",
    "scuba",
    "snorkel",
    "snorkeling",
}


class ImageFetcher:
    _COUNTER_LOCK = threading.Lock()
    _COUNTERS: dict[str, int] = {
        "nps_api_calls": 0,
        "wikimedia_api_calls": 0,
        "unsplash_api_calls": 0,
        "image_download_requests": 0,
        "cache_hits": 0,
    }

    def __init__(
        self,
        config_path: str | Path = "config.yaml",
        output_dir: str | Path | None = None,
        force_refresh: bool = False,
    ) -> None:
        import yaml
        with Path(config_path).open() as f:
            cfg = yaml.safe_load(f)
        self._nps_key = os.environ.get("NPS_API_KEY", "DEMO_KEY")
        images_cfg = cfg.get("images", {})
        self._min_per_dest = images_cfg.get("min_per_destination", 2)
        self._max_per_dest = images_cfg.get("max_per_destination", 4)
        self._cache_ttl_seconds = int(images_cfg.get("cache_ttl_hours", 168)) * 3600
        self._force_refresh = force_refresh
        raw_blacklist = images_cfg.get("never_content_terms", []) or []
        self._global_blacklist_terms = {
            str(term).strip().lower()
            for term in raw_blacklist
            if str(term).strip()
        } or set(GLOBAL_IMAGE_BLACKLIST_TERMS)
        if output_dir is None:
            self._output_dir = Path("output/images")
        else:
            self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = Path(".cache/images")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_index_path = self._cache_dir / "cache_index.json"
        self._cache_lock = threading.Lock()
        self._cache_index = self._load_cache_index()
        self._session_local = threading.local()

    @classmethod
    def _increment_counter(cls, key: str) -> None:
        with cls._COUNTER_LOCK:
            cls._COUNTERS[key] = int(cls._COUNTERS.get(key, 0) or 0) + 1

    @classmethod
    def snapshot_counters(cls) -> dict[str, int]:
        with cls._COUNTER_LOCK:
            return {k: int(v or 0) for k, v in cls._COUNTERS.items()}

    def _get_session(self) -> requests.Session:
        if not hasattr(self._session_local, "session"):
            s = requests.Session()
            s.headers.update({"User-Agent": "RoadTripItineraryGenerator/1.0"})
            self._session_local.session = s
        return self._session_local.session

    # ── Public entry point ───────────────────────────────────────────────────

    def fetch_all(self, trip: dict[str, Any]) -> None:
        destinations = trip.get("destinations", [])

        def _fetch_one(dest: dict) -> None:
            logger.info("Fetching images for '%s'…", dest["name"])
            imgs = self._fetch_for_dest(dest)
            dest["images"] = imgs
            if len(imgs) < self._min_per_dest:
                logger.warning(
                    "  Image shortfall for '%s': only %d image(s) verified (min: %d)",
                    dest.get("name", ""),
                    len(imgs),
                    self._min_per_dest,
                )
            logger.info("  %d image(s) acquired for '%s'", len(imgs), dest["name"])

        with ThreadPoolExecutor(max_workers=min(len(destinations), 4)) as pool:
            futures = {pool.submit(_fetch_one, d): d for d in destinations}
            for f in as_completed(futures):
                f.result()

    # ── Per-destination fetch ────────────────────────────────────────────────

    def _fetch_for_dest(self, dest: dict[str, Any]) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        dest_name = str(dest.get("name", "") or "")
        query_name = self._provider_query_for_destination(dest_name)
        cache_key = self._cache_key(dest)

        if not self._force_refresh:
            cached_images = self._get_cached_images(cache_key)
            if cached_images:
                self._increment_counter("cache_hits")
                logger.info("  Reusing cached image candidates for '%s'", dest_name)
                verified_cached = self._verify_and_materialize(cached_images, dest_name)
                if len(verified_cached) >= self._min_per_dest:
                    return verified_cached[:self._max_per_dest]
                images.extend(cached_images)

        # Source 1: NPS API
        if dest.get("nps_park_code"):
            images.extend(self._fetch_from_nps(dest["nps_park_code"]))

    # Source 2: Unsplash (preferred over Wikimedia)
        if len(images) < self._max_per_dest:
            remaining = self._max_per_dest - len(images)
            images.extend(self._fetch_from_unsplash(query_name, limit=remaining))

    # Source 3: Wikimedia (fallback)
        if len(images) < self._max_per_dest:
            remaining = self._max_per_dest - len(images)
            images.extend(self._fetch_from_wikimedia(query_name, limit=remaining + 2))

        images = self._rank_images_for_destination(images, dest_name)
        verified = self._verify_and_materialize(images, dest_name)

        # Fallback queries if still short
        attempt = 0
        fallback_queries = self._fallback_queries(query_name)
        while len(verified) < self._min_per_dest and attempt < MAX_FALLBACK_ATTEMPTS:
            query = fallback_queries[attempt % len(fallback_queries)]
            logger.warning("  Image fallback attempt %d for '%s': '%s'", attempt + 1, dest["name"], query)
            extra = self._fetch_from_wikimedia(query, limit=4)
            extra = self._rank_images_for_destination(extra, dest_name)
            verified = self._verify_and_materialize(verified + extra, dest_name)
            attempt += 1

        if verified:
            self._set_cached_images(cache_key, verified)

        return verified[:self._max_per_dest]

    def _verify_and_materialize(self, images: list[dict[str, Any]], dest_name: str) -> list[dict[str, Any]]:
        ranked = self._rank_images_for_destination(images, dest_name)
        verified: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for img in ranked:
            url = str(img.get("url", "") or "").strip()
            if not url or url in seen_urls:
                continue
            local_path = self._download_image(url)
            if not local_path:
                continue
            item = self._normalize_image_record(dict(img))
            item["local_path"] = str(local_path)
            verified.append(item)
            seen_urls.add(url)
            if len(verified) >= self._max_per_dest:
                break
        return verified

    def _cache_key(self, dest: dict[str, Any]) -> str:
        name = str(dest.get("name", "") or "").strip().lower()
        name = re.sub(r"\s+", " ", name)
        nps = str(dest.get("nps_park_code", "") or "none").strip().lower()
        return f"v2::{name}::{nps}"

    def _load_cache_index(self) -> dict[str, Any]:
        if not self._cache_index_path.exists():
            return {"version": 1, "entries": {}}
        try:
            payload = json.loads(self._cache_index_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {"version": 1, "entries": {}}
            payload.setdefault("version", 1)
            payload.setdefault("entries", {})
            if not isinstance(payload["entries"], dict):
                payload["entries"] = {}
            return payload
        except Exception:
            logger.warning("Image cache index unreadable; rebuilding: %s", self._cache_index_path)
            return {"version": 1, "entries": {}}

    def _save_cache_index(self) -> None:
        tmp = self._cache_index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache_index, indent=2), encoding="utf-8")
        tmp.replace(self._cache_index_path)

    def _get_cached_images(self, cache_key: str) -> list[dict[str, Any]]:
        with self._cache_lock:
            entry = self._cache_index.get("entries", {}).get(cache_key)
        if not entry or not isinstance(entry, dict):
            return []
        updated_at = float(entry.get("updated_at", 0) or 0)
        if time.time() - updated_at > self._cache_ttl_seconds:
            return []
        images = entry.get("images", [])
        if not isinstance(images, list):
            return []
        out: list[dict[str, Any]] = []
        for item in images:
            if not isinstance(item, dict):
                continue
            if not item.get("url"):
                continue
            out.append(dict(item))
        return out

    def _set_cached_images(self, cache_key: str, images: list[dict[str, Any]]) -> None:
        slim: list[dict[str, Any]] = []
        for img in images:
            if not isinstance(img, dict):
                continue
            cleaned = self._normalize_image_record(img)
            url = str(cleaned.get("url", "") or "").strip()
            if not url:
                continue
            slim.append(
                {
                    "url": url,
                    "title": str(cleaned.get("title", "") or ""),
                    "credit": str(cleaned.get("credit", "") or ""),
                    "license": str(cleaned.get("license", "") or ""),
                    "source": str(cleaned.get("source", "") or ""),
                }
            )
        if not slim:
            return
        with self._cache_lock:
            self._cache_index.setdefault("entries", {})[cache_key] = {
                "updated_at": time.time(),
                "images": slim,
            }
            self._save_cache_index()

    def _rank_images_for_destination(self, images: list[dict[str, Any]], destination: str) -> list[dict[str, Any]]:
        tokens = self._location_tokens(destination)
        if not tokens:
            return images

        profile = self._destination_image_profile(destination)

        def score(img: dict[str, Any]) -> int:
            hay = " ".join(
                [
                    str(img.get("title", "") or ""),
                    str(img.get("credit", "") or ""),
                    str(img.get("url", "") or ""),
                ]
            ).lower()
            base = sum(1 for t in tokens if t in hay)

            # Strongly penalize context mismatch (e.g., coral/ocean photos for desert parks).
            neg = sum(1 for t in profile["negative"] if t in hay)
            pos = sum(1 for t in profile["positive"] if t in hay)

            return base + (2 * pos) - (3 * neg)

        def neg_hits(img: dict[str, Any]) -> int:
            hay = " ".join(
                [
                    str(img.get("title", "") or ""),
                    str(img.get("credit", "") or ""),
                    str(img.get("url", "") or ""),
                ]
            ).lower()
            return sum(1 for t in profile["negative"] if t in hay)

        def has_hard_marine_mismatch(img: dict[str, Any]) -> bool:
            hay = " ".join(
                [
                    str(img.get("title", "") or ""),
                    str(img.get("credit", "") or ""),
                    str(img.get("url", "") or ""),
                ]
            ).lower()
            if not any(cue in destination.lower() for cue in ("national park", "state park", "desert", "canyon", "utah", "arizona", "nevada", "new mexico", "colorado")):
                return False
            return any(term in hay for term in MARINE_HARD_REJECT_TERMS)

        def has_global_blacklist_hit(img: dict[str, Any]) -> bool:
            hay = " ".join(
                [
                    str(img.get("title", "") or ""),
                    str(img.get("credit", "") or ""),
                    str(img.get("url", "") or ""),
                ]
            ).lower()
            blocked = getattr(self, "_global_blacklist_terms", set(GLOBAL_IMAGE_BLACKLIST_TERMS))
            return any(term in hay for term in blocked)

        blacklist_filtered = [img for img in images if not has_global_blacklist_hit(img)]
        if blacklist_filtered:
            images = blacklist_filtered
        elif images:
            return []

        mismatch_flags = [has_hard_marine_mismatch(img) for img in images]
        filtered = [img for img, bad in zip(images, mismatch_flags) if not bad]
        if filtered:
            images = filtered
        elif images and any(mismatch_flags):
            # All candidates are hard marine mismatches for an inland/desert context.
            return []

        required_any = profile.get("required_any", set())
        if required_any:
            required_hits = []
            for img in images:
                hay = " ".join(
                    [
                        str(img.get("title", "") or ""),
                        str(img.get("credit", "") or ""),
                        str(img.get("url", "") or ""),
                    ]
                ).lower()
                if any(term in hay for term in required_any):
                    required_hits.append(img)
            if required_hits:
                images = required_hits

        scored = sorted(images, key=score, reverse=True)
        non_negative = [img for img in scored if neg_hits(img) == 0]
        if non_negative:
            scored = non_negative
        # Keep only relevant images when possible; if none score above zero, keep original order.
        positive = [img for img in scored if score(img) > 0]
        return positive if positive else scored

    @staticmethod
    def _destination_image_profile(destination: str) -> dict[str, set[str]]:
        d = (destination or "").lower()

        positive: set[str] = {
            "landscape",
            "mountain",
            "canyon",
            "plateau",
            "mesa",
            "desert",
            "trail",
            "hiking",
            "sandstone",
            "cliff",
            "rock",
            "national park",
        }
        negative: set[str] = set()
        required_any: set[str] = set()

        # For inland and canyon/desert contexts, marine imagery is usually a mismatch.
        inland_cues = (
            "national park",
            "state park",
            "desert",
            "canyon",
            "mesa",
            "plateau",
            "utah",
            "arizona",
            "nevada",
            "new mexico",
            "colorado",
        )
        if any(cue in d for cue in inland_cues):
            negative.update(
                {
                    "coral",
                    "underwater",
                    "scuba",
                    "snorkel",
                    "snorkeling",
                    "ocean",
                    "sea",
                    "tropical",
                    "reef fish",
                    "marine",
                    "wildlife",
                    "bird",
                    "rodent",
                    "marmot",
                    "chipmunk",
                    "squirrel",
                    "weasel",
                    "animal portrait",
                }
            )

        # Specific guard for Capitol Reef ambiguity with ocean reef photos.
        if "capitol reef" in d or "capital reef" in d:
            negative.update({"coral", "underwater", "ocean", "sea", "scuba", "snorkel"})
            positive.update({"capitol reef", "waterpocket fold", "utah", "sandstone"})
            # Disambiguate from marine "reef" imagery when metadata is sparse.
            required_any.update({"capitol", "capital", "utah", "waterpocket", "sandstone", "canyon", "national park"})

        return {"positive": positive, "negative": negative, "required_any": required_any}

    @staticmethod
    def _location_tokens(destination: str) -> list[str]:
        parts = re.findall(r"[a-z0-9]+", (destination or "").lower())
        stop = {"national", "park", "state", "the", "and", "city"}
        tokens = [p for p in parts if len(p) >= 4 and p not in stop]
        # "reef" is highly ambiguous and over-matches marine photos for Capitol Reef.
        if ("capitol" in parts or "capital" in parts) and "reef" in tokens:
            tokens = [t for t in tokens if t != "reef"]
        # Add canonical typo resilience for common park names.
        expanded = set(tokens)
        if "kolob" in expanded:
            expanded.add("kolb")
        return sorted(expanded)

    # ── NPS images ───────────────────────────────────────────────────────────

    def _fetch_from_nps(self, park_code: str) -> list[dict[str, Any]]:
        try:
            self._increment_counter("nps_api_calls")
            resp = self._get_session().get(
                f"{NPS_API_BASE}/multimedia/galleries/assets",
                params={"parkCode": park_code, "limit": 6},
                headers={"X-Api-Key": self._nps_key},
                timeout=10,
            )
            resp.raise_for_status()
            results = []
            for item in resp.json().get("data", []):
                url = item.get("fileInfo", {}).get("url", "")
                if url:
                    results.append(self._normalize_image_record({
                        "url": url,
                        "title": item.get("title", ""),
                        "credit": item.get("credit", "National Park Service"),
                        "license": "Public Domain / NPS",
                        "source": "nps",
                    }))
            return results
        except requests.RequestException as exc:
            logger.warning("NPS image API error for '%s': %s", park_code, exc)
            return []

    # ── Wikimedia images ─────────────────────────────────────────────────────

    def _fetch_from_wikimedia(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        try:
            self._increment_counter("wikimedia_api_calls")
            resp = self._get_session().get(
                WIKIMEDIA_SEARCH,
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrnamespace": 6,
                    "gsrsearch": f"filetype:bitmap {query}",
                    "gsrlimit": limit * 3,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata|size|mime",
                    "iiurlwidth": THUMB_WIDTH,
                    "format": "json",
                },
                timeout=10,
            )
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            results = []
            for page in pages.values():
                info = page.get("imageinfo", [{}])[0]
                url = info.get("thumburl") or info.get("url", "")
                if not url:
                    continue
                mime = info.get("mime", "image/jpeg")
                if not mime.startswith("image/"):
                    continue
                meta = info.get("extmetadata", {})
                results.append(self._normalize_image_record({
                    "url": url,
                    "title": page.get("title", "").replace("File:", ""),
                    "credit": meta.get("Artist", {}).get("value", "Wikimedia Commons"),
                    "license": meta.get("LicenseShortName", {}).get("value", "CC BY-SA"),
                    "source": "wikimedia",
                }))
            return results[:limit]
        except requests.RequestException as exc:
            logger.warning("Wikimedia search error for '%s': %s", query, exc)
            return []
        
        # ── Unsplash images ───────────────────────────────────────────────────────

    def _fetch_from_unsplash(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        key = os.environ.get("UNSPLASH_ACCESS_KEY")
        if not key:
            return []

        try:
            self._increment_counter("unsplash_api_calls")
            url = "https://api.unsplash.com/search/photos"
            params = {
                "query": query,
                "per_page": limit,
                "orientation": "landscape",
            }
            headers = {
                "Authorization": f"Client-ID {key}",
                "User-Agent": "RoadTripItineraryGenerator/1.0"
            }

            resp = self._get_session().get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()

            results = []
            for item in resp.json().get("results", []):
                img_url = item.get("urls", {}).get("regular")
                if not img_url:
                    continue

                results.append({
                    "url": img_url,
                    "title": item.get("alt_description") or item.get("description") or "",
                    "credit": item.get("user", {}).get("name", "Unsplash"),
                    "license": "Unsplash License",
                    "source": "unsplash",
                })

            return [self._normalize_image_record(r) for r in results[:limit]]

        except requests.RequestException as exc:
            logger.warning("Unsplash search error for '%s': %s", query, exc)
            return []


    # ── Download to local file ───────────────────────────────────────────────

    def _download_image(self, url: str) -> Path | None:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        ext = self._guess_extension(url)
        local_path = self._output_dir / f"{url_hash}{ext}"
        if local_path.exists():
            return local_path
        try:
            self._increment_counter("image_download_requests")
            resp = self._get_session().get(url, timeout=20, stream=True)
            resp.raise_for_status()
            with local_path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            time.sleep(REQUEST_DELAY)
            return local_path
        except requests.RequestException as exc:
            logger.warning("Image download failed (%s): %s", url[:60], exc)
            return None

    @staticmethod
    def _guess_extension(url: str) -> str:
        path = url.split("?")[0].split("#")[0]
        ext = Path(path).suffix.lower()
        return ext if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"

    @staticmethod
    def _fallback_queries(destination: str) -> list[str]:
        base = destination.split(",")[0].strip()
        return [
            f"{base} landscape",
            f"{base} mountains",
            f"{base} aerial view",
            f"{base} scenic",
        ]

    @staticmethod
    def _provider_query_for_destination(destination: str) -> str:
        name = str(destination or "").strip()
        lower = name.lower()
        if "capitol reef" in lower or "capital reef" in lower:
            return f"{name} Utah national park desert canyon"
        return name

    @staticmethod
    def _sanitize_metadata_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = html_lib.unescape(text)
        text = re.sub(r"\{\{.*?\}\}", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()

        lower = text.lower()
        if any(marker in lower for marker in NOISY_CAPTION_MARKERS):
            return ""

        if len(text) > 200:
            text = text[:197].rstrip() + "..."
        return text

    def _normalize_image_record(self, image: dict[str, Any]) -> dict[str, Any]:
        out = dict(image)
        out["title"] = self._sanitize_metadata_text(out.get("title", ""))
        out["credit"] = self._sanitize_metadata_text(out.get("credit", ""))
        out["license"] = self._sanitize_metadata_text(out.get("license", ""))
        out["source"] = self._sanitize_metadata_text(out.get("source", ""))
        if not out.get("credit"):
            source = str(out.get("source", "") or "").strip().lower()
            if source == "wikimedia":
                out["credit"] = "Wikimedia Commons"
            elif source == "nps":
                out["credit"] = "National Park Service"
            elif source == "unsplash":
                out["credit"] = "Unsplash"
        return out
