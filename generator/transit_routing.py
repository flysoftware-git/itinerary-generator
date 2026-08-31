"""
transit_routing.py — public-transport options for the leg between two stops.

Phase 1 of docs/design/multimodal-routing.md: AI-only, and deliberately
limited to what a language model can actually be right about.

A model cannot know a departure time. Not unreliably -- at all. A timetable
is the same class of fact design.md 1.4 removed from the model's job for
URLs: a plausible 09:00 departure is indistinguishable from a real one until
someone stands at a bus stop. So this module emits corridor descriptions,
duration BANDS and search phrases, and structurally forbids the shape that
carries clock times and booking links.

"Structurally" means `normalize_transit_options` strips them rather than the
prompt asking the model not to produce them -- design.md principle 7, with
the banned-marketing episode as precedent (one real run contained 28
violations of a rule the prompt had stated for months).

The output mirrors cultural_events' discriminated union, which solved the
identical problem for "what's on?":

  Format A -- has_transit: true, 1-3 options, plus a `fallback` line
  Format B -- has_transit: false, an `honest_assessment` and a `local_tip`

Format B is not a degraded answer. On a remote corridor it is the correct
one, and far more useful than an invented Greyhound route.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

#: Cost-ledger prefix. design.md 4.4 records two incidents of spend silently
#: excluded from stage attribution because a prefix was unrecognised, so this
#: is a constant shared with the ledger rather than a literal at the call site.
USAGE_OPERATION_PREFIX = "transit_routing"

#: Providers this factory knows. `google_directions` is Phase 2 and is named
#: here so selecting it fails with an explanation rather than silently
#: falling back to the AI path and reporting `confidence: api_verified` for
#: an answer no API produced.
VALID_TRANSIT_PROVIDERS = ("ai", "google_directions")

#: Fields an option may carry. Everything else the model invents is dropped --
#: an allowlist, so a new hallucinated key cannot ride along by default.
_ALLOWED_OPTION_FIELDS = ("mode", "label", "duration", "transfers", "notes", "booking_hint")

#: Keys dropped from an option unconditionally, whatever they contain. A
#: booking URL from a model is the thing design.md 1.4 exists to prevent, and
#: `depart`/`arrive` are the Shape A datetimes Phase 1 must not emit.
_FORBIDDEN_OPTION_FIELDS = ("url", "booking_url", "link", "website", "depart", "arrive")

#: Anything that looks like a clock time or a date, in any of the shapes a
#: model reaches for. Applied to the free-text fields that survive, because a
#: "duration" of "09:00-12:15" is a timetable wearing a duration's name.
_DATETIME_PATTERN = re.compile(
    r"""
    \d{4}-\d{2}-\d{2}            # 2026-10-14, with or without a T-time
    | \b\d{1,2}:\d{2}\s*(?:am|pm)?\b   # 09:00, 9:00 PM
    | \b\d{1,2}\s*(?:am|pm)\b          # 9am
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Bare-minimum URL sniff for prose fields. Deliberately eager: a stripped
#: half-URL in a notes line is a cosmetic loss, a live one is a promise the
#: project has twice decided the model may not make.
_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

#: The issue asked for up to a handful; three is where a card stops being a
#: recommendation and starts being a search results page.
MAX_OPTIONS = 3


