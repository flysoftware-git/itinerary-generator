from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generator.url_validator import URLValidator


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if str(tag or "").lower() != "a":
            return
        for key, value in attrs:
            if str(key or "").lower() != "href" or not isinstance(value, str):
                continue
            candidate = value.strip()
            if candidate.lower().startswith(("http://", "https://")):
                self.urls.add(candidate)


def _extract_http_links(index_html_path: Path) -> list[str]:
    parser = _HrefParser()
    parser.feed(index_html_path.read_text(encoding="utf-8", errors="ignore"))
    return sorted(parser.urls)


class _RenderedCoverageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._contexts: list[dict[str, object]] = []
        self.missing_counts = {
            "attractions": 0,
            "restaurants": 0,
            "en_route_stops": 0,
        }

    @staticmethod
    def _coverage_bucket(attrs: list[tuple[str, str | None]]) -> str | None:
        class_value = ""
        for key, value in attrs:
            if str(key or "").lower() == "class" and isinstance(value, str):
                class_value = value
                break
        classes = set(class_value.split())
        if "attr-item" in classes and "attr-drive-item" not in classes:
            return "attractions"
        if "rest-item" in classes:
            return "restaurants"
        if "stop-card" in classes:
            return "en_route_stops"
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = str(tag or "").lower()
        if lowered == "div":
            for context in self._contexts:
                context["depth"] = int(context.get("depth", 0) or 0) + 1
            bucket = self._coverage_bucket(attrs)
            if bucket:
                self._contexts.append({"bucket": bucket, "depth": 1, "has_link": False})
            return

        if lowered == "a":
            href = ""
            for key, value in attrs:
                if str(key or "").lower() == "href" and isinstance(value, str):
                    href = value.strip()
                    break
            if href.lower().startswith(("http://", "https://")):
                for context in self._contexts:
                    context["has_link"] = True

    def handle_endtag(self, tag: str) -> None:
        if str(tag or "").lower() != "div":
            return
        for context in reversed(self._contexts):
            context["depth"] = int(context.get("depth", 0) or 0) - 1
        while self._contexts and int(self._contexts[-1].get("depth", 0) or 0) <= 0:
            finished = self._contexts.pop()
            if not bool(finished.get("has_link")):
                bucket = str(finished.get("bucket", "") or "")
                if bucket in self.missing_counts:
                    self.missing_counts[bucket] += 1


def _count_rendered_items_without_links(index_html_path: Path) -> dict[str, int]:
    parser = _RenderedCoverageParser()
    parser.feed(index_html_path.read_text(encoding="utf-8", errors="ignore"))
    return dict(parser.missing_counts)


def _verify_links(urls: Iterable[str], timeout: int) -> list[dict[str, object]]:
    validator = URLValidator(timeout=timeout)
    results: list[dict[str, object]] = []
    for url in urls:
        ok, status, _text = validator.get_text(url, timeout=timeout)
        final_url = str(getattr(validator, "_last_final_url", "") or url)
        results.append(
            {
                "url": url,
                "final_url": final_url,
                "ok": bool(ok),
                "status": status,
            }
        )
    return results


def _looks_like_single_result_url(url: str) -> bool:
    """
    Heuristic for "single valid result" links.

    Rules are intentionally strict for major search surfaces, especially
    map/search URLs that can land on multiple candidates.
    """
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False

    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    query = parse_qs(parsed.query or "")

    # Generic web search pages are not single-result destinations.
    if host.endswith("google.com") and path.startswith("/search"):
        return False
    if host.endswith("bing.com") and path.startswith("/search"):
        return False
    if host.endswith("duckduckgo.com") and path.startswith("/") and "q" in query:
        return False

    # Generic TripAdvisor/Yelp search/listing pages are not single-result pages.
    if host.endswith("tripadvisor.com") and ("/search" in path or "/restaurants-" in path):
        return False
    if host.endswith("yelp.com") and path.startswith("/search"):
        return False

    # Google Maps search URLs are single-result only when query_place_id is present.
    if "google.com" in host and path.startswith("/maps/search"):
        return bool(query.get("query_place_id"))

    # directions and place URLs are acceptable target pages.
    if "google.com" in host and path.startswith("/maps/dir"):
        return True
    if "google.com" in host and path.startswith("/maps/place"):
        return True

    # Most non-search URLs are assumed to be single-destination pages.
    return True


