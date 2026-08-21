@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM REFERENCE COPY. This example writes to a local OUTPUT_ROOT and does NOT
REM publish anywhere -- no git add/commit/push. The working copy used for real
REM runs lives outside the repo and does publish to the Travel-apps Pages site.
REM
REM If you ever add a publish step here, it MUST refuse to publish anything but
REM prod. Only prod output is privacy-redacted (_resolve_privacy_redaction
REM redacts in prod only), so dev/eval builds carry private planning links and
REM lodging/booking confirmation details verbatim, and Travel-apps is a public
REM repo whose Pages workflow uploads path: '.', the entire repository:
REM
REM     if /I not "%ENVIRONMENT%"=="prod" (
REM         echo [INFO] Environment is "%ENVIRONMENT%", not prod -- skipping publish.
REM         goto :end
REM     )
REM
REM Note also that generator.main writes to output/<environment>/, not to
REM OUTPUT_DIR itself -- a publish step checking OUTPUT_DIR/validation_report.json
REM will never find it and will silently skip on every run.
REM
REM LOG LEVEL. Prefer --log-level warning for routine runs. A real 10-stop
REM run emits ~1700 INFO lines, ~1650 of them per-item url_discovery
REM chatter, which buries the ~30 warnings worth reading. Progress output
REM is click.echo rather than logging, so it survives at any level.
REM Re-run with --log-level info for an investigation.
REM
REM ENVIRONMENT POSTURES. Each environment is a posture, not just an output
REM folder. Three separate mechanisms decide these, so the combination is easy
REM to get wrong:
REM
REM   dev / eval   redaction OFF (planning links, lodging names, confirmation
REM                numbers and travel legs render verbatim); never published.
REM   prod         redaction ON; publishable.
REM
REM Bookings are NOT ingested by this script in any environment. Ingestion
REM reads a mailbox, spends LLM calls per message, and is one-way -- it flags
REM mail read and files it. A build that silently depends on a reachable
REM mailbox fails for reasons unrelated to the trip. It is a separate step:
REM   python scripts/ingest_reservations.py --manifest <path> --env-file <path>
REM which writes <manifest_stem>.reservations.yaml beside the manifest; the
REM build merges that file if it exists. Absent, the build is still valid and
REM simply renders no booking cards -- which is invisible in the output, so a
REM real runner should say so before generating.
REM ─────────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion

REM ────────────────────────────────────────────────────────────────
REM Correct repo root (match actual casing exactly)
REM NOTE: this is a LOCAL FILESYSTEM PATH, not the repo name. The GitHub repo
REM was renamed to itinerary-generator on 2026-08-21; the working directory
REM deliberately was NOT renamed, because the venv's Scripts\*.exe shims embed
REM absolute paths and would need rebuilding. Do not "fix" this to match.
REM ────────────────────────────────────────────────────────────────
set REPO_ROOT=C:\Dev\road-trip-generator

REM Path to the directory where this BAT file lives (scripts/)
set SCRIPT_ROOT=%~dp0

REM Program name from argument
if "%1"=="" (
    echo You must specify a program name
    echo Example: run-trip.bat SW2026 dev
    echo Example with custom output dir: run-trip.bat SW2026 dev "C:\Temp\RoadTripRuns\SW2026-dipstick"
    pause
    exit /b
)
set PROGRAM=%1

REM Optional environment (dev/test/prod)
set ENVIRONMENT=%2

REM Optional absolute or relative custom output directory
set CUSTOM_OUTPUT_DIR=%3

REM Output root
set OUTPUT_ROOT=C:\Users\bryan\OneDrive\RoadTripOutput
set OUTPUT_DIR=%OUTPUT_ROOT%\%PROGRAM%
if not "%CUSTOM_OUTPUT_DIR%"=="" (
    set OUTPUT_DIR=%CUSTOM_OUTPUT_DIR%
)

if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

echo Run date: %DATE% %TIME% > "%OUTPUT_DIR%\version.txt"

REM Validate repo root
if not exist "%REPO_ROOT%" (
    echo [ERROR] Repo root not found: %REPO_ROOT%
    pause
    exit /b 1
)

REM Validate venv
if not exist "%REPO_ROOT%\venv\Scripts\activate.bat" (
    echo [ERROR] venv not found at: %REPO_ROOT%\venv\Scripts\activate.bat
    pause
    exit /b 1
)

REM Validate manifest in scripts directory
set MANIFEST_PATH=%SCRIPT_ROOT%%PROGRAM%_manifest.yaml
if not exist "%MANIFEST_PATH%" (
    echo [ERROR] Manifest not found: %MANIFEST_PATH%
    pause
    exit /b 1
)

REM Validate .env in scripts directory
set ENV_PATH=%SCRIPT_ROOT%.env
if not exist "%ENV_PATH%" (
    echo [WARN] .env not found in: %ENV_PATH%
)

REM Activate venv
call "%REPO_ROOT%\venv\Scripts\activate.bat"

REM Move into repo root so Python can import generator
cd /d "%REPO_ROOT%"

REM Build CLI flags
set CLI_ENV_FLAG=
if not "%ENVIRONMENT%"=="" (
    set CLI_ENV_FLAG=--environment %ENVIRONMENT%
)

set CLI_ENVFILE_FLAG=
if exist "%ENV_PATH%" (
    set CLI_ENVFILE_FLAG=--env-file "%ENV_PATH%"
)

REM Run generator
python -m generator.main ^
    --manifest "%MANIFEST_PATH%" ^
    --output "%OUTPUT_DIR%" ^
    %CLI_ENV_FLAG% ^
    %CLI_ENVFILE_FLAG%

echo Completed at %DATE% %TIME% >> "%OUTPUT_DIR%\run-log.txt"

pause
endlocal
