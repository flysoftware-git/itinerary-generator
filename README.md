# Itinerary Generator

A Python CLI tool that transforms a minimal YAML trip manifest into a single self-contained `index.html` travel itinerary — visually and functionally identical to the [Southwest Road Trip Itinerary v2.5](https://swiftsure-pro.github.io/Travel-apps/sw/prod/), with AI-generated content tailored to any destinations worldwide.

Write a manifest in minutes. Get a polished, deployable trip guide with:
- AI-generated environment descriptions, attraction writeups, en-route stops, and daily schedules
- AI-generated "What to Know" briefing per destination (customs, weather patterns, transport quirks, safety, photography, crowd timing, etiquette)
- Auto-discovered cultural events (via Search + AI synthesis — never hallucinated)
- 5–6 restaurant recommendations per destination with cuisine and price diversity
- 2–4 scenic drives/viewpoints per destination (fully AI-discovered, not user-seeded)
- Verified URLs for every attraction, restaurant, and stop
- Semantic URL scoring to choose the best candidate link (not first-valid)
- Destination images from NPS API and Wikimedia Commons
- Interactive Leaflet map with auto-generated Google Maps overview link
- Per-destination Attractions Map link for viewing recommended stops in Google Maps
- Footer includes generator version/timestamp plus GitHub issue links for broken-link reports and general itinerary feedback

## Quick Start

### 1. Clone

```bash
git clone https://github.com/flysoftware-git/itinerary-generator.git
cd itinerary-generator
```

### 2. Bootstrap environment (Windows)

PowerShell:

```powershell
.\scripts\bootstrap.ps1
```

Batch wrapper:

```bat
scripts\bootstrap.bat
```

Optional flags:

```powershell
# Rebuild venv from scratch
.\scripts\bootstrap.ps1 -Recreate

# Skip tests during setup
.\scripts\bootstrap.ps1 -SkipTests
```

### 3. Set up environment variables

`scripts/bootstrap.ps1` auto-creates `.env` from `.env.example` if missing.
Then edit `.env` with your API keys.

Required variables:
| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key (when `ai.provider: openai`) |
| `ANTHROPIC_API_KEY` | Anthropic API key (when `ai.provider: anthropic`) |
| `DEEPSEEK_API_KEY` | DeepSeek API key (when `ai.provider: deepseek`) |
| `GEMINI_API_KEY` | Gemini API key (when `ai.provider: gemini`) |
| `GROK_API_KEY` | Grok API key (when `ai.provider: grok`) |
| `XAI_API_KEY` | xAI Grok API key |
| `XAI_CONTENT_TIMEOUT_SECONDS` | Pins the Grok content-generation read timeout, skipping the size-based calculation below |
| `XAI_CONNECT_TIMEOUT_SECONDS` | Connect timeout for Grok content generation (default 10) |
| `XAI_OUTPUT_TOKENS_PER_SECOND` | Assumed generation rate used to size the read timeout (default 25) |
| `XAI_CONTENT_TIMEOUT_FLOOR_SECONDS` / `_CEILING_SECONDS` | Bounds on the computed read timeout (defaults 120 / 600) |

Optional:
| Variable | Description |
|---|---|
| `NPS_API_KEY` | NPS API key (defaults to `DEMO_KEY`, which is rate-limited) |
| `OPENAI_MODEL` | Default OpenAI model override (e.g. `gpt-4o-mini`) |
| `AZURE_OPENAI_*` | Legacy Azure OpenAI compatibility variables |

### 4. Write your manifest

```yaml
trip:
  title: "Pacific Coast Highway"
  subtitle: "September 2026 — California"
  theme_color: "#2E6B8A"
  default_day_start_time: "10:00 AM"
  default_daily_activity_hours: 5
  llm:
    provider: "openai"
    model: "gpt-4o-mini"
    temperature: 0.6
    max_tokens: 4096

destinations:
  - id: sf
    name: "San Francisco, California"
    dates: "September 5–7, 2026"
    schedule_start_time: "9:30 AM"      # optional per-destination override
    daily_activity_hours: 6              # optional per-destination override
    planning_links:
      - label: "Hotel Reservation"
        url: "https://..."
    seeds:
      - "Golden Gate Bridge"
      - "Alcatraz"
      - "Lombard Street"

  - id: bigsur
    name: "Big Sur, California"
    dates: "September 7–9, 2026"
    planning_links:
      - label: "Campsite Reservation"
        url: "https://www.recreation.gov/..."
    seeds:
      - "McWay Falls"
      - "Bixby Creek Bridge"
```

Seeds are **name hints only** — attractions, hikes, or experiences you specifically want included. The AI discovers scenic drives, cultural events, and restaurants independently.

Schedule controls:
- `trip.default_day_start_time` sets the default daily planning start anchor.
- `trip.default_daily_activity_hours` sets the default activity-time budget used for multi-activity schedule packing.
- `destination.schedule_start_time` and `destination.daily_activity_hours` override those defaults for a specific stop.

### 5. Generate

```bash
python -m generator.main --manifest trip_manifest.yaml --output output/
```

Your itinerary is at `output/index.html` by default. If you explicitly pass `--environment`, output is nested at `output/<environment>/index.html`.

Each run also writes `url_diff_report.json` and `url_diff_report.md` next to the generated HTML. These compare links from the pre-run output HTML to the newly generated HTML and report kept, added, and removed URLs so review can focus on true link changes.

---

## CLI Options

```
python -m generator.main [OPTIONS]

  --manifest PATH          Trip manifest YAML (required)
  --output PATH            Output directory [default: output/]
  --config PATH            Config YAML [default: config.yaml]
  --llm-provider [openai|anthropic|deepseek|gemini|grok|azure_openai]
                           Override LLM provider for this run
  --llm-model TEXT         Override LLM model for this run
  --environment [dev|eval|prod]
                           Optional environment override (also enables output/<environment>/ nesting)
  --log-level [debug|info|warning|error|critical]
                           Console logging threshold (`--verbose` overrides this to DEBUG)
  --dry-run                Parse & validate manifest only; no AI calls
  --skip-images            Skip image fetching (faster iteration)
  --refresh-image-cache    Force refresh image provider queries (bypass local image cache)
  --skip-events            Skip cultural events discovery
  --skip-url-discovery     Skip URL discovery (AI content only)
  --notrails               Disable trail link discovery and omit trail links
  --trails / --no-trails   Force trails on/off, overriding trails.enabled
  --events / --no-events   Force cultural events on/off
  --en-route / --no-en-route       Force en-route stops on/off
  --restaurants / --no-restaurants Force restaurants on/off
  --alltrails-source [direct-link-batch|search|apify-single-call]
                           AllTrails source for trail-like attractions (default: direct-link-batch)
  --attraction-source [search|direct-link-batch]
                           Source for non-trail attractions
  --restaurant-source [search|direct-link-batch]
                           Source for restaurant links
  --en-route-source [search|direct-link-batch]
                           Source for en-route stop links
  --alltrails-apify-actor-id TEXT
                           Optional Apify actor id override for apify-single-call mode
  --destination TEXT       Limit to specific destination id (repeatable)
  --verbose                Enable debug logging
```

**Examples:**

```bash
# Validate manifest only
python -m generator.main --manifest trip.yaml --dry-run

# Generate content for one destination only
python -m generator.main --manifest trip.yaml --destination zion

# Show only warnings and errors on the console
python -m generator.main --manifest trip.yaml --log-level warning

# Fast iteration (skip images and events)
python -m generator.main --manifest trip.yaml --skip-images --skip-events

# Disable trail links entirely
python -m generator.main --manifest trip.yaml --notrails

# Optional categories default OFF in config.yaml because they are the priced
# ones. Turn them on for a single run without editing config:
python -m generator.main --manifest manifests/alpine_grouped.yaml     --trails --events --en-route --first-destination

# Use one Apify call per destination for trail-like link sourcing
python -m generator.main --manifest trip.yaml --alltrails-source apify-single-call

# Use direct-link batch mode for attractions and restaurants too
python -m generator.main --manifest trip.yaml --attraction-source direct-link-batch --restaurant-source direct-link-batch

# Use direct-link batch mode for en-route stop discovery
python -m generator.main --manifest trip.yaml --en-route-source direct-link-batch

# Re-run with fresh image-provider lookup (ignore local cache index)
python -m generator.main --manifest trip.yaml --refresh-image-cache

# Test with a different provider without editing config.yaml/manifest
python -m generator.main --manifest trip.yaml --llm-provider anthropic

# Test provider+model combination from CLI
python -m generator.main --manifest trip.yaml --llm-provider openai --llm-model gpt-4o-mini

# Force full debug output regardless of --log-level
python -m generator.main --manifest trip.yaml --log-level warning --verbose
```

Logging behavior:
- Default console threshold is `INFO`.
- Use `--log-level warning` to show only warnings and errors.
- `--verbose` forces `DEBUG` logging and takes precedence over `--log-level`.

---

## Pipeline

```
manifest.yaml
    │
    ▼  Stage 1: Parse & Validate
       • Schema validation (jsonschema)
       • Seed URL rejection
       • Planning link HTTP verification
    │
    ▼  Stage 2: Auto-Enrich
       • Geocoding via Nominatim
       • NPS park code detection (US-coordinate destinations only)
       • Google Maps URL auto-generation
       • Weather links: weather.gov (US) or global Weather.com fallback
    │
     ▼  Stage 3: AI Content (Configured LLM)
       • Environment, attractions, en-route stops, schedule
       • What-to-Know briefing per destination
       • Scenic drives & viewpoints (fully AI-discovered)
       • Cultural events (Grok Search + AI synthesis)
    │
     ▼  Stage 4: URL Discovery (Grok Search)
       • NPS.gov filter for park attractions
       • Two-pass restaurant strategy (Google Maps → TripAdvisor)
       • 4-variant fallback per item
       • Semantic candidate scoring + relevance verification
    │
    ▼  Stage 5: Images
       • NPS API (for national parks)
       • Wikimedia Commons (all destinations)
       • 4-attempt fallback on failure
       • Hard fail if < 2 images per destination
    │
    ▼  Stage 6: Assemble + Validate
       • SHA-256 template checksum verification
       • HTML assembly via Python strings
       • Div balance, script isolation, drive key checks
       • JSON validation report
    │
    ▼
output/index.html   ← Deploy to GitHub Pages
```

---

## Manifest Specification

See [docs/requirements.md](docs/requirements.md) for the full requirements specification (currently v2.2) including:
- Complete manifest schema
- AI content JSON schemas
- URL discovery strategy
- Image pipeline details
- Configuration reference

---

## Testing

```powershell
.\venv\Scripts\python.exe -m pytest tests -v
```

Test fixtures in `tests/fixtures/` include sample manifests, AI outputs, Bing results, and NPS API responses for offline testing.

---

## Template Integrity

The HTML template (`templates/v2.5_template.html`) is frozen and checksum-verified on every run. The filename names the 2.5 *family* and does not change on a revision; the exact version is `generator.__template_version__`, recorded against its checksum in `templates/template_versions.json`, and a template edit that does not move it fails the test suite. The SHA-256 hash is stored in `templates/checksums.txt`. A mismatch causes an immediate hard failure — the template may not be modified without regenerating the checksum.

To update the template checksum after an intentional template change:

```bash
python -c "
import hashlib
t = open('templates/v2.5_template.html', encoding='utf-8').read()
h = hashlib.sha256(t.encode()).hexdigest()
open('templates/checksums.txt', 'w').write(h + '  templates/v2.5_template.html\n')
print('Checksum updated:', h[:16] + '...')
"
```

---

## Configuration

Edit `config.yaml` to tune AI behavior, image counts, and URL discovery:

```yaml
ai:
  temperature: 0.7          # LLM temperature (0.0–1.0)
  max_tokens: 3000          # Max tokens per AI response

images:
  min_per_destination: 2    # Hard fail if fewer verified images
  max_per_destination: 4    # Max images fetched per destination
  cache_ttl_hours: 168      # Reuse image candidate cache for iterative runs

url_discovery:
  max_fallback_attempts: 4  # Query variants tried per item
```

---

## Output Structure

```
output/
├── index.html              ← Self-contained trip itinerary
├── images/
│   └── {md5hash}.jpg       ← Downloaded destination photos
└── validation_report.json  ← Post-assembly validation results

.cache/
└── images/
  └── cache_index.json    ← Local image-candidate cache for iterative regeneration
```

---

## Example Manifest (Southwest Road Trip)

`trip_manifest.yaml` in the project root is the reverse-engineered manifest for the Southwest Road Trip v2.5 (Zion → Bryce → Capitol Reef → Moab → Telluride → Pagosa Springs → Santa Fe). Use it for testing and comparing generator output against the hand-crafted v2.5 reference.

---

## Nested Folder Cleanup (Windows)

If you accidentally extracted or cloned the repo into itself (for example `itinerary-generator/itinerary-generator`), keep one canonical root and remove the duplicate copy.

Safe process:

```powershell
# 1) See current status
git status --short

# 2) Compare duplicate folder contents (optional)
Get-ChildItem .\itinerary-generator -Recurse | Select-Object FullName

# 3) Remove accidental nested copy if not needed
Remove-Item -Recurse -Force .\itinerary-generator

# 4) Verify tree is clean
git status --short
```

If the nested folder has unique files you need, move them into the root before deletion.

---

## License

MIT — see [LICENSE](LICENSE)
