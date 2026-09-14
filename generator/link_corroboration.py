"""Honest alternative evidence for a link the run could not fetch.

A link whose host refuses automated fetches -- a 401/403/429 block page, a
per-domain block cooldown, a connection reset -- is `unchecked` in the
liveness ledger, and stays so here. Nothing below changes what counts as
`live` or `dead`, and nothing below tries to get past a block: no different
client, no retries under another identity. The fetch was refused, and the
page says so.

What this module adds is one recorded fact beside that state: **the exact
URL was returned as a result by a search-engine index this run.** A search
engine listing a page is evidence the page exists -- weaker than fetching it,
and reported as weaker -- and when the run already paid for that search, the
evidence costs nothing.

The rules, each of which a test holds:

* **Only a refusal is corroborated.** A timeout, or a resolver's temporary
  failure, says nothing about the host at all, and a never-fetched link was
  never refused. Those stay plainly `unchecked`.
* **Only index rows count.** A provider whose results are rows from a
  search-engine index declares `RESULTS_ARE_SEARCH_INDEX_ROWS = True`
  (Serper). A model-backed provider's `search()` returns URLs a model wrote
  down after searching; a URL a model transcribed is not a URL an index
  returned, so those rows corroborate nothing.
* **Only this run's searches.** Rows loaded from the persistent search cache
  were returned by an earlier run, up to the cache TTL ago, and are not
  recorded.
* **The state stays `unchecked`.** Corroboration is recorded in the report as
  `corroborated_by` (URL -> `"search_index"`) and `corroborated_count`, which
  is a subset of the `unchecked` count, so `live + dead + unchecked` still
  equals `published_count`. The card's "not checked" mark stays, because the
  link was not fetched.

An opt-in extra search (`url_discovery.link_corroboration_search`, off by
default) spends one site-restricted search per still-uncorroborated refused
link, up to a per-run cap, through the same cached, usage-recorded search
path as every other per-item search.
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

logger = logging.getLogger(__name__)

CORROBORATED_BY_SEARCH_INDEX = "search_index"

# Opt-in site-restricted search: off unless configured on, and bounded.
DEFAULT_LINK_CORROBORATION_SEARCH_ENABLED = False
DEFAULT_LINK_CORROBORATION_SEARCH_MAX_PER_RUN = 10
LINK_CORROBORATION_CALL_SITE = "link_corroboration_search"

# Query parameters that identify a click, not a page.
_TRACKING_PARAMS = frozenset(
    {"gclid", "dclid", "fbclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "_ga", "ref_src"}
)

# What a refusal looks like in the ledger's detail string.
_REFUSED_STATUSES = frozenset({"401", "403", "429"})
_REFUSED_DETAILS = frozenset({"domain_cooldown"})
_TIMEOUT_MARKERS = ("timed out", "timeout")
# Kept in step with `URLDiscoverer._is_definitively_dead_status`.
_TEMPORARY_DNS_MARKERS = ("errno 11002", "temporary failure in name resolution", "errno -3]")
_REFUSED_CONNECTION_MARKERS = (
    "connection reset",
    "connectionreseterror",
    "connection aborted",
    "remotedisconnected",
    "connection refused",
    "connectionrefusederror",
    "errno 10054",
    "errno 104]",
)


def normalize_for_corroboration(url: Any) -> str:
    """One comparable form for a URL: scheme, `www.`, default port, trailing
    slash, fragment and tracking parameters do not distinguish two pages.

    Path case is kept -- paths are case-sensitive on most hosts -- and the
    remaining query parameters are kept and sorted, because on many sites they
    are what names the page.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = unquote(parts.path or "").rstrip("/")
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMS
    )
    encoded = urlencode(query)
    return f"{netloc}{path}?{encoded}" if encoded else f"{netloc}{path}"


def is_refused_fetch(url: str, detail: Any) -> bool:
    """True when an `unchecked` ledger detail records the host refusing us.

    Timeouts and a resolver's temporary failure are not refusals: they are the
    instrument failing, and a search result cannot stand in for an answer the
    host never gave. `never_fetched` is not a refusal either.
    """
    text = str(detail or "").strip().lower()
    if not text:
        return False
    if text in _REFUSED_STATUSES or text in _REFUSED_DETAILS:
        return True
    if any(marker in text for marker in _TIMEOUT_MARKERS):
        return False
    if any(marker in text for marker in _TEMPORARY_DNS_MARKERS):
        return False
    if any(marker in text for marker in _REFUSED_CONNECTION_MARKERS):
        return True
    # The `.gov` carve-out: a connection-level refusal from a host too
    # established to have gone, which the ledger already keeps `unchecked`.
    from generator.url_discovery import URLDiscoverer

    return URLDiscoverer._is_bot_block_false_negative_dead_status(url, text)


