# Requirements Traceability — v2.1.0

**Supersedes** `requirements-traceability-v0.30-to-v0.20.md` for the areas below.
That matrix predates v2 entirely; it is retained for history, not for coverage
claims.

Scope: requirement areas added or materially changed since `v2.0.1`. Mappings
were derived by searching the suite, not asserted — **gaps are listed, not
omitted.** A linkage matrix that quietly skips an uncovered area reads as
coverage, which is worse than having no matrix.

---

## 1. Matrix

| Req | Area | Automated coverage | Manual / live only |
|---|---|---|---|
| §3.1 | Trip-level `transportation[]` | `test_reservation_ingest.py` (trip-level routing, merge, dedupe) · `test_html_assembler.py` (`_build_trip_transportation`) | chip rendering read in a real build |
| §3.3 | `lodging.confirmation_number` | `test_main_requirements.py` (redaction) · `test_html_assembler.py` (card, escaping) · `test_reservation_ingest.py` (extraction, dedupe) | legibility of the code in the rendered card |
| §3.3 | `lodging.website` | `test_main_requirements.py` (redacted with the name) · `test_html_assembler.py` | link target correctness |
| §3.3 | Destination `transportation[]` | `test_html_assembler.py` (`_build_transportation_pills`) · `test_reservation_ingest.py` | pill placement in the header row |
| §3.4 | Sidecar merge + re-validation | `test_reservation_ingest.py` (fill-not-overwrite, append+dedupe, unknown id, schema rejection) | — |
| §3.4 | Match outcomes (attach / pending / unrelated) | `test_reservation_ingest.py` (`UNRELATED_SCORE_FLOOR`, near-tie, gateways, endpoint selection, single-token floor) | whether a real inbox's bookings match correctly |
| §3.4 | Mailbox filing | `test_reservation_ingest.py` (MOVE, COPY fallback, no EXPUNGE, failure non-fatal, selective uids) | real IMAP server behaviour; folder creation permissions |
| §3.4 | IMAP credentials | `test_reservation_ingest.py` (Gmail diagnostics, app-password whitespace, secret never in error text) | live login against a real provider |
| §11.1 | Redaction of booking data | `test_main_requirements.py` (every field, both transportation levels, location/checkin kept) | **grep a real prod build for a known confirmation number** |
| §10 | Persistent cache | `conftest.py` (test isolation) · `test_url_discovery.py` (TTL preservation, expiry, liveness-vs-content invariant) | warm-vs-cold cost on consecutive real runs |
| §6 | Wikimedia throttle / 429 | `test_image_fetcher.py` (429 → retry not "no images", fallback to Unsplash) | real rate-limit behaviour under a full run |
| §15 | Drive modal integrity | `test_html_assembler.py` (buttons == entries, cross-destination) · `test_html_validator.py` (validator check) | — |
| §15 | **Full Route Map placement** | **NONE — see §2.1** | button visible at laptop/tablet widths |
| §9 | Runner postures | none (the runner is outside the repo) | environment validation, sidecar reporting, publish guard |

---

## 2. Gaps

### 2.1 Full Route Map relocation is untested

Moving the button out of the nav strip and beside the Route Overview heading
changed `_build_nav_tabs`, added `_build_route_map_link`, and edited the
template. A search of the suite for `map-tab-btn` or `route_map_link` returns
**no tests**.

The original defect — the button straddling the scroll container's clip edge,
67px cut off at 1280px — was found by eye and fixed by restructuring, then
verified by measuring the rendered page in a browser. That verification was
real but is not repeatable in CI.

Minimum worth adding: `_build_nav_tabs` no longer emits `map-tab-btn`;
`_build_route_map_link` returns a well-formed anchor and an empty string when
no route URL exists; the template contains exactly one `ROUTE_MAP_LINK`
placeholder. None of these catch a visual regression, but all three catch the
structural mistake of the button silently vanishing.

### 2.2 The runner is outside the repository

Environment posture validation, sidecar presence reporting, and the publish
guard live in a working script outside version control. Its guards were
exercised by hand — `dev`/`eval`/empty skip publish, `prod`/`PROD` proceed,
`prd` rejected — but nothing re-checks them.

The repo's `scripts/run-trip.BAT` carries the documentation but not the code,
so a future edit could reintroduce a publish-on-dev path with no test failing.
Two of the three bugs found in that script this cycle had **never executed
before**, because an unrelated path bug skipped publishing on every run.

### 2.3 No provenance canary

Nothing detects a provider silently ceasing to perform live search while still
returning plausible content. This has already happened once, undetected for an
unknown period. Out of scope for v2.1.0; recorded so it is a decision.

---

## 3. Cadence compliance (§19)

1. **Focused gates first** — `v2.1.0-test-plan.md` §2
2. **One controlled smoke execution after they pass** — §3, eval before prod
3. **Smoke output as confirmation, not primary discovery** — the four defects
   found this cycle (cache clobbering, Wikimedia 429s, drive-modal orphan,
   publish-block parsing) were all found by focused investigation of a specific
   symptom, not by reading smoke output. That is the intended order and it held.