def _load_dotenv_file(env_file: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not env_file.exists():
        return loaded

    for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        loaded[key] = value
    return loaded


def _resolve_index_path(output_dir: Path, environment: str) -> Path:
    if environment:
        return output_dir / environment / "index.html"
    return output_dir / "index.html"


def _run_generation(manifest: Path, output_dir: Path, generator_args: list[str], env: dict[str, str] | None = None) -> int:
    cmd = [
        sys.executable,
        "-m",
        "generator.main",
        "--manifest",
        str(manifest),
        "--output",
        str(output_dir),
        *generator_args,
    ]
    print("\nGENERATION_CMD:", " ".join(cmd))
    completed = subprocess.run(cmd, check=False, env=env)
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate itinerary output and verify every external link in index.html. "
            "Retries generation until invalid-link threshold is met."
        )
    )
    parser.add_argument("--manifest", required=True, help="Path to manifest YAML")
    parser.add_argument("--output", default="output/link_verification", help="Output directory for generation")
    parser.add_argument("--environment", default="", help="Environment subdirectory if generator is run with --environment")
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum generation+validation attempts")
    parser.add_argument("--max-invalid", type=int, default=0, help="Allowed invalid-link count (default 0)")
    parser.add_argument("--timeout", type=int, default=10, help="Per-link HTTP timeout seconds")
    parser.add_argument("--env-file", default=".env", help="Path to .env file used to populate generation environment")
    parser.add_argument(
        "--require-single-result",
        action="store_true",
        help="Fail links that resolve to generic/search result pages instead of a single destination",
    )
    parser.add_argument(
        "--require-link-coverage",
        action="store_true",
        help="Fail when rendered attractions, restaurants, or en-route stops appear without hyperlinks",
    )
    parser.add_argument(
        "--allow-nonzero-generator-exit-when-index-exists",
        action="store_true",
        help=(
            "Continue to link verification even when generator exits non-zero, "
            "as long as index.html exists"
        ),
    )
    parser.add_argument(
        "--generator-arg",
        action="append",
        default=[],
        help="Extra argument to pass through to generator.main (repeatable)",
    )
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Do not run generator.main; validate links from existing index.html only",
    )
    args = parser.parse_args()

    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"ERROR: manifest not found: {manifest}")
        return 2

    if args.max_attempts < 1:
        print("ERROR: --max-attempts must be >= 1")
        return 2

    if args.max_invalid < 0:
        print("ERROR: --max-invalid must be >= 0")
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    env_file = Path(str(args.env_file or "")).expanduser()
    loaded_env = _load_dotenv_file(env_file)
    generation_env = os.environ.copy()
    for key, value in loaded_env.items():
        generation_env.setdefault(key, value)

    print(
        json.dumps(
            {
                "env_file": str(env_file),
                "env_file_found": env_file.exists(),
                "loaded_env_var_count": len(loaded_env),
                "require_single_result": bool(args.require_single_result),
                "allow_nonzero_generator_exit_when_index_exists": bool(args.allow_nonzero_generator_exit_when_index_exists),
            },
            indent=2,
        )
    )

    for attempt in range(1, args.max_attempts + 1):
        print(f"\n=== ATTEMPT {attempt}/{args.max_attempts} ===")
        rc = 0
        if not bool(args.skip_generation):
            rc = _run_generation(
                manifest=manifest,
                output_dir=output_dir,
                generator_args=list(args.generator_arg),
                env=generation_env,
            )
        else:
            print("GENERATION_SKIPPED: validating existing output only")
        index_html_path = _resolve_index_path(output_dir=output_dir, environment=str(args.environment or "").strip())
        if rc != 0:
            if not (bool(args.allow_nonzero_generator_exit_when_index_exists) and index_html_path.exists()):
                print(f"GENERATION_FAILED: exit_code={rc}")
                return rc
            print(
                f"GENERATION_NONZERO_CONTINUED: exit_code={rc}, using existing output for link verification: {index_html_path}"
            )

        if not index_html_path.exists():
            print(f"ERROR: expected output not found: {index_html_path}")
            return 3

        urls = _extract_http_links(index_html_path)
        results = _verify_links(urls=urls, timeout=int(args.timeout))
        invalid: list[dict[str, object]] = []
        for row in results:
            is_live = bool(row.get("ok"))
            if not is_live:
                row["invalid_reason"] = "http_unavailable"
                invalid.append(row)
                continue

            if args.require_single_result:
                single_result = _looks_like_single_result_url(str(row.get("final_url", "") or row.get("url", "")))
                row["single_result"] = bool(single_result)
                if not single_result:
                    row["invalid_reason"] = "not_single_result"
                    invalid.append(row)

        coverage_missing = _count_rendered_items_without_links(index_html_path)
        coverage_missing_total = sum(int(value or 0) for value in coverage_missing.values())
        if args.require_link_coverage and coverage_missing_total > 0:
            for bucket, count in coverage_missing.items():
                if int(count or 0) <= 0:
                    continue
                invalid.append(
                    {
                        "url": f"rendered:{bucket}",
                        "final_url": "",
                        "ok": False,
                        "status": "missing_link_coverage",
                        "invalid_reason": "missing_rendered_link",
                        "count": int(count or 0),
                    }
                )

        summary = {
            "attempt": attempt,
            "index_html": str(index_html_path),
            "total_urls": len(results),
            "valid_urls": len(results) - len(invalid),
            "invalid_urls": len(invalid),
            "max_invalid_allowed": int(args.max_invalid),
            "coverage_missing": coverage_missing,
        }
        print(json.dumps(summary, indent=2))

        if invalid:
            print("INVALID_URLS_START")
            for row in invalid:
                print(
                    f"{row.get('status')}\t{row.get('invalid_reason')}\t{row.get('url')}\tfinal={row.get('final_url')}"
                )
            print("INVALID_URLS_END")

        if len(invalid) <= int(args.max_invalid):
            print("SUCCESS: invalid-link threshold satisfied")
            return 0

    print("FAIL: invalid-link threshold not satisfied within max attempts")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