def _clean_prose(value: Any) -> str:
    """Free text with URLs and clock times removed, whitespace tidied."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = _URL_PATTERN.sub("", text)
    text = _DATETIME_PATTERN.sub("", text)
    # Strip the punctuation and spacing left behind by an excision, so
    # "departs 09:00, arrives 12:15" does not render as "departs , arrives".
    text = re.sub(r"\s*([,;:])\s*(?=[,;:]|$)", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,;:-")
    return text


def _clean_option(raw: Any) -> dict[str, Any] | None:
    """One Shape B option, or None if nothing usable survives the strip."""
    if not isinstance(raw, dict):
        return None

    dropped = [k for k in raw if k in _FORBIDDEN_OPTION_FIELDS]
    if dropped:
        logger.info(
            "    transit option: dropped %s — Phase 1 does not emit times or booking links",
            ", ".join(sorted(dropped)),
        )

    option: dict[str, Any] = {}
    for field in _ALLOWED_OPTION_FIELDS:
        if field not in raw:
            continue
        if field == "transfers":
            try:
                transfers = int(raw[field])
            except (TypeError, ValueError):
                continue
            if transfers >= 0:
                option["transfers"] = transfers
            continue
        cleaned = _clean_prose(raw[field])
        if cleaned:
            option[field] = cleaned

    # A label is what the card renders. Without one there is no option, only
    # a duration attached to nothing.
    if not option.get("label"):
        return None
    return option


def normalize_transit_options(
    payload: Any,
    *,
    source: str = "ai",
    confidence: str = "unverified",
) -> dict[str, Any]:
    """Coerce a provider payload into the Format A / Format B union.

    Strips rather than trusts. Anything unparseable degrades to Format B --
    an honest negative -- rather than to an empty card, which reads as "we
    did not look" instead of "there is nothing".

    `source` and `confidence` are not decoration: they are how the renderer
    chooses between a verified card and an unverified one without
    re-deriving the reasoning (design.md 2.2, confidence as a property of
    the path a fact travelled).
    """
    if not isinstance(payload, dict):
        return format_b("No scheduled public transit information is available for this leg.")

    options: list[dict[str, Any]] = []
    for raw in (payload.get("options") or []) if isinstance(payload.get("options"), list) else []:
        cleaned = _clean_option(raw)
        if cleaned:
            options.append(cleaned)
        if len(options) >= MAX_OPTIONS:
            break

    has_transit = bool(payload.get("has_transit")) and bool(options)
    if not has_transit:
        return format_b(
            _clean_prose(payload.get("honest_assessment"))
            or "No scheduled public transit is known to connect these two stops.",
            local_tip=_clean_prose(payload.get("local_tip")),
        )

    result: dict[str, Any] = {
        "has_transit": True,
        "source": source,
        "confidence": confidence,
        "options": options,
    }
    fallback = _clean_prose(payload.get("fallback"))
    if fallback:
        result["fallback"] = fallback
    return result


def format_b(honest_assessment: str, *, local_tip: str = "") -> dict[str, Any]:
    """The honest negative. `source`/`confidence` are omitted deliberately --
    there is no claim here for them to qualify."""
    result: dict[str, Any] = {
        "has_transit": False,
        "honest_assessment": str(honest_assessment or "").strip(),
    }
    if local_tip:
        result["local_tip"] = local_tip
    return result


# --------------------------------------------------------------------------
# Which legs get options at all
# --------------------------------------------------------------------------

def booked_arrival_leg(dest: dict[str, Any] | None) -> dict[str, Any] | None:
    """The booked leg arriving at this destination, if the traveler holds one.

    reservation_ingest already attaches a booking to the destination it
    delivers the traveler TO, which is the same convention `transport_mode`
    follows, so the two line up without new matching logic.
    """
    if not isinstance(dest, dict):
        return None
    for leg in (dest.get("transportation") or []):
        if isinstance(leg, dict) and str(leg.get("type", "") or "").strip():
            return leg
    return None


def resolve_leg_mode(
    dest: dict[str, Any] | None,
    *,
    previous_id: str = "",
    trip_meta: dict[str, Any] | None = None,
    legs: Any = None,
) -> str:
    """The travel mode for the leg ARRIVING at `dest`: auto | transit | mixed.

    Precedence, most specific first: a `legs:` entry naming this leg, then
    the destination's own `transport_mode`, then the trip-wide default, then
    `auto`. The parser has already raised on any disagreement between the
    first two (multimodal-routing.md 3.2), so this ordering never silently
    picks a winner between two contradictory authored statements.

    A grouped entry is always `auto`: a there-and-back day trip from a shared
    base has no arriving relocation leg for a mode to describe. The parser
    warns about a mode set there; this is where it is ignored.
    """
    if not isinstance(dest, dict):
        return "auto"
    if str(dest.get("group_with", "") or "").strip():
        return "auto"

    dest_id = str(dest.get("id", "") or "").strip()
    if isinstance(legs, list) and dest_id:
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            if str(leg.get("to", "") or "").strip() != dest_id:
                continue
            if previous_id and str(leg.get("from", "") or "").strip() != previous_id:
                continue
            mode = str(leg.get("mode", "") or "").strip()
            if mode:
                return mode

    mode = str(dest.get("transport_mode", "") or "").strip()
    if mode:
        return mode
    return str((trip_meta or {}).get("transport_mode", "") or "").strip() or "auto"


#: Where the resolved leg mode is stamped onto a destination. Resolution
#: needs the trip-wide default and the `legs:` list, which are trip-level;
#: the modules that act on the answer (ai_content, url_discovery) see only a
#: destination. Resolving once at parse time and stamping the result means
#: those two cannot drift apart the way two independent resolutions would.
RESOLVED_MODE_KEY = "_transport_mode"


def stamp_resolved_modes(trip: dict[str, Any]) -> None:
    """Resolve every leg's mode once and record it on the arriving stop."""
    destinations = [d for d in (trip.get("destinations") or []) if isinstance(d, dict)]
    trip_meta = trip.get("trip") if isinstance(trip.get("trip"), dict) else {}
    legs = trip.get("legs")
    previous_id = ""
    for index, dest in enumerate(destinations):
        # The first destination has no inbound leg between stops -- the
        # journey into it is the trip's own arrival, which trip.transportation
        # describes.
        mode = "auto" if index == 0 else resolve_leg_mode(
            dest, previous_id=previous_id, trip_meta=trip_meta, legs=legs
        )
        dest[RESOLVED_MODE_KEY] = mode
        if not str(dest.get("group_with", "") or "").strip():
            previous_id = str(dest.get("id", "") or "").strip()


