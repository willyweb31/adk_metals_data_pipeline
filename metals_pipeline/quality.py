"""Data-quality checks run by the quality_gate node. Pure functions, no I/O."""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import config
from .schemas import CheckResult, QualityReport


def _check(name: str, series_id: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, series_id=series_id, passed=bool(passed), detail=detail)


def check_series(series_id: str, g: pd.DataFrame, base_month: str,
                 today: date, max_staleness_days: int = config.MAX_STALENESS_DAYS,
                 ) -> list[CheckResult]:
    rows = len(g)
    if rows == 0:
        return [_check("row_count", series_id, False, "zero rows returned")]

    out = [_check("row_count", series_id, rows >= config.MIN_ROWS,
                  f"{rows} rows (min {config.MIN_ROWS})")]

    dates = g["date"]
    dupes = int(dates.duplicated().sum())
    out.append(_check("unique_dates", series_id, dupes == 0, f"{dupes} duplicate dates"))

    not_month_start = int((dates.dt.day != 1).sum())
    uniq = dates.drop_duplicates().sort_values()
    month_num = uniq.dt.year * 12 + uniq.dt.month
    gaps = int((month_num.diff().dropna() > 1).sum())
    out.append(_check(
        "monthly_continuity", series_id, gaps == 0 and not_month_start == 0,
        f"{gaps} gaps over one month, {not_month_start} non-month-start dates",
    ))

    values = g["value"]
    non_positive = int((values <= 0).sum())
    out.append(_check("positive_values", series_id, non_positive == 0,
                      f"{non_positive} non-positive values"))

    null_share = float(values.isna().mean())
    out.append(_check("null_share", series_id, null_share < config.MAX_NULL_SHARE,
                      f"{null_share:.1%} null (max {config.MAX_NULL_SHARE:.0%})"))

    valid = g.dropna(subset=["value"]).sort_values("date")
    if valid.empty:
        out.append(_check("freshness", series_id, False, "no numeric values"))
        return out

    latest_date = valid["date"].iloc[-1].date()
    age = (today - latest_date).days
    out.append(_check("freshness", series_id, age <= max_staleness_days,
                      f"latest {latest_date.isoformat()} is {age} days old "
                      f"(max {max_staleness_days})"))

    yoy = valid["yoy_pct"].iloc[-1] if "yoy_pct" in valid else float("nan")
    yoy_ok = pd.notna(yoy) and abs(yoy) <= config.MAX_ABS_LATEST_YOY_PCT
    out.append(_check("latest_yoy_range", series_id, yoy_ok,
                      f"latest YoY {yoy:.2f}%" if pd.notna(yoy) else "latest YoY unavailable"))

    base_ts = pd.Timestamp(f"{base_month}-01")
    has_base = bool(((valid["date"] == base_ts)).any())
    out.append(_check("base_month_present", series_id, has_base,
                      f"base month {base_month} {'present' if has_base else 'missing'}"))
    return out


def run_checks(monthly: pd.DataFrame, requested: list[str], base_month: str,
               today: date | None = None) -> QualityReport:
    today = today or date.today()
    checks: list[CheckResult] = []
    if monthly.empty:
        checks.append(_check("row_count", "*", False, "zero rows for all series"))
    for series_id in requested:
        g = monthly[monthly["series_id"] == series_id]
        staleness = (config.MARKET_MAX_STALENESS_DAYS
                     if config.series_kind(series_id) == "market"
                     else config.MAX_STALENESS_DAYS)
        checks.extend(check_series(series_id, g, base_month, today, staleness))
    return QualityReport(passed=all(c.passed for c in checks), checks=checks)
