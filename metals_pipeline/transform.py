"""Pure pandas transforms: raw FRED JSON -> monthly, annual, latest tables.

No I/O and no ADK here, so everything is unit-testable offline.

Rules (from the spec):
- FRED values arrive as strings; anything non-numeric (FRED uses ".") is null
  and counted, never silently dropped.
- rebased_100 = index / index at the plan's base month * 100.
- mom_pct / yoy_pct compare against the same series 1 / 12 calendar months
  earlier (looked up by date, so a missing month yields null, not a wrong lag).
- vol_12m = rolling 12-month standard deviation of mom_pct.
- annual rows with fewer than 12 months are kept; `months` flags them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MONTHLY_COLUMNS = [
    "series_id", "label", "date", "index_value", "rebased_100",
    "mom_pct", "yoy_pct", "vol_12m",
]
ANNUAL_COLUMNS = [
    "series_id", "year", "avg_index", "min_index", "max_index",
    "avg_yoy_pct", "months",
]
LATEST_COLUMNS = [
    "series_id", "label", "as_of", "index_value", "mom_pct", "yoy_pct",
    "vol_12m", "high_5y", "low_5y", "yoy_rank",
]
DECIMALS = 4


def parse_observations(raw: dict) -> tuple[pd.DataFrame, int]:
    """Return (frame with date + index_value, count of non-numeric values)."""
    obs = (raw.get("response") or {}).get("observations") or []
    df = pd.DataFrame(obs, columns=["date", "value"]) if obs else pd.DataFrame(
        {"date": pd.Series(dtype="object"), "value": pd.Series(dtype="object")}
    )
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "index_value": pd.to_numeric(df["value"], errors="coerce").astype("float64"),
    })
    nulls = int(out["index_value"].isna().sum())
    return out, nulls


def _lagged_pct(dates: pd.Series, values: pd.Series, months: int) -> np.ndarray:
    """% change vs. the value exactly `months` calendar months earlier."""
    lookup = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(dates))
    lookup = lookup[~lookup.index.duplicated(keep="last")]
    prior = lookup.reindex(pd.DatetimeIndex(dates) - pd.DateOffset(months=months))
    return (values.to_numpy() / prior.to_numpy() - 1.0) * 100.0


def build_monthly(parsed: dict[str, pd.DataFrame], labels: dict[str, str],
                  base_month: str) -> pd.DataFrame:
    base_ts = pd.Timestamp(f"{base_month}-01")
    frames = []
    for series_id, df in parsed.items():
        d = df.sort_values("date", kind="stable").reset_index(drop=True).copy()
        base_rows = d.loc[d["date"] == base_ts, "index_value"].dropna()
        base_value = float(base_rows.iloc[-1]) if len(base_rows) else np.nan
        d["series_id"] = series_id
        d["label"] = labels.get(series_id, series_id)
        d["rebased_100"] = d["index_value"] / base_value * 100.0
        d["mom_pct"] = _lagged_pct(d["date"], d["index_value"], 1)
        d["yoy_pct"] = _lagged_pct(d["date"], d["index_value"], 12)
        d["vol_12m"] = d["mom_pct"].rolling(12, min_periods=12).std()
        frames.append(d[MONTHLY_COLUMNS])
    if not frames:
        return pd.DataFrame(columns=MONTHLY_COLUMNS)
    monthly = pd.concat(frames, ignore_index=True)
    num = ["rebased_100", "mom_pct", "yoy_pct", "vol_12m"]
    monthly[num] = monthly[num].round(DECIMALS)
    return monthly


def build_annual(monthly: pd.DataFrame) -> pd.DataFrame:
    if monthly.empty:
        return pd.DataFrame(columns=ANNUAL_COLUMNS)
    annual = (
        monthly.assign(year=monthly["date"].dt.year)
        .groupby(["series_id", "year"], sort=True)
        .agg(
            avg_index=("index_value", "mean"),
            min_index=("index_value", "min"),
            max_index=("index_value", "max"),
            avg_yoy_pct=("yoy_pct", "mean"),
            months=("index_value", "count"),
        )
        .reset_index()
    )
    num = ["avg_index", "min_index", "max_index", "avg_yoy_pct"]
    annual[num] = annual[num].round(DECIMALS)
    return annual[ANNUAL_COLUMNS]


def build_latest(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for series_id, g in monthly.groupby("series_id", sort=False):
        valid = g.dropna(subset=["index_value"]).sort_values("date")
        if valid.empty:
            rows.append({"series_id": series_id, "label": g["label"].iloc[0]})
            continue
        last = valid.iloc[-1]
        window = valid[valid["date"] > last["date"] - pd.DateOffset(years=5)]
        rows.append({
            "series_id": series_id,
            "label": last["label"],
            "as_of": last["date"].strftime("%Y-%m-%d"),
            "index_value": last["index_value"],
            "mom_pct": last["mom_pct"],
            "yoy_pct": last["yoy_pct"],
            "vol_12m": last["vol_12m"],
            "high_5y": window["index_value"].max(),
            "low_5y": window["index_value"].min(),
        })
    latest = pd.DataFrame(rows, columns=LATEST_COLUMNS[:-1])
    latest["yoy_rank"] = latest["yoy_pct"].rank(ascending=False, method="min").astype("Int64")
    return latest[LATEST_COLUMNS]


def to_csv(df: pd.DataFrame) -> str:
    return df.to_csv(index=False, date_format="%Y-%m-%d", lineterminator="\n")


def read_monthly_csv(text: str) -> pd.DataFrame:
    from io import StringIO

    df = pd.read_csv(StringIO(text), dtype={"series_id": str, "label": str})
    df["date"] = pd.to_datetime(df["date"])
    return df
