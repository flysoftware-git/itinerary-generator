# PR-017: Ajax Peak points to unavailable AllTrails URL and should be discarded when target is invalid

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:content-linking`, `area:hiking`, `area:html-output`

**Fixed in:** v1.4.2 — AllTrails slug denylist added to `config.yaml` (`alltrails_slug_denylist`); `ajax-peak-trail` is explicitly denied, causing `_retain_discovered_url` and `_is_relevant_result` to reject the URL before any network fetch. Rationale: AllTrails bot-blocking (403) prevents automated 404 detection; the denylist is the correct escape hatch for known-invalid slugs verified in browser.

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Ajax Peak` is linked to an AllTrails URL that is reported as unavailable (404). Invalid/unavailable targets should be discarded rather than published as final links.

## Expected Behavior

- Final published links must resolve to valid, accessible subject-specific destinations.
- If a target resolves as unavailable (for example 404), the link should be discarded.
- Content may remain as plain text, but hyperlink output should be removed when validation fails.

## Actual Behavior

- `Ajax Peak` is currently published with:
  - `https://www.alltrails.com/trail/us/colorado/ajax-peak-trail`
- Reported outcome: target returns 404 (not found) and should not be emitted.

## Evidence

- In [output/index.html](output/index.html), line 1278 contains:
  - visible link text: `Ajax Peak`
  - rendered URL: `https://www.alltrails.com/trail/us/colorado/ajax-peak-trail`
- Runtime verification note:
  - Automated fetch from this environment returned HTTP 403 (access blocked), so the report uses user-observed browser outcome of 404 for acceptance criteria.

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Telluride` attractions
- Subject: `Ajax Peak`

## Suspected Area

- Primary components: final-link validation and fail-closed publication policy
- Possible files:
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- URL acceptance currently allows links that are not robustly validated for availability/quality under end-user conditions, allowing dead/unavailable targets to pass through.

## Scope of Likely Fix

- Enforce final availability checks before publish.
- Discard links when explicit invalid/unavailable outcomes are observed.
- Prefer no link over publishing a dead target.

## Non-Breaking Validation Plan

- Unit tests:
  - URLs flagged unavailable are excluded from rendered hyperlinks.
  - valid hiking links continue to render normally.
- Integration checks:
  - regenerate output and verify `Ajax Peak` is rendered without hyperlink when URL is unavailable.

## Notes

- This is intake-only; no implementation change is included.
