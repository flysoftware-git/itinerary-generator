"""
main.py — CLI entry point for the Road Trip Itinerary Generator.

Usage:
  python -m generator.main --manifest trip_manifest.yaml --output output/

Flags:
  --manifest        Path to trip manifest YAML (required)
  --output          Output directory (default: output/)
  --config          Path to config.yaml (default: config.yaml)
    --llm-provider    Override LLM provider for this run
    --llm-model       Override LLM model for this run
    --log-level       Console logging threshold (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  --dry-run         Parse + validate manifest only; no AI calls, no output
  --skip-images     Skip image fetching (useful for fast content iteration)
  --skip-events     Skip cultural events discovery
  --skip-url-discovery  Skip URL discovery (AI content only)
  --destination     Process only this destination id (repeatable)
  --verbose         Enable debug logging
"""

from __future__ import annotations
import atexit
import json
import logging, os, sys
from datetime import datetime, timezone
from pathlib import Path
import click
from generator import __version__, __template_version__

logger = logging.getLogger(__name__)
LOG_LEVEL_CHOICES = ["debug", "info", "warning", "error", "critical"]


def _extract_http_urls_from_html_text(html_text: str) -> set[str]:
    import re

    urls: set[str] = set()
    for match in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html_text, flags=re.IGNORECASE):
        candidate = str(match.group(1) or "").strip()
        if candidate.lower().startswith(("http://", "https://")):
            urls.add(candidate)
    return urls


def _read_output_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return _extract_http_urls_from_html_text(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return set()


def _domain_counts(urls: set[str]) -> dict[str, int]:
    from urllib.parse import urlparse

    counts: dict[str, int] = {}
    for url in urls:
        domain = (urlparse(url).netloc or "").lower()
        if not domain:
            continue
        counts[domain] = counts.get(domain, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _write_url_diff_report(
    *,
    output_dir: Path,
    baseline_urls: set[str],
    current_urls: set[str],
    run_id: str,
) -> Path:
    kept = sorted(baseline_urls & current_urls)
    added = sorted(current_urls - baseline_urls)
    removed = sorted(baseline_urls - current_urls)
    report = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "baseline_url_count": len(baseline_urls),
            "current_url_count": len(current_urls),
            "kept_count": len(kept),
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_total": len(added) + len(removed),
        },
        "baseline_domains": _domain_counts(baseline_urls),
        "current_domains": _domain_counts(current_urls),
        "kept": kept,
        "added": added,
        "removed": removed,
    }
    path = output_dir / "url_diff_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _write_url_diff_markdown_report(
    *,
    output_dir: Path,
    baseline_urls: set[str],
    current_urls: set[str],
    run_id: str,
) -> Path:
    kept = sorted(baseline_urls & current_urls)
    added = sorted(current_urls - baseline_urls)
    removed = sorted(baseline_urls - current_urls)

    lines: list[str] = []
    lines.append("# URL Diff Report")
    lines.append("")
    lines.append(f"- Run ID: {run_id}")
    lines.append(f"- Generated at (UTC): {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- Baseline URLs: {len(baseline_urls)}")
    lines.append(f"- Current URLs: {len(current_urls)}")
    lines.append(f"- Kept: {len(kept)}")
    lines.append(f"- Added: {len(added)}")
    lines.append(f"- Removed: {len(removed)}")
    lines.append("")

    def _append_section(title: str, values: list[str]) -> None:
        lines.append(f"## {title} ({len(values)})")
        if not values:
            lines.append("- None")
            lines.append("")
            return
        for v in values:
            lines.append(f"- {v}")
        lines.append("")

    _append_section("Added URLs", added)
    _append_section("Removed URLs", removed)
    _append_section("Kept URLs", kept)

    path = output_dir / "url_diff_report.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _append_run_ledger(ledger_path: Path, record: dict) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _filter_destinations(
    trip: dict,
    destination_ids: tuple[str, ...],
    *,
    first_destination_only: bool,
) -> None:
    destinations = list(trip.get("destinations", []))
    if destination_ids:
        destinations = [d for d in destinations if d["id"] in destination_ids]
        if not destinations:
            raise click.ClickException(f"None of {destination_ids} matched any destination id.")
    if first_destination_only and destinations:
        destinations = destinations[:1]
    trip["destinations"] = destinations


