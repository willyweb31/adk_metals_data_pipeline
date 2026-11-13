"""Render the static dashboard page (Jinja2 + inline Chart.js data)."""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import PACKAGE_DIR
from .schemas import Commentary, MetricsSummary, QualityReport

CHART_JS_URL = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"

_env = Environment(
    loader=FileSystemLoader(PACKAGE_DIR / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def short_label(title: str) -> str:
    """'Producer Price Index by Commodity: Metals and Metal Products: Iron and Steel'
    -> 'Iron and Steel'."""
    label = title
    for prefix in ("Producer Price Index by Commodity: ", "Producer Price Index by Industry: "):
        if label.startswith(prefix):
            label = label[len(prefix):]
    if label.startswith("Global price of "):
        label = label[len("Global price of "):]
    mmp = "Metals and Metal Products: "
    if label.startswith(mmp) and len(label) > len(mmp):
        label = label[len(mmp):]
    return label


def _clean(x: Any) -> Any:
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def _fmt(x: float | None, digits: int = 1, signed: bool = False) -> str:
    if x is None:
        return "–"
    return f"{x:+,.{digits}f}" if signed else f"{x:,.{digits}f}"


def _usd(x: float | None, signed: bool = False) -> str:
    """14473.06 -> '$14,473.06'; signed: 141.79 -> '+$141.79', -5 -> '−$5.00'."""
    if x is None:
        return "–"
    text = f"${abs(x):,.2f}"
    if signed:
        return ("+" if x >= 0 else "−") + text
    return text if x >= 0 else "−" + text


_env.filters["fmt"] = _fmt
_env.filters["usd"] = _usd


def chart_data(monthly: pd.DataFrame, summary: MetricsSummary) -> dict[str, Any]:
    """Line chart: PPIs rebased. Small charts: each market price in dollars.
    Bar chart: YoY % for every series, so market prices and indexes compare."""
    by_id = {s.series_id: s for s in summary.series}
    order = [s.series_id for s in summary.series]

    def series_months(kind: str) -> list[str]:
        ids = [sid for sid in order if by_id[sid].kind == kind]
        sub = monthly[monthly["series_id"].isin(ids)]
        return sorted(sub["date"].dt.strftime("%Y-%m").unique())

    ppi_months = series_months("ppi")
    lines = []
    for sid in [s for s in order if by_id[s].kind == "ppi"]:
        g = monthly[monthly["series_id"] == sid]
        by_month = dict(zip(g["date"].dt.strftime("%Y-%m"), g["rebased_100"]))
        lines.append({"id": sid, "label": by_id[sid].label,
                      "data": [_clean(by_month.get(m)) for m in ppi_months]})

    market = []
    for sid in [s for s in order if by_id[s].kind == "market"]:
        g = monthly[monthly["series_id"] == sid].sort_values("date")
        market.append({
            "id": sid,
            "label": by_id[sid].label,
            "months": list(g["date"].dt.strftime("%Y-%m")),
            "data": [_clean(round(float(v), 2)) if pd.notna(v) else None for v in g["value"]],
        })

    bars = sorted(
        [s for s in summary.series if s.yoy_pct is not None],
        key=lambda s: s.yoy_pct, reverse=True,
    )
    return {
        "months": ppi_months,
        "lines": lines,
        "market": market,
        "bars": [{"id": s.series_id, "label": s.label, "yoy": s.yoy_pct,
                  "kind": s.kind} for s in bars],
        "base_month": summary.base_month,
    }


def _month_label(as_of: str) -> str:
    return pd.Timestamp(as_of).strftime("%B %Y") if as_of else ""


def _embed_json(obj: Any) -> str:
    """JSON safe to place inside a <script> element."""
    text = json.dumps(obj, allow_nan=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_report(*, summary: MetricsSummary, commentary: Commentary,
                  monthly: pd.DataFrame, quality: QualityReport,
                  object_paths: dict[str, Any], storage_location: str,
                  adk_version: str, generated_at: str) -> str:
    passed = sum(1 for c in quality.checks if c.passed)
    rows = sorted(summary.series, key=lambda s: (s.yoy_rank is None, s.yoy_rank or 0))
    through = pd.Timestamp(summary.data_through) if summary.data_through else None
    template = _env.get_template("report.html.j2")
    return template.render(
        summary=summary,
        commentary=commentary,
        data_through=through.strftime("%B %Y") if through is not None else "n/a",
        market_through=_month_label(max((r.as_of for r in rows
                                         if r.kind == "market" and r.as_of), default="")),
        base_month_label=pd.Timestamp(f"{summary.base_month}-01").strftime("%b %Y"),
        market_rows=[r for r in rows if r.kind == "market"],
        ppi_rows=[r for r in rows if r.kind != "market"],
        checks_passed=passed,
        checks_total=len(quality.checks),
        object_paths=object_paths,
        storage_location=storage_location,
        adk_version=adk_version,
        generated_at=generated_at,
        chart_js_url=CHART_JS_URL,
        data_json=_embed_json(chart_data(monthly, summary)),
    )