def client_returns_index_rows(client: Any) -> bool:
    """Whether a search client's result URLs came from a search-engine index.

    `is True`, not truthiness: a mock client answers any attribute with a
    truthy object, and a guess must not read as a declaration.
    """
    return getattr(client, "RESULTS_ARE_SEARCH_INDEX_ROWS", False) is True


def record_search_results(discoverer: Any, client: Any, rows: Iterable[Any]) -> int:
    """Remember the URLs a fresh index search returned this run. Returns how many were new."""
    if not client_returns_index_rows(client):
        return 0
    urls = {
        normalize_for_corroboration(row.get("url"))
        for row in rows
        if isinstance(row, dict)
    }
    urls.discard("")
    if not urls:
        return 0
    lock = getattr(discoverer, "_search_index_urls_lock", None)
    if lock is None:
        lock = Lock()
        discoverer._search_index_urls_lock = lock
    with lock:
        seen = getattr(discoverer, "_search_index_urls", None)
        if seen is None:
            seen = set()
            discoverer._search_index_urls = seen
        before = len(seen)
        seen.update(urls)
        return len(seen) - before


def corroborated_by(discoverer: Any, states: dict[str, str], details: dict[str, str]) -> dict[str, str]:
    """URL -> evidence, for each refused `unchecked` link this run's index returned."""
    seen = getattr(discoverer, "_search_index_urls", None) or set()
    if not seen:
        return {}
    evidence: dict[str, str] = {}
    for url, state in states.items():
        if state != "unchecked" or not is_refused_fetch(url, details.get(url, "")):
            continue
        if normalize_for_corroboration(url) in seen:
            evidence[url] = CORROBORATED_BY_SEARCH_INDEX
    return dict(sorted(evidence.items()))


def _query_for(link: Any) -> str:
    host = (urlsplit(link.url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    holder = link.holder if isinstance(link.holder, dict) else {}
    name = str(
        holder.get("name", "") or holder.get("title", "") or holder.get("trail_label", "") or ""
    ).strip()
    if not name:
        tail = unquote(urlsplit(link.url).path.rstrip("/").rsplit("/", 1)[-1])
        name = " ".join(part for part in tail.replace("_", "-").split("-") if part)
    dest = str(link.dest.get("name", "") or "").strip() if isinstance(link.dest, dict) else ""
    return " ".join(part for part in (f"site:{host}", name, dest) if part)


def search_for_uncorroborated(discoverer: Any, links: Iterable[Any]) -> int:
    """The opt-in: one site-restricted search per refused, uncorroborated link.

    Off unless `_link_corroboration_search_enabled` is True, capped at
    `_link_corroboration_search_max` searches per run, and a no-op when the
    per-item search client does not return index rows (a paid search whose
    results could not corroborate anything is not made). Returns the number of
    searches requested.
    """
    if getattr(discoverer, "_link_corroboration_search_enabled", DEFAULT_LINK_CORROBORATION_SEARCH_ENABLED) is not True:
        return 0
    client = getattr(discoverer, "_search_fallback", None) or getattr(discoverer, "_search", None)
    if not client_returns_index_rows(client):
        return 0
    try:
        cap = int(getattr(discoverer, "_link_corroboration_search_max", DEFAULT_LINK_CORROBORATION_SEARCH_MAX_PER_RUN))
    except (TypeError, ValueError):
        cap = DEFAULT_LINK_CORROBORATION_SEARCH_MAX_PER_RUN
    used = int(getattr(discoverer, "_link_corroboration_searches_used", 0) or 0)
    recorded = getattr(discoverer, "_link_liveness", None) or {}
    done: set[str] = set()
    requested = 0
    for link in links:
        if used >= cap:
            break
        url = link.url
        key = normalize_for_corroboration(url)
        if not key or key in done:
            continue
        entry = recorded.get(url)
        if not entry or entry[0] != "unchecked" or not is_refused_fetch(url, entry[1]):
            continue
        if key in (getattr(discoverer, "_search_index_urls", None) or set()):
            continue
        done.add(key)
        used += 1
        requested += 1
        if hasattr(discoverer, "_note_fallback_call_site"):
            discoverer._note_fallback_call_site(LINK_CORROBORATION_CALL_SITE)
        try:
            discoverer._search_cached(_query_for(link), count=10)
        except Exception as exc:  # evidence-gathering must not fail the run
            logger.info("Corroboration search failed for %s: %s", url, exc)
    discoverer._link_corroboration_searches_used = used
    return requested