def _is_us_coordinates(lat: object, lng: object) -> bool:
    """Return True when coordinates are in US regions where NPS codes are relevant."""
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return False

    # Continental US
    if 24.0 <= lat_f <= 49.5 and -125.0 <= lng_f <= -66.5:
        return True
    # Alaska
    if 51.0 <= lat_f <= 72.0 and -170.0 <= lng_f <= -129.0:
        return True
    # Hawaii
    if 18.0 <= lat_f <= 23.0 and -161.0 <= lng_f <= -154.0:
        return True
    return False


def _write_pwa_assets(output_dir: Path, trip: dict) -> None:
        """Write manifest.webmanifest and sw.js next to generated index.html."""
        trip_meta = trip.get("trip", {}) if isinstance(trip, dict) else {}
        title = str(trip_meta.get("title", "Road Trip Itinerary") or "Road Trip Itinerary").strip()
        subtitle = str(trip_meta.get("subtitle", "Interactive road trip itinerary") or "Interactive road trip itinerary").strip()
        theme_color = str(trip_meta.get("theme_color", "#C0623E") or "#C0623E").strip()

        icon_192 = (
                "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E"
                "%3Crect width='192' height='192' rx='36' fill='%23C0623E'/%3E"
                "%3Ctext x='50%25' y='54%25' font-size='110' text-anchor='middle' dominant-baseline='middle'%3E"
                "%F0%9F%97%BA%EF%B8%8F%3C/text%3E%3C/svg%3E"
        )
        icon_512 = (
                "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'%3E"
                "%3Crect width='512' height='512' rx='96' fill='%23C0623E'/%3E"
                "%3Ctext x='50%25' y='54%25' font-size='300' text-anchor='middle' dominant-baseline='middle'%3E"
                "%F0%9F%97%BA%EF%B8%8F%3C/text%3E%3C/svg%3E"
        )

        manifest = {
                "name": title,
                "short_name": title[:24] or "Road Trip",
                "description": subtitle,
                "start_url": "./index.html",
                "scope": "./",
                "display": "standalone",
                "background_color": "#FAFAF7",
                "theme_color": theme_color,
                "orientation": "portrait-primary",
                "icons": [
                        {
                                "src": icon_192,
                                "sizes": "192x192",
                                "type": "image/svg+xml",
                                "purpose": "any maskable",
                        },
                        {
                                "src": icon_512,
                                "sizes": "512x512",
                                "type": "image/svg+xml",
                                "purpose": "any maskable",
                        },
                ],
        }
        (output_dir / "manifest.webmanifest").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
        )

        sw_js = """const CACHE = 'roadtrip-shell-v1';
const SHELL = ['./', './index.html', './manifest.webmanifest'];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') {
        return;
    }

    const reqUrl = new URL(event.request.url);
    const sameOrigin = reqUrl.origin === self.location.origin;
    const cacheableCdn =
        reqUrl.href.startsWith('https://cdn.tailwindcss.com') ||
        reqUrl.href.startsWith('https://unpkg.com/lucide@latest') ||
        reqUrl.href.startsWith('https://cdn.jsdelivr.net/npm/leaflet@1.9.4/');

    if (!sameOrigin && !cacheableCdn) {
        return;
    }

    event.respondWith(
        caches.match(event.request).then((cached) => {
            if (cached) {
                return cached;
            }
            return fetch(event.request)
                .then((response) => {
                    if (response && response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE).then((cache) => cache.put(event.request, clone));
                    }
                    return response;
                })
                .catch(() => caches.match('./index.html'));
        })
    );
});
"""
        (output_dir / "sw.js").write_text(sw_js, encoding="utf-8")


