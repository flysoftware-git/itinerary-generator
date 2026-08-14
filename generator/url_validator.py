"""
url_validator.py — HTTP HEAD verification for user-supplied URLs.

Verifies planning_links provided by the user in the manifest.
AI-generated content URLs are NOT handled here — see url_discovery.py.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse
import requests
from requests.exceptions import RequestException, SSLError

logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 10
DEFAULT_UA = "RoadTripItineraryGenerator/1.0"
MAX_RETRIES = 2
TRUSTED_SSL_FALLBACK_HOST_SUFFIXES = ("blm.gov",)


class URLValidator:
    _COUNTER_LOCK = threading.Lock()
    _COUNTERS: dict[str, int] = {
        "head_requests": 0,
        "get_requests": 0,
        "get_text_requests": 0,
    }

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, user_agent: str = DEFAULT_UA) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.results: list[dict[str, Any]] = []

    @classmethod
    def _increment_counter(cls, key: str) -> None:
        with cls._COUNTER_LOCK:
            cls._COUNTERS[key] = int(cls._COUNTERS.get(key, 0) or 0) + 1

    @classmethod
    def snapshot_counters(cls) -> dict[str, int]:
        with cls._COUNTER_LOCK:
            return {k: int(v or 0) for k, v in cls._COUNTERS.items()}

    def verify_planning_links(self, trip: dict[str, Any]) -> None:
        for dest in trip.get("destinations", []):
            for link in dest.get("planning_links", []):
                ok, status = self._check(link.get("url", ""))
                link["verified"] = ok
                link["http_status"] = status
                logger.log(
                    logging.INFO if ok else logging.WARNING,
                    "[%s] %s → %s", "OK" if ok else "FAIL", link.get("url", ""), status
                )
                self.results.append({"url": link.get("url"), "verified": ok, "status": status})

    def verify_url(self, url: str) -> tuple[bool, int | str]:
        return self._check(url)

    def get_text(self, url: str, timeout: int | None = None) -> tuple[bool, int | str, str]:
        if not url or not urlparse(url).scheme:
            return False, "invalid_url", ""
        to = timeout or self.timeout
        try:
            self._increment_counter("get_text_requests")
            self._increment_counter("get_requests")
            resp = self.session.get(url, timeout=to)
            # Expose final URL after redirects for downstream entity checks.
            self._last_final_url = str(getattr(resp, "url", None) or url)
            return resp.status_code < 400, resp.status_code, resp.text or ""
        except RequestException as exc:
            if self._is_ssl_error(exc) and self._is_trusted_ssl_fallback_host(url):
                try:
                    self._increment_counter("get_text_requests")
                    self._increment_counter("get_requests")
                    resp = self.session.get(url, timeout=to, verify=False)
                    self._last_final_url = str(getattr(resp, "url", None) or url)
                    logger.info("SSL verify bypass used for trusted host: %s", urlparse(url).netloc)
                    return resp.status_code < 400, resp.status_code, resp.text or ""
                except RequestException as inner_exc:
                    return False, str(inner_exc), ""
            return False, str(exc), ""

    def _check(self, url: str) -> tuple[bool, int | str]:
        if not url or not urlparse(url).scheme:
            return False, "invalid_url"
        for attempt in range(MAX_RETRIES + 1):
            try:
                self._increment_counter("head_requests")
                resp = self.session.head(url, timeout=self.timeout, allow_redirects=True)
                if resp.status_code == 405:
                    self._increment_counter("get_requests")
                    resp = self.session.get(url, timeout=self.timeout, allow_redirects=True, stream=True)
                    resp.close()
                return resp.status_code < 400, resp.status_code
            except RequestException as exc:
                if self._is_ssl_error(exc) and self._is_trusted_ssl_fallback_host(url):
                    try:
                        self._increment_counter("head_requests")
                        resp = self.session.head(url, timeout=self.timeout, allow_redirects=True, verify=False)
                        if resp.status_code == 405:
                            self._increment_counter("get_requests")
                            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True, stream=True, verify=False)
                            resp.close()
                        logger.info("SSL verify bypass used for trusted host: %s", urlparse(url).netloc)
                        return resp.status_code < 400, resp.status_code
                    except RequestException as inner_exc:
                        if attempt == MAX_RETRIES:
                            return False, str(inner_exc)
                if attempt == MAX_RETRIES:
                    return False, str(exc)
                time.sleep(1)
        return False, "timeout"

    @staticmethod
    def _is_ssl_error(exc: RequestException) -> bool:
        if isinstance(exc, SSLError):
            return True
        text = str(exc).lower()
        return (
            "ssl" in text
            or "certificate verify failed" in text
            or "certificateverifyfailed" in text
            or "hostname mismatch" in text
        )

    @staticmethod
    def _is_trusted_ssl_fallback_host(url: str) -> bool:
        host = (urlparse(url).netloc or "").lower()
        if not host:
            return False
        for suffix in TRUSTED_SSL_FALLBACK_HOST_SUFFIXES:
            if host == suffix or host.endswith("." + suffix):
                return True
        return False
