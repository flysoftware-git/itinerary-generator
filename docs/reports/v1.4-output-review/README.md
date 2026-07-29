# V1.4 Output Review

This directory tracks problem reports discovered during manual review of the generated v1.4 output.

## Workflow

- Reviewer submits feedback in chat using the format `Bug: <description>`.
- For each bug, create one markdown report in this directory.
- File naming convention:
  - `PR-001-short-slug.md`
  - `PR-002-short-slug.md`
- Update [index.md](index.md) with a one-line entry per report.

## Labels

Each report should include labels in frontmatter-like fields near the top.

Recommended labels:
- `review:v1.4-output`
- `type:bug`
- `status:open`
- one or more area labels, for example:
  - `area:url-discovery`
  - `area:html-output`
  - `area:image-selection`
  - `area:validation`
  - `area:content-linking`
  - `area:restaurant-linking`
  - `area:alltrails`
  - `area:google-maps`

Optional labels:
- `severity:low`
- `severity:medium`
- `severity:high`
- `manifest:sw_manifest`
- `manifest:trip_manifest`

## Notes

- These are tracking reports, not fixes.
- Reports should capture evidence, expected behavior, actual behavior, and likely component ownership.
- Avoid mixing multiple defects into one report unless they clearly share the same root cause.