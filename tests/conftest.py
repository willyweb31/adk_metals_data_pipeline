import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FIXED_NOW = datetime(2021, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
FIXTURE_IDS = ("WPU10", "WPU101", "PCOPPUSDM")


def load_raw(series_id: str) -> dict:
    return json.loads((FIXTURES / f"{series_id}.json").read_text(encoding="utf-8"))


class FakeCtx:
    """Stands in for google.adk.Context: nodes only touch ctx.state."""

    def __init__(self):
        self.state: dict = {}


@pytest.fixture
def raw_fixtures():
    return {sid: load_raw(sid) for sid in FIXTURE_IDS}


@pytest.fixture
def local_output(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    return tmp_path / "output"


@pytest.fixture
def offline_pipeline(local_output, monkeypatch):
    """Patch FRED + clock so the node chain runs fully offline on fixtures.

    Returns a dict of raw responses the fake FRED serves; tests may corrupt it.
    """
    from metals_pipeline import nodes

    served = {sid: load_raw(sid) for sid in FIXTURE_IDS}
    monkeypatch.setenv("FRED_API_KEY", "x" * 32)
    monkeypatch.setattr(nodes, "_now_utc", lambda: FIXED_NOW)
    monkeypatch.setattr(nodes.fred, "fetch_series_info",
                        lambda sid, key: copy.deepcopy(served[sid]["series_info"]))
    monkeypatch.setattr(nodes.fred, "fetch_observations",
                        lambda sid, start, key: copy.deepcopy(served[sid]["response"]))
    return served
