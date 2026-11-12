"""Minimal FRED API client (series metadata + observations).

Retries 429 and 5xx with exponential backoff. Error messages never include the
request URL, because FRED puts the API key in the query string.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from . import config

_RETRY_STATUS = {429, 500, 502, 503, 504}


class FredError(RuntimeError):
    pass


def _get(endpoint: str, params: dict[str, Any], api_key: str,
         session: requests.Session | None = None) -> dict[str, Any]:
    http = session or requests
    url = f"{config.FRED_BASE_URL}/{endpoint}"
    query = {**params, "api_key": api_key, "file_type": "json"}
    last_status = None
    for attempt in range(config.FRED_MAX_RETRIES + 1):
        try:
            resp = http.get(url, params=query, timeout=config.FRED_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_status = type(exc).__name__
            if attempt < config.FRED_MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise FredError(f"FRED {endpoint} request failed: {last_status}") from None
        if resp.status_code in _RETRY_STATUS and attempt < config.FRED_MAX_RETRIES:
            last_status = resp.status_code
            time.sleep(2 ** attempt)
            continue
        if not resp.ok:
            try:
                message = resp.json().get("error_message", "")
            except ValueError:
                message = ""
            raise FredError(
                f"FRED {endpoint} returned HTTP {resp.status_code} for "
                f"{params.get('series_id')}: {message}".rstrip(": ")
            )
        return resp.json()
    raise FredError(f"FRED {endpoint} failed after retries (last: {last_status})")


def fetch_series_info(series_id: str, api_key: str,
                      session: requests.Session | None = None) -> dict[str, Any]:
    """Title, units, frequency, etc. from `fred/series`."""
    data = _get("series", {"series_id": series_id}, api_key, session)
    seriess = data.get("seriess") or []
    if not seriess:
        raise FredError(f"FRED returned no metadata for {series_id}")
    return seriess[0]


def fetch_observations(series_id: str, observation_start: str, api_key: str,
                       session: requests.Session | None = None) -> dict[str, Any]:
    """Raw `fred/series/observations` response, returned exactly as received."""
    return _get(
        "series/observations",
        {"series_id": series_id, "observation_start": observation_start},
        api_key,
        session,
    )