def _setup_logging(verbose: bool, log_level: str) -> str:
    selected = "debug" if verbose else (log_level or "info").lower()
    level = getattr(logging, selected.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    return selected.upper()


@click.command()
@click.option("--manifest", required=True, type=click.Path(exists=True), help="Trip manifest YAML")
@click.option("--output", default="output", show_default=True, help="Output directory")
@click.option("--config", "config_path", default="config.yaml", show_default=True, help="Config YAML")
@click.option(
    "--llm-provider",
    type=click.Choice(["openai", "anthropic", "deepseek", "gemini", "grok", "azure_openai"], case_sensitive=False),
    help="Override LLM provider for this run",
)
@click.option(
    "--environment",
    type=click.Choice(["dev", "test", "prod"], case_sensitive=False),
    help="Environment override (dev/test/prod). Optional.",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Optional path to .env file. If provided, loaded before environment resolution.",
)
@click.option("--llm-model", type=str, help="Override LLM model for this run")
@click.option("--dry-run", is_flag=True, help="Parse & validate only; no AI calls")
@click.option("--skip-images", is_flag=True, help="Skip image fetching")
@click.option("--refresh-image-cache", is_flag=True, help="Force refresh image-provider queries, bypassing local image cache")
@click.option("--skip-events", is_flag=True, help="Skip cultural events discovery")
@click.option("--skip-url-discovery", is_flag=True, help="Skip URL discovery")
@click.option("--noschedule", is_flag=True, help="Suppress schedule card rendering in output HTML")
@click.option("--destination", "destinations", multiple=True, help="Limit to specific destination ids")
@click.option("--first-destination", "first_destination_only", is_flag=True, help="Process only the first destination after any destination filtering")
@click.option(
    "--log-level",
    type=click.Choice(LOG_LEVEL_CHOICES, case_sensitive=False),
    default="info",
    show_default=True,
    help="Console logging threshold. Ignored when --verbose is set.",
)
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def main(
    manifest: str,
    output: str,
    config_path: str,
    llm_provider: str | None,
    environment: str | None,
    env_file: str | None,
    llm_model: str | None,
    dry_run: bool,
    skip_images: bool,
    refresh_image_cache: bool,
    skip_events: bool,
    skip_url_discovery: bool,
    noschedule: bool,
    destinations: tuple[str, ...],
    first_destination_only: bool,
    log_level: str,
    verbose: bool,
) -> None:
    run_started_at = datetime.now(timezone.utc)
    run_id = run_started_at.strftime("%Y%m%dT%H%M%S.%fZ")
    ledger_path = Path(output) / "dev" / "run_ledger.jsonl"
    finalized = False

    def _finalize_run(status: str, exit_code: int, error: str | None = None) -> None:
        nonlocal finalized
        if finalized:
            return
        ended_at = datetime.now(timezone.utc)
        duration_s = max(0.0, (ended_at - run_started_at).total_seconds())
        record = {
            "run_id": run_id,
            "status": status,
            "exit_code": int(exit_code),
            "error": str(error or "").strip() or None,
            "started_at_utc": run_started_at.isoformat(),
            "ended_at_utc": ended_at.isoformat(),
            "duration_seconds": round(duration_s, 3),
            "manifest": manifest,
            "output": output,
            "config": config_path,
            "environment": environment,
            "env_file": env_file,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "dry_run": bool(dry_run),
            "skip_images": bool(skip_images),
            "skip_events": bool(skip_events),
            "skip_url_discovery": bool(skip_url_discovery),
            "first_destination_only": bool(first_destination_only),
            "destinations": list(destinations),
        }
        try:
            _append_run_ledger(ledger_path, record)
        except Exception as exc:  # pragma: no cover - defensive only
            logger.warning("Failed to append run ledger entry: %s", exc)
        finalized = True

    def _finalize_if_unfinished() -> None:
        if not finalized:
            _finalize_run("terminated_without_finalize", 1, "Process exited before normal completion")

    atexit.register(_finalize_if_unfinished)

    effective_log_level = _setup_logging(verbose, log_level)
    output_dir = Path(output)

    click.echo(f"🗺  Road Trip Itinerary Generator")
    click.echo(f"   Manifest : {manifest}")
    click.echo(f"   Output   : {output_dir.resolve()}")
    click.echo(f"   Config   : {config_path}")
    click.echo(f"   Logging  : {effective_log_level}")

    if llm_provider:
        click.echo(f"   LLM      : provider override = {llm_provider.lower()}")
    if llm_model:
        click.echo(f"   LLM      : model override = {llm_model}")
    if dry_run:
        click.echo("   Mode     : DRY RUN (no AI calls)")
    click.echo()

    # ── Optional .env loading ───────────────────────────────────────────────
    if env_file:
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            click.echo(f"   EnvFile  : loaded from {env_file}")
        except Exception as exc:
            click.echo(f"   EnvFile  : failed to load ({exc})", err=True)

    # ── Stage 1: Parse & validate manifest ──────────────────────────────────
    click.echo("Stage 1/6 — Parsing manifest…")
    from generator.parser import ManifestParser
    parser = ManifestParser()
    trip = parser.load(manifest)

    # ── Hybrid environment selection ─────────────────────────────────────────
    env_from_manifest = trip.get("trip", {}).get("environment")
    env_from_cli = environment
    env_from_env = os.environ.get("ENVIRONMENT")

    environment_selected = (
        (env_from_cli or env_from_manifest or env_from_env or "dev").lower()
    )

    click.echo(
        click.style("   Env      : ", fg="cyan") +
        click.style(environment_selected, fg="green")
    )

    # Add environment tag to logger name
    logger.name = f"{logger.name}[{environment_selected}]"

    # Output directory behavior:
    # - default: write directly to --output path
    # - only nest by environment when explicitly requested via CLI
    if env_from_cli:
        output_dir = Path(output) / environment_selected
    else:
        output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "index.html"
    baseline_output_urls = _read_output_urls(output_file)

    click.echo()

    try:
        _filter_destinations(
            trip,
            destinations,
            first_destination_only=first_destination_only,
        )
    except click.ClickException as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        _finalize_run("input_error", 1, str(exc))
        sys.exit(1)

    click.echo(f"  ✓ {len(trip['destinations'])} destination(s) loaded")

    if dry_run:
        click.echo("\n✅ Dry run complete — manifest valid.")
        _finalize_run("dry_run_completed", 0)
        return

    # ── Stage 2: Geocode + auto-enrich ──────────────────────────────────────
    click.echo("Stage 2/6 — Geocoding & enrichment…")
    from generator.geocoder import Geocoder
    from generator.nps_resolver import NPSResolver
    from concurrent.futures import ThreadPoolExecutor, as_completed
    geo = Geocoder()
    nps = NPSResolver()
    # Geocoding is sequential (Nominatim ToS: 1 req/sec)
    for dest in trip["destinations"]:
        lat, lng = geo._geocode(dest["name"])
        dest["lat"] = lat
        dest["lng"] = lng

    # Optional departure/return geocoding for full-route maps and first-card routing context.
    departure_name = trip.get("trip", {}).get("departure")
    return_name = trip.get("trip", {}).get("return")
    if departure_name:
        dlat, dlng = geo._geocode(departure_name)
        trip["trip"]["departure_lat"] = dlat
        trip["trip"]["departure_lng"] = dlng
    if return_name:
        rlat, rlng = geo._geocode(return_name)
        trip["trip"]["return_lat"] = rlat
        trip["trip"]["return_lng"] = rlng
    # NPS resolution is independent — run in parallel
    def _resolve_nps(dest: dict) -> None:
        if not _is_us_coordinates(dest.get("lat"), dest.get("lng")):
            dest["nps_park_code"] = None
            return
        dest["nps_park_code"] = nps.resolve(dest["name"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_resolve_nps, d): d for d in trip["destinations"]}
        for f in as_completed(futures):
            f.result()
    for dest in trip["destinations"]:
        click.echo(f"  \u2713 {dest['name']}: lat={dest['lat']:.4f} lng={dest['lng']:.4f} nps={dest['nps_park_code']}")

    # ── Stage 3: AI content generation ──────────────────────────────────────
    click.echo("Stage 3/6 — AI content generation…")
    from generator.llm_client import MultiLLMClient
    from generator.ai_content import AIContentGenerator
    from generator.costs import print_cost_summary, summarize_from_usage
    llm_overrides = dict(trip.get("trip", {}).get("llm", {}))

    # ── Hybrid provider selection ───────────────────────────────────────────
    provider_from_manifest = trip.get("trip", {}).get("llm_provider")
    provider_from_cli = llm_provider
    provider_from_env = os.environ.get("LLM_PROVIDER")
    provider_selected = (provider_from_cli or provider_from_manifest or provider_from_env)

    if trip.get("trip", {}).get("llm_provider"):
        llm_overrides["provider"] = trip["trip"].get("llm_provider")
    if trip.get("trip", {}).get("llm_features"):
        llm_overrides["features"] = trip["trip"].get("llm_features")
    if llm_provider:
        llm_overrides["provider"] = llm_provider.lower()
    elif provider_selected:
        llm_overrides["provider"] = provider_selected.lower()

    click.echo(
        click.style("   LLM      : provider = ", fg="cyan") +
        click.style(llm_overrides.get("provider"), fg="green")
    )

    if llm_model:
        llm_overrides["model"] = llm_model

    # ── Optional environment-aware config merging ───────────────────────────
    try:
        import yaml
        with Path(config_path).open(encoding="utf-8") as f:
            cfg_full = yaml.safe_load(f) or {}
        if environment_selected in cfg_full:
            env_cfg = cfg_full[environment_selected]
            ai_env_cfg = env_cfg.get("ai", {})
            for key, val in ai_env_cfg.items():
                llm_overrides.setdefault(key, val)
    except Exception:
        pass
    llm_client = MultiLLMClient(
        config_path=config_path,
        llm_overrides=llm_overrides,
    )

    ai_gen = AIContentGenerator(config_path, llm_client=llm_client)
    ai_gen.generate_all(trip)

    if noschedule:
        for dest in trip.get("destinations", []):
            if "ai_content" in dest and isinstance(dest["ai_content"], dict):
                dest["ai_content"]["possible_daily_schedule"] = []

    click.echo(f"  ✓ AI content generated for {len(trip['destinations'])} destination(s)")

    # ── Stages 4 + 5a + 5b: run concurrently (all independent of each other) ─
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    def _run_events() -> None:
        if not skip_events:
            click.echo("Stage 4/6 — Cultural events discovery…")
            from generator.cultural_events import CulturalEventsDiscoverer
            CulturalEventsDiscoverer(config_path, llm_client=llm_client).discover(trip)
            click.echo("  ✓ Cultural events resolved")
        else:
            click.echo("Stage 4/6 — Cultural events discovery SKIPPED")

    def _run_images() -> None:
        if not skip_images:
            click.echo("Stage 5/6 — Fetching images…")
            from generator.image_fetcher import ImageFetcher
            ImageFetcher(
                config_path,
                output_dir=output_dir / "images",
                force_refresh=refresh_image_cache,
            ).fetch_all(trip)
            total = sum(len(d.get("images", [])) for d in trip["destinations"])
            click.echo(f"  ✓ {total} images fetched")
        else:
            click.echo("Stage 5/6 — Image fetching SKIPPED")
            for dest in trip["destinations"]:
                dest.setdefault("images", [])

    def _run_urls() -> None:
        if not skip_url_discovery:
            click.echo("Stage 5b — URL discovery…")
            from generator.url_discovery import URLDiscoverer
            url_discoverer = URLDiscoverer(config_path, llm_client=llm_client)
            url_discoverer.discover_all(trip)
            url_discoverer.audit_discovered_urls(trip)
            click.echo("  ✓ URLs discovered and verified")
        else:
            click.echo("Stage 5b — URL discovery SKIPPED")

    click.echo("Stages 4–5b — Cultural events, images, and URL discovery (parallel)…")
    with ThreadPoolExecutor(max_workers=3) as _stage_pool:
        _stage_futures = [_stage_pool.submit(fn) for fn in (_run_events, _run_images, _run_urls)]
        for _f in _as_completed(_stage_futures):
            _f.result()

    # ── Stage 6: Assemble HTML ───────────────────────────────────────────────
    click.echo("Stage 6/6 — Assembling HTML…")
    from generator.html_assembler import HTMLAssembler
    trip["_meta"] = {
        "generator_version": __version__,
        "template_version": __template_version__,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": environment_selected,
        "llm": {
            "provider": llm_client.provider,
            "model": llm_client.model,
            "usage": llm_client.usage_summary(),
        },
    }
    assembler = HTMLAssembler(config_path)
    html = assembler.assemble(trip)

    output_file.write_text(html, encoding="utf-8")
    click.echo(f"  ✓ index.html written ({output_file.stat().st_size:,} bytes)")

    current_output_urls = _read_output_urls(output_file)
    url_diff_report_path = _write_url_diff_report(
        output_dir=output_dir,
        baseline_urls=baseline_output_urls,
        current_urls=current_output_urls,
        run_id=run_id,
    )
    url_diff_markdown_path = _write_url_diff_markdown_report(
        output_dir=output_dir,
        baseline_urls=baseline_output_urls,
        current_urls=current_output_urls,
        run_id=run_id,
    )
    click.echo(f"  ✓ URL diff report: {url_diff_report_path}")
    click.echo(f"  ✓ URL diff summary: {url_diff_markdown_path}")

    _write_pwa_assets(output_dir, trip)
    click.echo("  ✓ PWA assets written (manifest.webmanifest, sw.js)")

    # ── Validate ─────────────────────────────────────────────────────────────
    from generator.html_validator import HTMLValidator
    from generator.report_writer import ReportWriter
    validator = HTMLValidator(config_path)
    report = validator.validate(output_file, trip)
    report_path = ReportWriter(output_dir).write(report)
    click.echo(f"  ✓ Validation report: {report_path}")

    predicted_cost, actual_cost = summarize_from_usage(trip.get("_meta", {}).get("llm", {}).get("usage", {}))
    print_cost_summary(
        model=trip.get("_meta", {}).get("llm", {}).get("model", llm_client.model),
        manifest_path=manifest,
        predicted_usd=predicted_cost,
        actual_usd=actual_cost,
        environment=environment_selected,
    )
    usage_models = trip.get("_meta", {}).get("llm", {}).get("usage", {}).get("models", [])
    if usage_models:
        click.echo("  Usage breakdown by provider/model:")
        for row in usage_models:
            click.echo(
                f"    - {row.get('provider')}/{row.get('model')}: "
                f"calls={row.get('calls', 0)} tokens={row.get('total_tokens', 0)} "
                f"est=${row.get('estimated_cost_usd', 0.0):.4f}"
            )

    if not report["valid"]:
        click.echo(f"\n⚠️  {report['error_count']} validation error(s) found:", err=True)
        for e in report["errors"]:
            click.echo(f"   ✗ {e}", err=True)
        _finalize_run("validation_failed", 2, f"{report['error_count']} validation errors")
        sys.exit(2)

    _finalize_run("completed", 0)
    click.echo(f"\n✅ Done! Open {output_file.resolve()} in your browser.")


if __name__ == "__main__":
    main()