def resolved_mode(dest: dict[str, Any] | None) -> str:
    """The stamped mode, or `auto` for a trip that never ran the stamp."""
    if not isinstance(dest, dict):
        return "auto"
    return str(dest.get(RESOLVED_MODE_KEY, "") or "").strip() or "auto"


def suppresses_en_route_stops(dest: dict[str, Any] | None) -> bool:
    """True on a `transit` leg, false on `mixed` (multimodal-routing.md 4.4).

    There is no roadside to stop at on a train, so an en-route stop there is
    structurally meaningless -- and skipping one of the four parallel
    discovery jobs is a real cost saving. Under `mixed` the drive is still on
    the table, so its stops are still real.
    """
    return resolved_mode(dest) == "transit"


def should_generate_options(dest: dict[str, Any] | None, mode: str) -> bool:
    """Whether this leg gets generated transit options.

    A booked leg outranks a generated one and suppresses generation entirely
    (multimodal-routing.md 4.6). The traveler holds a confirmation; offering
    "there might be a bus around 9" beside "your 09:00 flight, locator
    XR7Q2M" is noise attached to a decided question.

    Unlike the `legs:`/`transport_mode` collision, this does NOT raise. That
    is an authoring statement against an observed fact, not two authoring
    statements: a traveler who wrote `transport_mode: transit` in March and
    forwarded a car-rental confirmation in August has not made an error.
    Logged at INFO so the divergence stays discoverable.
    """
    if mode not in ("transit", "mixed"):
        return False
    booking = booked_arrival_leg(dest)
    if booking is not None:
        logger.info(
            "Transit options suppressed for '%s': transport_mode '%s' is outranked by a "
            "booked %s leg the traveler already holds",
            (dest or {}).get("name", ""), mode, booking.get("type", ""),
        )
        return False
    return True


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------

