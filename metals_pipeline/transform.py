"""Pure pandas transforms: raw FRED JSON -> monthly, annual, latest tables.

No I/O and no ADK here, so everything is unit-testable offline.

Two kinds of series share one schema, told apart by the `kind` column:
- "ppi":    BLS producer price indexes; `value` is an index level (e.g. 1982=100).
- "market": IMF global prices; `value` is U.S. dollars per metric ton.

Rules (from the spec):
- FRED values arrive as strings; anything non-numeric (FRED uses ".") is null
  and counted, never silently dropped.
- rebased_100 = value / value at the plan's base month * 100.
- mom_* / yoy_* compare against the same series 1 / 12 calendar months
  earlier (looked up by date, so a missing month yields null, not a wrong lag).
  *_pct is the percent change; *_change is the absolute change in the series'
  own units (index points, or dollars per metric ton).
- vol_12m = rolling 12-month standard deviation of mom_pct.
- annual rows with fewer than 12 months are kept; `months` flags them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import series_kind

MONTHLY_COLUMNS = [
    "series_id", "label", "kind", "units", "date", "value", "rebased_100",
    "mom_change", "mom_pct", "yoy_change", "yoy_pct", "vol_12m",
]
ANNUAL_COLUMNS = [
    "series_id", "kind", "year", "avg_value", "min_value", "max_value",
    "avg_yoy_pct", "months",
]
LATEST_COLUMNS = [
    "series_id", "label", "kind", "units", "as_of", "value",
    "mom_change", "mom_pct", "yoy_change", "yoy_pct",
    "vol_12m", "high_5y", "low_5y", "yoy_rank",
]
DECIMALS = 4


def parse_observations(raw: dict) -> tuple[pd.DataFrame, int]:
    """Return (frame with date + value, count of non-numeric values)."""
    obs = (raw.get("response") or {}).get("observations") or []
    df = pd.DataFrame(obs, columns=["date", "value"]) if obs else pd.DataFrame(
        {"date": pd.Series(dtype="object"), "value": pd.Series(dtype="object")}
    )
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "value": pd.to_numeric(df["value"], errors="coerce").astype("float64"),
    })
    nulls = int(out["value"].isna().sum())
    return out, nulls


def _prior(dates: pd.Series, values: pd.Series, months: int) -> np.ndarray:
    """The value exactly `months` calendar months earlier (NaN if missing)."""
    lookup = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(dates))
    lookup = lookup[~lookup.index.duplicated(keep="last")]
    return lookup.reindex(pd.DatetimeIndex(dates) - pd.DateOffset(months=months)).to_numpy()


def build_monthly(parsed: dict[str, pd.DataFrame], labels: dict[str, str],
                  base_month: str, units: dict[str, str] | None = None) -> pd.DataFrame:
    base_ts = pd.Timestamp(f"{base_month}-01")
    units = units or {}
    frames = []
    for series_id, df in parsed.items():
        d = df.sort_values("date", kind="stable").reset_index(drop=True).copy()
        base_rows = d.loc[d["date"] == base_ts, "value"].dropna()
        base_value = float(base_rows.iloc[-1]) if len(base_rows) else np.nan
        cur = d["value"].to_numpy()
        prev_m = _prior(d["date"], d["value"], 1)
        prev_y = _prior(d["date"], d["value"], 12)
        d["series_id"] = series_id
        d["label"] = labels.get(series_id, series_id)
        d["kind"] = series_kind(series_id)
        d["units"] = units.get(series_id, "")
        d["rebased_100"] = d["value"] / base_value * 100.0
        d["mom_change"] = cur - prev_m
        d["mom_pct"] = (cur / prev_m - 1.0) * 100.0
        d["yoy_change"] = cur - prev_y
        d["yoy_pct"] = (cur / prev_y - 1.0) * 100.0
        d["vol_12m"] = d["mom_pct"].rolling(12, min_periods=12).std()
        frames.append(d[MONTHLY_COLUMNS])
    if not frames:
        return pd.DataFrame(columns=MONTHLY_COLUMNS)
    monthly = pd.concat(frames, ignore_index=True)
    num = ["rebased_100", "mom_change", "mom_pct", "yoy_change", "yoy_pct", "vol_12m"]
    monthly[num] = monthly[num].round(DECIMALS)
    return monthly


def build_annual(monthly: pd.DataFrame) -> pd.DataFrame:
    if monthly.empty:
        return pd.DataFrame(columns=ANNUAL_COLUMNS)
    annual = (
        monthly.assign(year=monthly["date"].dt.year)
        .groupby(["series_id", "kind", "year"], sort=True)
        .agg(
            avg_value=("value", "mean"),
            min_value=("value", "min"),
            max_value=("value", "max"),
            avg_yoy_pct=("yoy_pct", "mean"),
            months=("value", "count"),
        )
        .reset_index()
    )
    num = ["avg_value", "min_value", "max_value", "avg_yoy_pct"]
    annual[num] = annual[num].round(DECIMALS)
    return annual[ANNUAL_COLUMNS]


def build_latest(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for series_id, g in monthly.groupby("series_id", sort=False):
        valid = g.dropna(subset=["value"]).sort_values("date")
        first = g.iloc[0]
        if valid.empty:
            rows.append({"series_id": series_id, "label": first["label"],
                         "kind": first["kind"], "units": first["units"]})
            continue
        last = valid.iloc[-1]
        window = valid[valid["date"] > last["date"] - pd.DateOffset(years=5)]
        rows.append({
            "series_id": series_id,
            "label": last["label"],
            "kind": last["kind"],
            "units": last["units"],
            "as_of": last["date"].strftime("%Y-%m-%d"),
            "value": last["value"],
            "mom_change": last["mom_change"],
            "mom_pct": last["mom_pct"],
            "yoy_change": last["yoy_change"],
            "yoy_pct": last["yoy_pct"],
            "vol_12m": last["vol_12m"],
            "high_5y": window["value"].max(),
            "low_5y": window["value"].min(),
        })
    latest = pd.DataFrame(rows, columns=LATEST_COLUMNS[:-1])
    # Rank within each kind, so market prices and indexes are ranked separately.
    latest["yoy_rank"] = (
        latest.groupby("kind")["yoy_pct"].rank(ascending=False, method="min").astype("Int64")
    )
    return latest[LATEST_COLUMNS]


def to_csv(df: pd.DataFrame) -> str:
    return df.to_csv(index=False, date_format="%Y-%m-%d", lineterminator="\n")


def read_monthly_csv(text: str) -> pd.DataFrame:
    from io import StringIO

    df = pd.read_csv(StringIO(text), dtype={"series_id": str, "label": str,
                                            "kind": str, "units": str})
    df["units"] = df["units"].fillna("")
    df["date"] = pd.to_datetime(df["date"])
    return df
