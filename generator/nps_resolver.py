"""
nps_resolver.py — Detect NPS parks and resolve park codes via the NPS API.
"""
from __future__ import annotations
import logging, os, re
import requests

logger = logging.getLogger(__name__)
NPS_API_BASE = "https://developer.nps.gov/api/v1"
NPS_KEYWORDS = [
    "national park", "national monument", "national recreation area",
    "national seashore", "national lakeshore", "national preserve",
    "national memorial", "national historic", "national battlefield",
]

KNOWN_PARK_CODE_HINTS = {
    "zion": "zion",
    "bryce canyon": "brca",
    "capitol reef": "care",
    "arches": "arch",
    "canyonlands": "cany",
    "yellowstone": "yell",
    "yosemite": "yose",
    "grand canyon": "grca",
    "rocky mountain": "romo",
}


class NPSResolver:
    
    def __init__(self) -> None:
        self.api_key = os.environ.get("NPS_API_KEY", "DEMO_KEY")
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": self.api_key})

    def _looks_like_nps(self, name: str) -> bool:
        lower = name.lower()
        return any(kw in lower for kw in NPS_KEYWORDS)

    def _resolve_park_code(self, name: str) -> str | None:
        normalized_name = re.sub(r"\s+", " ", (name or "").lower()).strip()
        for hint, code in KNOWN_PARK_CODE_HINTS.items():
            if hint in normalized_name:
                return code

        query = re.sub(
            "|".join(NPS_KEYWORDS), "", name, flags=re.IGNORECASE
        ).strip()[:40]
        target = re.sub(r"\s+", " ", name.lower()).strip()

        def _score(candidate_name: str) -> int:
            c = candidate_name.lower()
            score = 0
            if target == c:
                score += 100
            target_tokens = [t for t in re.findall(r"[a-z0-9]+", target) if len(t) > 2]
            cand_tokens = set(t for t in re.findall(r"[a-z0-9]+", c) if len(t) > 2)
            if target_tokens and all(t in cand_tokens for t in target_tokens):
                score += 50
            for token in re.findall(r"[a-z0-9]+", target):
                if len(token) > 2 and token in c:
                    score += 4
            if "national park" in c:
                score += 3
            return score

        try:
            resp = self.session.get(
                f"{NPS_API_BASE}/parks",
                params={"q": query, "limit": 5},
                timeout=10,
            )
            resp.raise_for_status()
            parks = resp.json().get("data", [])
            if parks:
                best = max(parks, key=lambda p: _score(p.get("fullName", "")))
                return best.get("parkCode")
        except requests.RequestException as exc:
            logger.warning("NPS API error for '%s': %s", name, exc)
        return None

    def resolve(self, name: str) -> str | None:
        """Public wrapper for park code resolution."""
        if self._looks_like_nps(name):
            return self._resolve_park_code(name)
        return None