def read_transit_provider(config_path: str | Path) -> str:
    """`transit_routing.provider` from config.yaml, defaulting to `ai`.

    Fail-open to the default on an unreadable config, matching
    search_provider.py: a missing config should not crash construction.
    """
    try:
        import yaml

        with Path(config_path).open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        provider = str(((cfg.get("transit_routing") or {}).get("provider") or "ai")).strip().lower()
    except Exception:
        return "ai"
    if provider not in VALID_TRANSIT_PROVIDERS:
        logger.warning("Unknown transit_routing.provider '%s', falling back to ai", provider)
        return "ai"
    return provider


class AITransitProvider:
    """Phase 1: ask the content model, then disbelieve most of the answer.

    Holds no search client. Corroborating a named operator through the search
    path was considered and deferred (open question 9) rather than shipped
    off-by-default, so Phase 1 carries no dormant cost lever.
    """

    source = "ai"
    confidence = "unverified"

    def __init__(self, llm_client: Any, *, template: str | None = None,
                 system_prompt: str | None = None) -> None:
        self._llm = llm_client
        self._template = (
            template
            if template is not None
            else (PROMPTS_DIR / "transit_options.txt").read_text(encoding="utf-8")
        )
        self._system_prompt = (
            system_prompt
            if system_prompt is not None
            else (PROMPTS_DIR / "system_prompt.txt").read_text(encoding="utf-8")
        )

    def generate_transit_options(
        self,
        from_dest: dict[str, Any],
        to_dest: dict[str, Any],
        trip_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        origin = str((from_dest or {}).get("name", "") or "").strip()
        arrival = str((to_dest or {}).get("name", "") or "").strip()
        if not origin or not arrival:
            return format_b("This leg's endpoints are not known well enough to look for transit.")

        prompt = self._template.format(
            origin=origin,
            destination=arrival,
            dates=str((to_dest or {}).get("dates", "") or ""),
            trip_title=str((trip_meta or {}).get("title", "") or ""),
        )
        try:
            payload = self._llm.generate_json(
                system_prompt=self._system_prompt,
                user_prompt=prompt,
                operation=f"{USAGE_OPERATION_PREFIX}:{arrival}",
            )
        except Exception as exc:
            # An error is not a negative. Say so in the card rather than
            # asserting that no service exists on a corridor we failed to ask
            # about.
            logger.warning("Transit options for '%s' -> '%s' failed: %s", origin, arrival, exc)
            return format_b(
                "Public transport options for this leg could not be checked for this build."
            )

        result = normalize_transit_options(
            payload, source=self.source, confidence=self.confidence
        )
        logger.info(
            "  → Transit options %s -> %s: has_transit=%s, %d option(s)",
            origin, arrival, result.get("has_transit"), len(result.get("options") or []),
        )
        return result


def build_transit_provider(
    config_path: str | Path = "config.yaml",
    *,
    llm_client: Any = None,
    provider_override: str | None = None,
) -> AITransitProvider:
    """Factory mirroring search_provider.build_search_client.

    Its point is that Phase 2 becomes a config flip rather than a rewrite --
    and that an A/B comparison run on one manifest is possible, which is how
    this project has settled every provider question so far.
    """
    provider = (provider_override or read_transit_provider(config_path)).strip().lower()
    if provider == "google_directions":
        raise NotImplementedError(
            "transit_routing.provider 'google_directions' is Phase 2 and is not "
            "implemented. See docs/design/multimodal-routing.md 2.2 and 6.4 — the "
            "Maps Platform terms question is unresolved. Use 'ai' for now."
        )
    if llm_client is None:
        from generator.llm_client import MultiLLMClient

        llm_client = MultiLLMClient(config_path)
    return AITransitProvider(llm_client)
