# V2 Non-Regression Invariants (Issue #6)

Purpose: capture the current v0.30 behavioral contract that Version 2 must preserve while changing orchestration.

Baseline:
- Requirements contract: [../requirements.md](../requirements.md) version 0.30.
- Rollback-safe commit: `ff71a13` (`Stabilize review fixes before v2 architecture`).

## Scope

These invariants are about runtime behavior, not implementation shape.
Version 2 may batch acquisition stages and add registry/reconciliation layers, but it must not weaken these outcomes.

## Core Invariants

### 1. Rendering Contract Stays Destination-Shaped

- The final structure consumed by [../../generator/html_assembler.py](../../generator/html_assembler.py) remains destination-shaped.
- Destination sections still render from `trip -> destinations[] -> ai_content/images/scenic_drives/cultural_events`.
- Final-leg transfer content remains represented inside `ai_content.getting_there.route_options`, not as generic in-stay activity cards.

Primary references:
- [../requirements.md](../requirements.md)
- [../../generator/html_assembler.py](../../generator/html_assembler.py)

### 2. Named-Entity URLs Are Fail-Closed

- A named entity must not publish a fabricated, ambiguous, or unverifiable link.
- If verification fails, the item renders with an empty URL or plain text.
- Query-style fallback links are not acceptable for named entities.

Must preserve:
- URL class blocklist
- domain denylist
- AllTrails slug denylist
- redirect entity-match checks
- category-vs-entity handling
- confidence gating

Primary references:
- [../requirements.md](../requirements.md)
- [../../generator/url_discovery.py](../../generator/url_discovery.py)

Primary regression surface:
- [../../tests/test_url_discovery.py](../../tests/test_url_discovery.py)

### 3. Trail-Like Attractions Do Not Downgrade to Generic Named-Entity Fallback Links

- Trail-like items that do not retain a validated trail URL must render without a link.
- Dead, denylisted, or mismatched trail entities must not silently degrade into generic map-search links.

Primary references:
- [../../generator/url_discovery.py](../../generator/url_discovery.py)
- [../../tests/test_url_discovery.py](../../tests/test_url_discovery.py)

### 4. Schedule Normalization Wins Over Renderer Synthesis

- If structured schedule content exists, the renderer must preserve it.
- One-day arrival-aware schedules must not be overwritten by generic synthetic Day 1 schedule text.
- First-destination arrival and final-destination departure rules remain mandatory.

Primary references:
- [../../generator/ai_content.py](../../generator/ai_content.py)
- [../../generator/html_assembler.py](../../generator/html_assembler.py)

Primary regression surface:
- [../../tests/test_ai_content_normalization.py](../../tests/test_ai_content_normalization.py)
- [../../tests/test_html_assembler.py](../../tests/test_html_assembler.py)

### 5. Transfer-Leg Ownership Remains Explicit

- Departure-aligned one-way scenic drives belong to the final-leg `Getting There` block when they align with the return route.
- Transfer-leg-owned content must not be duplicated as ordinary destination activity when ownership is route-leg specific.

Primary references:
- [../requirements.md](../requirements.md)
- [../../generator/ai_content.py](../../generator/ai_content.py)
- [../../generator/html_assembler.py](../../generator/html_assembler.py)

### 6. Cross-Destination and Cross-Section Reconciliation Remains Mandatory

- Duplicate concepts across sections or destinations must be reconciled rather than rendered twice with conflicting ownership or links.
- What to Know must not echo Cultural Events fallback prose.
- Scenic-drive/attraction overlap must continue to resolve to one canonical representation.

Primary references:
- [../../generator/ai_content.py](../../generator/ai_content.py)
- [../../generator/url_discovery.py](../../generator/url_discovery.py)
- [../requirements.md](../requirements.md)

### 7. Template Integrity Remains Mandatory

- `section-{id}` identifiers must remain aligned with manifest destination ids.
- `DRIVE_DESCRIPTIONS` keys must remain aligned with drive-link references.
- Route overview markers must preserve stop ordering and readable date context.
- Frozen template checksum enforcement remains part of the contract.

Primary references:
- [../../templates/v2.5_template.html](../../templates/v2.5_template.html)
- [../../templates/checksums.txt](../../templates/checksums.txt)
- [../../generator/html_assembler.py](../../generator/html_assembler.py)

Primary regression surface:
- [../../tests/test_html_assembler.py](../../tests/test_html_assembler.py)
- [../../tests/test_html_validator.py](../../tests/test_html_validator.py)

### 8. Per-Destination Minimums and Quality Gates Remain Mandatory

- Image minimums remain enforced per destination.
- URL quality and rejection semantics remain measurable.
- Destination-level quality failures must be isolatable even if acquisition becomes batched.

Primary references:
- [../../generator/html_validator.py](../../generator/html_validator.py)
- [../../generator/image_fetcher.py](../../generator/image_fetcher.py)
- [../requirements.md](../requirements.md)

## Recommended Focused Non-Regression Commands

- `pytest tests/test_ai_content_normalization.py -k "first_day or travel_realism or departure_aligned"`
- `pytest tests/test_url_discovery.py -k "denylist or bear or jud or fly or category or redirect or trail_like_attraction or filtered_constraints"`
- `pytest tests/test_html_assembler.py -k "marker or getting_there or schedule_preserves_structured_one_day_schedule"`
- `pytest tests/test_html_validator.py`

## Version 2 Rule

Issue #6 may change orchestration, batching, and intermediate data flow.
It must not weaken the invariants above before Version 2 is declared validated.