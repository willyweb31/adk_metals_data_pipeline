"""Transform rules, checked against hand-computed values from the fixtures.

WPU10  = 200 + i (i = months since 2019-01), 2020-07 is "."  -> base 2020-01 = 212
WPU101 = 300 + 3i                                               -> base 2020-01 = 336
PCOPPUSDM = 6000 + 100i  (USD per metric ton)                   -> base 2020-01 = 7200
"""

import math

import pandas as pd
import pytest

from metals_pipeline import transform as tx


@pytest.fixture
def tables(raw_fixtures):
    parsed = {sid: tx.parse_observations(raw)[0] for sid, raw in raw_fixtures.items()}
    labels = {"WPU10": "Metals", "WPU101": "Iron and Steel", "PCOPPUSDM": "Copper"}
    units = {"WPU10": "Index 1982=100", "WPU101": "Index 1982=100",
             "PCOPPUSDM": "U.S. Dollars per Metric Ton"}
    monthly = tx.build_monthly(parsed, labels, "2020-01", units)
    return monthly, tx.build_annual(monthly), tx.build_latest(monthly)


def row(monthly, sid, date):
    r = monthly[(monthly.series_id == sid) & (monthly.date == pd.Timestamp(date))]
    assert len(r) == 1
    return r.iloc[0]


def test_dot_values_become_null_and_are_counted(raw_fixtures):
    df, nulls = tx.parse_observations(raw_fixtures["WPU10"])
    assert nulls == 1
    assert len(df) == 30  # the null month is kept, not dropped
    assert math.isnan(df.loc[df.date == "2020-07-01", "value"].iloc[0])
    assert tx.parse_observations(raw_fixtures["WPU101"])[1] == 0


@pytest.mark.parametrize("sid,date,index,rebased,mom,yoy", [
    # 2020-01: 212 vs base 212; vs 211 last month; vs 200 a year ago
    ("WPU10", "2020-01-01", 212.0, 100.0, 0.4739, 6.0),
    # 2021-01: 224 vs base 212; vs 223; vs 212
    ("WPU10", "2021-01-01", 224.0, 105.6604, 0.4484, 5.6604),
    # 2021-01: 372 vs base 336; vs 369; vs 336
    ("WPU101", "2021-01-01", 372.0, 110.7143, 0.813, 10.7143),
])
def test_hand_checked_months(tables, sid, date, index, rebased, mom, yoy):
    r = row(tables[0], sid, date)
    assert r.value == pytest.approx(index)
    assert r.rebased_100 == pytest.approx(rebased, abs=1e-4)
    assert r.mom_pct == pytest.approx(mom, abs=1e-4)
    assert r.yoy_pct == pytest.approx(yoy, abs=1e-4)


def test_missing_month_breaks_lags_instead_of_shifting(tables):
    monthly = tables[0]
    assert math.isnan(row(monthly, "WPU10", "2020-07-01").mom_pct)
    assert math.isnan(row(monthly, "WPU10", "2020-08-01").mom_pct)
    # 2020-08 YoY still compares against 2019-08 by date: 219 / 207
    assert row(monthly, "WPU10", "2020-08-01").yoy_pct == pytest.approx((219 / 207 - 1) * 100, abs=1e-4)


def test_vol_12m_needs_twelve_monthly_changes(tables):
    monthly = tables[0]
    assert math.isnan(row(monthly, "WPU101", "2019-12-01").vol_12m)
    assert not math.isnan(row(monthly, "WPU101", "2020-01-01").vol_12m)


def test_annual_flags_partial_years(tables):
    annual = tables[1].set_index(["series_id", "year"])
    assert annual.loc[("WPU101", 2019), "months"] == 12
    assert annual.loc[("WPU101", 2021), "months"] == 6      # partial year kept
    assert annual.loc[("WPU10", 2020), "months"] == 11      # null month not counted
    assert annual.loc[("WPU101", 2019), "avg_value"] == pytest.approx(300 + 3 * 5.5)
    assert list(tables[1].columns) == tx.ANNUAL_COLUMNS


def test_latest_table(tables):
    latest = tables[2].set_index("series_id")
    assert list(tables[2].columns) == tx.LATEST_COLUMNS
    assert latest.loc["WPU10", "as_of"] == "2021-06-01"
    assert latest.loc["WPU10", "value"] == 229
    assert latest.loc["WPU10", "high_5y"] == 229 and latest.loc["WPU10", "low_5y"] == 200
    # WPU101 YoY 387/351 = 10.26% beats WPU10 229/217 = 5.53%. Copper (+15.6%)
    # is faster than both but is ranked separately, among market prices.
    assert latest.loc["WPU101", "yoy_rank"] == 1
    assert latest.loc["WPU10", "yoy_rank"] == 2
    assert latest.loc["PCOPPUSDM", "yoy_rank"] == 1


def test_market_price_dollar_changes(tables):
    monthly, _, latest = tables
    # 2021-01 is i = 24: 8400; a month earlier 8300; a year earlier 7200
    r = row(monthly, "PCOPPUSDM", "2021-01-01")
    assert (r.kind, r.units) == ("market", "U.S. Dollars per Metric Ton")
    assert r.value == pytest.approx(8400)
    assert r.mom_change == pytest.approx(100)
    assert r.yoy_change == pytest.approx(1200)
    assert r.yoy_pct == pytest.approx(1200 / 7200 * 100, abs=1e-4)
    last = latest.set_index("series_id").loc["PCOPPUSDM"]
    assert last.value == pytest.approx(8900)          # 2021-06, i = 29
    assert last.yoy_change == pytest.approx(1200)
    assert last.high_5y == pytest.approx(8900) and last.low_5y == pytest.approx(6000)


def test_index_series_are_kind_ppi_with_point_changes(tables):
    r = row(tables[0], "WPU101", "2021-01-01")
    assert r.kind == "ppi"
    assert r.mom_change == pytest.approx(3)     # 372 - 369 index points
    assert r.yoy_change == pytest.approx(36)    # 372 - 336


def test_monthly_columns_and_csv_roundtrip(tables):
    monthly = tables[0]
    assert list(monthly.columns) == tx.MONTHLY_COLUMNS
    back = tx.read_monthly_csv(tx.to_csv(monthly))
    assert len(back) == len(monthly)
    assert back.date.dtype.kind == "M"
