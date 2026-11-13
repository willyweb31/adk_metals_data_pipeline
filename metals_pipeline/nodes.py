"""Deterministic function nodes of the ADK Workflow.

Each node is a thin wrapper: it reads small inputs (node_input + session
state), calls pure functions in transform.py / quality.py / report.py, writes
to the object store, and returns a small Pydantic model or an Event.

State keys written (all small, JSON-serialisable):
  run_id, started_at, plan, timings, ingest, summary, quality
"""

from __future__ import annotations

import platform
import time
from datetime import date, datetime, timezone
from importlib import metadata
from typing import Any

from google.adk import Context, Event

from . import config, fred, quality, report
from . import transform as tx
from .schemas import (
    Commentary,
    IngestResult,
    MetricsSummary,
    QualityReport,
    RunPlan,
    SeriesIngest,
    SeriesLatest,
    ValidatedPlan,
)
from .storage import Store, get_store


class PlanError(ValueError):
    pass


# --- small helpers ---------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now_utc().date()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_timing(ctx: Context, name: str, t0: float) -> None:
    timings = dict(ctx.state.get("timings") or {})
    timings[name] = round(time.perf_counter() - t0, 3)
    ctx.state["timings"] = timings


def _version(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return "unknown"


def _num(x: Any, digits: int | None = None) -> float | None:
    try:
        if x is None or x != x:  # NaN check without importing numpy
            return None
        return round(float(x), digits) if digits is not None else float(x)
    except (TypeError, ValueError):
        return None


# --- 2. validate_plan --------------------------------------------------------------
def _parse_start(text: str) -> date:
    t = (text or "").strip()
    if len(t) == 4:
        t += "-01-01"
    elif len(t) == 7:
        t += "-01"
    return date.fromisoformat(t[:10])


def _parse_month(text: str) -> date:
    t = (text or "").strip()[:7]
    return date.fromisoformat(f"{t}-01")


def make_plan(plan: RunPlan, now: datetime) -> ValidatedPlan:
    """Pure validation: allowlist, date clamps, base month, run_id."""
    notes: list[str] = []

    series_ids: list[str] = []
    rejected: list[str] = []
    for raw in plan.series_ids or []:
        sid = str(raw).strip().upper()
        if sid in config.SERIES:
            if sid not in series_ids:
                series_ids.append(sid)
        else:
            rejected.append(sid)
    if rejected:
        notes.append(f"Rejected series not in the allowlist: {', '.join(rejected)}")
    if not series_ids:
        if rejected:
            raise PlanError(
                f"None of the requested series are in the allowlist: {', '.join(rejected)}"
            )
        series_ids = list(config.DEFAULT_SERIES)
        notes.append("No series requested; using every allowlisted series.")

    try:
        start = _parse_start(plan.start_date)
    except ValueError:
        start = date.fromisoformat(config.DEFAULT_START)
        notes.append(f"Unreadable start_date {plan.start_date!r}; using {config.DEFAULT_START}.")
    min_start = date.fromisoformat(config.MIN_START)
    latest_start = date(now.year - config.MIN_HISTORY_YEARS, 1, 1)
    if start < min_start:
        notes.append(f"start_date {start} clamped to {min_start}.")
        start = min_start
    elif start > latest_start:
        notes.append(
            f"start_date {start} moved back to {latest_start} so each series has "
            f"at least {config.MIN_HISTORY_YEARS} years of history."
        )
        start = latest_start

    start_month = date(start.year, start.month, 1)
    try:
        base = _parse_month(plan.base_month)
    except ValueError:
        base = _parse_month(config.DEFAULT_BASE_MONTH)
        notes.append(f"Unreadable base_month {plan.base_month!r}; using {config.DEFAULT_BASE_MONTH}.")
    if base < start_month or base > now.date():
        notes.append(f"base_month {base:%Y-%m} is outside the data window; using {start_month:%Y-%m}.")
        base = start_month

    return ValidatedPlan(
        series_ids=series_ids,
        start_date=start.isoformat(),
        base_month=f"{base:%Y-%m}",
        focus=(plan.focus or "").strip(),
        run_id=now.strftime("%Y%m%dT%H%M%SZ"),
        notes=notes,
    )


def validate_plan(ctx: Context, node_input: RunPlan) -> ValidatedPlan:
    t0 = time.perf_counter()
    now = _now_utc()
    plan = make_plan(node_input, now)
    ctx.state["run_id"] = plan.run_id
    ctx.state["started_at"] = _iso(now)
    ctx.state["plan"] = plan.model_dump(mode="json")
    ctx.state["timings"] = {}
    _record_timing(ctx, "validate_plan", t0)
    return plan


# --- 3. ingest ------------------------------------------------------------------------
_INFO_KEYS = ("id", "title", "units", "units_short", "frequency",
              "seasonal_adjustment", "last_updated", "observation_end")


def ingest(ctx: Context, node_input: ValidatedPlan) -> IngestResult:
    t0 = time.perf_counter()
    plan = node_input
    store = get_store()
    api_key = config.fred_api_key()
    now = _now_utc()

    series: list[SeriesIngest] = []
    for sid in plan.series_ids:
        info = fred.fetch_series_info(sid, api_key)
        response = fred.fetch_observations(sid, plan.start_date, api_key)
        raw = {
            "series_id": sid,
            "fetched_at": _iso(now),
            "run_id": plan.run_id,
            "request": {"observation_start": plan.start_date},
            "series_info": {k: info.get(k) for k in _INFO_KEYS},
            "response": response,
        }
        path = config.raw_path(sid, now.date().isoformat(), plan.run_id)
        store.put_json(path, raw)
        _, nulls = tx.parse_observations(raw)
        series.append(SeriesIngest(
            series_id=sid,
            title=info.get("title") or sid,
            units=info.get("units") or "",
            raw_path=path,
            rows=len(response.get("observations") or []),
            nulls=nulls,
        ))

    result = IngestResult(run_id=plan.run_id, series=series)
    ctx.state["ingest"] = result.model_dump(mode="json")
    _record_timing(ctx, "ingest", t0)
    return result


# --- 4. transform -------------------------------------------------------------------
def transform(ctx: Context, node_input: IngestResult) -> MetricsSummary:
    t0 = time.perf_counter()
    plan = ValidatedPlan.model_validate(ctx.state["plan"])
    store = get_store()

    parsed, labels, units = {}, {}, {}
    for s in node_input.series:
        df, _ = tx.parse_observations(store.get_json(s.raw_path))
        parsed[s.series_id] = df
        labels[s.series_id] = s.title
        units[s.series_id] = s.units

    monthly = tx.build_monthly(parsed, labels, plan.base_month, units)
    annual = tx.build_annual(monthly)
    latest = tx.build_latest(monthly)

    paths = {name: config.processed_path(plan.run_id, f"{name}.csv")
             for name in ("monthly", "annual", "latest")}
    for name, df in (("monthly", monthly), ("annual", annual), ("latest", latest)):
        store.put_text(paths[name], tx.to_csv(df), "text/csv")

    # The summary (what the analyst sees and the dashboard table shows) is rounded
    # to display precision, so the commentary can quote numbers exactly as the
    # table prints them. Full precision stays in latest.csv. Dollar amounts keep
    # cents (2 dp); index levels keep 1 dp.
    rows = []
    for r in latest.to_dict("records"):
        rank = r.get("yoy_rank")
        kind = str(r.get("kind") or "ppi")
        lvl = 2 if kind == "market" else 1
        units_text = r.get("units")
        rows.append(SeriesLatest(
            series_id=r["series_id"],
            label=report.short_label(str(r.get("label") or r["series_id"])),
            kind=kind,
            units=units_text if isinstance(units_text, str) else "",
            as_of=str(r.get("as_of") or ""),
            value=_num(r.get("value"), lvl),
            mom_change=_num(r.get("mom_change"), lvl),
            mom_pct=_num(r.get("mom_pct"), 2),
            yoy_change=_num(r.get("yoy_change"), lvl),
            yoy_pct=_num(r.get("yoy_pct"), 1),
            vol_12m=_num(r.get("vol_12m"), 2),
            high_5y=_num(r.get("high_5y"), lvl),
            low_5y=_num(r.get("low_5y"), lvl),
            yoy_rank=None if rank is None or rank != rank or str(rank) == "<NA>" else int(rank),
        ))
    as_ofs = [r.as_of for r in rows if r.as_of]
    summary = MetricsSummary(
        run_id=plan.run_id,
        data_through=max(as_ofs) if as_ofs else "",
        base_month=plan.base_month,
        focus=plan.focus,
        units_note=(
            "Series with kind 'ppi' are BLS producer price indexes; value, "
            "mom_change, yoy_change, high_5y and low_5y are index points. Series "
            "with kind 'market' are IMF global prices; those fields are U.S. "
            "dollars per metric ton (see each series' units). mom_pct and yoy_pct "
            "are percent changes; vol_12m is the 12-month standard deviation of "
            "mom_pct in percentage points."
        ),
        series=rows,
        processed_paths=paths,
    )
    ctx.state["summary"] = summary.model_dump(mode="json")
    _record_timing(ctx, "transform", t0)
    return summary


# --- 5. quality_gate --------------------------------------------------------------
def quality_gate(ctx: Context, node_input: MetricsSummary) -> Event:
    t0 = time.perf_counter()
    plan = ValidatedPlan.model_validate(ctx.state["plan"])
    store = get_store()
    monthly = tx.read_monthly_csv(store.get_text(node_input.processed_paths["monthly"]))
    result = quality.run_checks(monthly, plan.series_ids, plan.base_month, _today())
    ctx.state["quality"] = result.model_dump(mode="json")
    _record_timing(ctx, "quality_gate", t0)
    return Event(
        output=node_input.model_dump(mode="json"),
        route="pass" if result.passed else "fail",
    )


# --- manifest shared by publish / report_failure ------------------------------
def _object_paths(ctx: Context, run_id: str, extra: dict[str, Any]) -> dict[str, Any]:
    ingest_state = ctx.state.get("ingest") or {}
    summary_state = ctx.state.get("summary") or {}
    paths = {
        "raw": [s["raw_path"] for s in ingest_state.get("series", [])],
        "processed": summary_state.get("processed_paths", {}),
        "manifest": config.manifest_path(run_id),
    }
    paths.update(extra)
    return paths


def _write_manifest(ctx: Context, store: Store, status: str,
                    object_paths: dict[str, Any], **extra: Any) -> str:
    run_id = ctx.state["run_id"]
    ingest_state = ctx.state.get("ingest") or {}
    quality_state = ctx.state.get("quality") or {}
    manifest = {
        "run_id": run_id,
        "status": status,
        "started_at": ctx.state.get("started_at"),
        "finished_at": _iso(_now_utc()),
        "plan": ctx.state.get("plan"),
        "storage": {"backend": store.backend, "location": store.location},
        "row_counts": {s["series_id"]: s["rows"] for s in ingest_state.get("series", [])},
        "null_counts": {s["series_id"]: s["nulls"] for s in ingest_state.get("series", [])},
        "quality": quality_state,
        "object_paths": object_paths,
        "timings_seconds": ctx.state.get("timings") or {},
        "versions": {
            "google-adk": _version("google-adk"),
            "pandas": _version("pandas"),
            "python": platform.python_version(),
        },
        "model": config.adk_model(),
        **extra,
    }
    path = config.manifest_path(run_id)
    return store.put_json(path, manifest)


# --- 7. publish --------------------------------------------------------------------
def publish(ctx: Context, node_input: Commentary) -> Event:
    t0 = time.perf_counter()
    store = get_store()
    run_id = ctx.state["run_id"]
    summary = MetricsSummary.model_validate(ctx.state["summary"])
    qreport = QualityReport.model_validate(ctx.state["quality"])
    monthly = tx.read_monthly_csv(store.get_text(summary.processed_paths["monthly"]))

    site_paths = [config.site_path(run_id), config.SITE_LATEST_PATH]
    object_paths = _object_paths(ctx, run_id, {"site": site_paths})
    html = report.render_report(
        summary=summary,
        commentary=node_input,
        monthly=monthly,
        quality=qreport,
        object_paths=object_paths,
        storage_location=store.location,
        adk_version=_version("google-adk"),
        generated_at=_iso(_now_utc()),
    )
    site_uris = [store.put_text(p, html, "text/html") for p in site_paths]
    _record_timing(ctx, "publish", t0)
    manifest_uri = _write_manifest(
        ctx, store, "published", object_paths,
        commentary=node_input.model_dump(mode="json"),
    )

    lines = [
        f"Run {run_id} published ({len(summary.series)} series, data through "
        f"{summary.data_through[:7]}).",
        f"Storage: {store.location}",
        *[f"  raw: {p}" for p in object_paths["raw"]],
        *[f"  processed: {p}" for p in summary.processed_paths.values()],
        *[f"  site: {p}" for p in site_paths],
        f"  manifest: {object_paths['manifest']}",
        f"Open: {site_uris[-1]}",
    ]
    return Event(
        message="\n".join(lines),
        output={"run_id": run_id, "status": "published",
                "site": site_uris, "manifest": manifest_uri},
    )


# --- 7b. report_failure ----------------------------------------------------------
def report_failure(ctx: Context, node_input: MetricsSummary) -> Event:
    store = get_store()
    run_id = ctx.state["run_id"]
    qreport = QualityReport.model_validate(ctx.state["quality"])
    failures = [f"{c.series_id} {c.name}: {c.detail}" for c in qreport.failures]
    manifest_uri = _write_manifest(
        ctx, store, "failed_quality_gate", _object_paths(ctx, run_id, {}),
        failure_reasons=failures,
    )
    message = "\n".join([
        f"Run {run_id} FAILED the quality gate; nothing was published.",
        *[f"  - {f}" for f in failures],
        f"Manifest: {manifest_uri}",
    ])
    return Event(
        message=message,
        output={"run_id": run_id, "status": "failed_quality_gate",
                "failures": failures, "manifest": manifest_uri},
    )
