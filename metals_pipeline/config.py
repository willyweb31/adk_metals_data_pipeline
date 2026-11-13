"""Static configuration: series allowlist, defaults, thresholds, env access.

Secrets are never stored here. They come from environment variables, which
`adk run` / `adk web` load from the nearest `.env` (agent folder or repo root).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

# When the package is imported outside the ADK CLI (pytest, scripts), load the
# same .env files ADK would. Existing environment variables always win.
load_dotenv(PACKAGE_DIR / ".env", override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

# --- Series allowlist -------------------------------------------------------
# Six monthly BLS producer price indexes on FRED. Labels are NOT typed here;
# they are pulled from FRED's `series` endpoint at ingest time.
SERIES: dict[str, str] = {
    "WPU10": "Headline index",
    "WPU101": "Steel family",
    "WPU1017": "Steel family",
    "WPU102501": "Nonferrous",
    "WPU102502": "Nonferrous",
    "PCU331110331110": "Industry view",
}
DEFAULT_SERIES: list[str] = list(SERIES)

# --- Plan defaults and limits -----------------------------------------------
DEFAULT_START = "2018-01-01"
MIN_START = "2000-01-01"
DEFAULT_BASE_MONTH = "2020-01"
# Latest allowed start is this many years before today, so every series has
# enough history for YoY and the 24-row quality check.
MIN_HISTORY_YEARS = 3

# --- Quality gate thresholds --------------------------------------------------
MIN_ROWS = 24
MAX_STALENESS_DAYS = 90
MAX_NULL_SHARE = 0.05
MAX_ABS_LATEST_YOY_PCT = 100.0

# --- FRED -----------------------------------------------------------------------
FRED_BASE_URL = "https://api.stlouisfed.org/fred"
FRED_TIMEOUT_SECONDS = 30
FRED_MAX_RETRIES = 3

# --- Object path layout (identical for GCS and the local backend) --------------
RAW_PREFIX = "raw/fred"
PROCESSED_PREFIX = "processed/metals_ppi"
SITE_PREFIX = "site"
RUNS_PREFIX = "runs"


def raw_path(series_id: str, ingest_date: str, run_id: str) -> str:
    return f"{RAW_PREFIX}/{series_id}/ingest_date={ingest_date}/{run_id}.json"


def processed_path(run_id: str, filename: str) -> str:
    return f"{PROCESSED_PREFIX}/run_id={run_id}/{filename}"


def site_path(run_id: str) -> str:
    return f"{SITE_PREFIX}/run_id={run_id}/index.html"


SITE_LATEST_PATH = f"{SITE_PREFIX}/latest/index.html"


def manifest_path(run_id: str) -> str:
    return f"{RUNS_PREFIX}/{run_id}/manifest.json"


# --- Environment ----------------------------------------------------------------
def fred_api_key() -> str:
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FRED_API_KEY is not set. Add it to .env (see .env.example)."
        )
    return key


def adk_model() -> str:
    # Flash-Lite by default: on the free tier, gemini-flash-latest often
    # returns 503 "high demand". Override with ADK_MODEL in .env.
    return os.getenv("ADK_MODEL", "gemini-flash-lite-latest")


def storage_backend() -> str:
    return os.getenv("STORAGE_BACKEND", "local").strip().lower()


def output_dir() -> Path:
    return Path(os.getenv("OUTPUT_DIR", str(REPO_ROOT / "output")))


def gcs_bucket() -> str:
    bucket = os.getenv("GCS_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("STORAGE_BACKEND=gcs requires GCS_BUCKET in .env.")
    return bucket


def gcp_project() -> str | None:
    return os.getenv("GOOGLE_CLOUD_PROJECT") or None


def azure_container() -> str:
    container = os.getenv("AZURE_STORAGE_CONTAINER", "").strip()
    if not container:
        raise RuntimeError("STORAGE_BACKEND=azure requires AZURE_STORAGE_CONTAINER in .env.")
    return container


def azure_account_url() -> str | None:
    url = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "").strip()
    if url:
        return url
    account = os.getenv("AZURE_STORAGE_ACCOUNT", "").strip()
    return f"https://{account}.blob.core.windows.net" if account else None


def azure_connection_string() -> str | None:
    return os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip() or None
