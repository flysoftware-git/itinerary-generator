# PR-018: Bear Creek Trail link resolves to different entity (Penrose Trail), violating promise-to-target match

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:content-linking`, `area:hiking`, `area:redirect-validation`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Bear Creek Trail` is presented as a specific trail recommendation, but the published URL resolves to a different trail entity (`Penrose Trail`) according to user verification. This violates promise-to-target integrity.

## Expected Behavior

- A named trail link must resolve to the same named trail entity.
- Redirect chains must be validated for final-entity match, not just host/path acceptance.
- If the final destination does not match the promised entity, discard the link.

## Actual Behavior

- Published link text: `Bear Creek Trail`
- Published URL: `https://www.alltrails.com/trail/us/colorado/bear-creek-trail`
- User-observed outcome: resolves to `Penrose Trail, Colorado - 342 Reviews, Map | AllTrails` (`https://www.alltrails.com/trail/us/colorado/penrose-trail`)

## Evidence

- In [output/index.html](output/index.html), line 1279 contains the `Bear Creek Trail` callout and URL.
- Runtime verification note:
  - Automated request from this environment returned HTTP 403 (AllTrails access restrictions), so redirect destination is documented from user-observed browser behavior.

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Telluride` attractions
- Subject: `Bear Creek Trail`

## Why This Happens (Hypothesis)

- URL validation likely checks that a link is syntactically plausible and on an allowed domain, but does not assert semantic identity of the final resolved page.
- Provider-side redirects, retired slugs, or canonical remaps can silently point to a different entity.
- Without final-page entity verification (title/slug/name match), mismatched redirects are published.

## Suspected Area

- Primary components: redirect-aware URL validation and entity match checks
- Possible files:
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Scope of Likely Fix

- Follow redirects to final URL and validate final entity identity against promised label.
- Reject links where final title/slug mismatches subject (e.g., Bear Creek vs Penrose).
- Fail closed (render text without hyperlink) when entity match cannot be proven.

## Non-Breaking Validation Plan

- Unit tests:
  - redirect to different entity is rejected.
  - redirect to canonical same-entity URL is allowed.
- Integration checks:
  - regenerate output and verify `Bear Creek Trail` has no link unless validated to matching final entity.

## Notes

- This is intake-only; no implementation change is included.
