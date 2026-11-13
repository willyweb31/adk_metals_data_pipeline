from datetime import date

import pandas as pd
import pytest

from metals_pipeline import quality
from metals_pipeline import transform as tx

TODAY = date(2021, 7, 15)


@pytest.fixture
def monthly(raw_fixtures):
    parsed = {sid: tx.parse_observations(raw)[0] for sid, raw in raw_fixtures.items()}
    return tx.build_monthly(parsed, {}, "2020-01")


def failed(report):
    return {(c.series_id, c.name) for c in report.checks if not c.passed}


def run(monthly, today=TODAY, series=("WPU10", "WPU101")):
    return quality.run_checks(monthly, list(series), "2020-01", today)


def test_clean_fixtures_pass(monthly):
    report = run(monthly)
    assert report.passed, failed(report)


def test_duplicate_dates_fail(monthly):
    dup = pd.concat([monthly, monthly[monthly.series_id == "WPU101"].tail(1)])
    report = run(dup)
    assert not report.passed
    assert ("WPU101", "unique_dates") in failed(report)


def test_gap_over_one_month_fails(monthly):
    gap = monthly[~((monthly.series_id == "WPU101") & (monthly.date == "2020-03-01"))]
    report = run(gap)
    assert ("WPU101", "monthly_continuity") in failed(report)


def test_non_positive_index_fails(monthly):
    bad = monthly.copy()
    bad.loc[(bad.series_id == "WPU101") & (bad.date == "2020-05-01"), "value"] = 0.0
    assert ("WPU101", "positive_values") in failed(run(bad))


def test_zero_rows_fails(monthly):
    report = run(monthly, series=("WPU10", "WPU101", "WPU1017"))
    assert ("WPU1017", "row_count") in failed(report)
    assert not run(monthly.iloc[0:0]).passed


def test_too_few_rows_fails(monthly):
    short = monthly.groupby("series_id").tail(20)
    assert ("WPU10", "row_count") in failed(run(short))


def test_stale_data_fails(monthly):
    assert ("WPU10", "freshness") in failed(run(monthly, today=date(2022, 1, 1)))


def test_market_prices_get_a_longer_freshness_window(monthly):
    # Latest fixture month is 2021-06-01. 100 days later an index is stale
    # (max 90) but a market price, which IMF publishes later, is not (max 120).
    series = ("WPU10", "PCOPPUSDM")
    report = run(monthly, today=date(2021, 9, 9), series=series)
    assert ("WPU10", "freshness") in failed(report)
    assert ("PCOPPUSDM", "freshness") not in failed(report)
    # 130 days later the market price is stale too.
    assert ("PCOPPUSDM", "freshness") in failed(run(monthly, today=date(2021, 10, 9), series=series))


def test_null_share_over_five_percent_fails(monthly):
    bad = monthly.copy()
    mask = (bad.series_id == "WPU10") & bad.date.isin(pd.to_datetime(["2019-03-01", "2019-04-01"]))
    bad.loc[mask, "value"] = float("nan")  # 3 of 30 rows null = 10%
    assert ("WPU10", "null_share") in failed(run(bad))


def test_extreme_latest_yoy_fails(monthly):
    bad = monthly.copy()
    last = (bad.series_id == "WPU10") & (bad.date == "2021-06-01")
    bad.loc[last, "yoy_pct"] = 250.0
    assert ("WPU10", "latest_yoy_range") in failed(run(bad))
