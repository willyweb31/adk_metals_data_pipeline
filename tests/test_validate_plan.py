from datetime import datetime, timezone

import pytest

from metals_pipeline.nodes import PlanError, make_plan
from metals_pipeline.schemas import RunPlan

NOW = datetime(2026, 11, 15, 18, 2, 11, tzinfo=timezone.utc)


def plan(**kw):
    base = dict(series_ids=["WPU10"], start_date="2018-01-01", base_month="2020-01", focus="")
    base.update(kw)
    return RunPlan(**base)


def test_valid_plan_passes_through_with_run_id():
    p = make_plan(plan(series_ids=["wpu10", "WPU101", "WPU10"]), NOW)
    assert p.series_ids == ["WPU10", "WPU101"]       # normalised and de-duplicated
    assert p.start_date == "2018-01-01"
    assert p.base_month == "2020-01"
    assert p.run_id == "20261115T180211Z"
    assert p.notes == []


def test_rejects_series_outside_allowlist():
    p = make_plan(plan(series_ids=["WPU10", "GOLDAMGBD228NLBM"]), NOW)
    assert p.series_ids == ["WPU10"]
    assert any("GOLDAMGBD228NLBM" in n for n in p.notes)


def test_all_series_outside_allowlist_is_an_error():
    with pytest.raises(PlanError):
        make_plan(plan(series_ids=["NOT_A_SERIES"]), NOW)


def test_empty_series_list_defaults_to_all_six():
    assert len(make_plan(plan(series_ids=[]), NOW).series_ids) == 6


def test_clamps_start_before_2000():
    p = make_plan(plan(start_date="1985-06-01", base_month="2020-01"), NOW)
    assert p.start_date == "2000-01-01"
    assert any("clamped" in n for n in p.notes)


def test_too_recent_start_is_moved_back_for_history():
    p = make_plan(plan(start_date="2026-01-01", base_month="2026-01"), NOW)
    assert p.start_date == "2023-01-01"


def test_base_month_before_start_uses_start_month():
    p = make_plan(plan(start_date="2021-03-15", base_month="2020-01"), NOW)
    assert p.base_month == "2021-03"


def test_year_only_start_and_bad_base_month():
    p = make_plan(plan(start_date="2019", base_month="soon"), NOW)
    assert p.start_date == "2019-01-01"
    assert p.base_month == "2020-01"
