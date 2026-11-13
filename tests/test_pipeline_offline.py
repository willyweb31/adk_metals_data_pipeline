"""Run the deterministic nodes end to end on fixtures (no FRED, no LLM).

Covers both branches of the quality gate: a clean run publishes the page and
manifest; a corrupted fixture takes the `fail` route and writes a manifest
with the reason.
"""

import json

from conftest import FakeCtx

from metals_pipeline import nodes
from metals_pipeline.schemas import Commentary, RunPlan

REQUEST = RunPlan(series_ids=["WPU10", "WPU101", "PCOPPUSDM"], start_date="2019-01-01",
                  base_month="2020-01", focus="steel")
COMMENTARY = Commentary(
    headline="Iron and steel prices outpaced the metals index through June 2021.",
    bullets=["Iron and Steel rose 10.3% year over year.", "Metals rose 5.5%."],
)


def run_until_gate(ctx):
    plan = nodes.validate_plan(ctx, REQUEST)
    ingested = nodes.ingest(ctx, plan)
    summary = nodes.transform(ctx, ingested)
    return summary, nodes.quality_gate(ctx, summary)


def test_pass_branch_publishes_everything(offline_pipeline, local_output):
    ctx = FakeCtx()
    summary, gate = run_until_gate(ctx)
    assert gate.actions.route == "pass"
    assert gate.output["run_id"] == summary.run_id

    final = nodes.publish(ctx, COMMENTARY)
    run_id = ctx.state["run_id"]
    expected = [
        f"raw/fred/WPU10/ingest_date=2021-07-15/{run_id}.json",
        f"raw/fred/WPU101/ingest_date=2021-07-15/{run_id}.json",
        f"raw/fred/PCOPPUSDM/ingest_date=2021-07-15/{run_id}.json",
        f"processed/metals_ppi/run_id={run_id}/monthly.csv",
        f"processed/metals_ppi/run_id={run_id}/annual.csv",
        f"processed/metals_ppi/run_id={run_id}/latest.csv",
        f"site/run_id={run_id}/index.html",
        "site/latest/index.html",
        f"runs/{run_id}/manifest.json",
    ]
    for rel in expected:
        assert (local_output / rel).is_file(), rel

    manifest = json.loads((local_output / f"runs/{run_id}/manifest.json").read_text())
    assert manifest["status"] == "published"
    assert manifest["row_counts"] == {"WPU10": 30, "WPU101": 30, "PCOPPUSDM": 30}
    assert manifest["null_counts"] == {"WPU10": 1, "WPU101": 0, "PCOPPUSDM": 0}
    assert manifest["quality"]["passed"] is True
    assert manifest["versions"]["google-adk"] != "unknown"
    assert set(manifest["timings_seconds"]) >= {"ingest", "transform", "quality_gate", "publish"}

    html = (local_output / "site/latest/index.html").read_text(encoding="utf-8")
    assert COMMENTARY.headline in html
    assert "June 2021" in html and "Iron and Steel" in html
    # Copper appears in dollars: latest 8900, +100 MoM, +1200 YoY.
    assert "Market prices (U.S. dollars per metric ton)" in html
    assert "$8,900.00" in html and "+$100.00" in html and "+$1,200.00" in html
    assert 'id="spark-PCOPPUSDM"' in html
    kinds = {s["series_id"]: s["kind"] for s in ctx.state["summary"]["series"]}
    assert kinds == {"WPU10": "ppi", "WPU101": "ppi", "PCOPPUSDM": "market"}
    assert "chart.umd.min.js" in html
    assert "processed/metals_ppi/run_id=" in html
    assert "published" in final.output["status"]


def test_fail_branch_writes_manifest_with_reason(offline_pipeline, local_output):
    # Corrupt one fixture: repeat the last observation (duplicate date).
    obs = offline_pipeline["WPU101"]["response"]["observations"]
    obs.append(dict(obs[-1]))

    ctx = FakeCtx()
    summary, gate = run_until_gate(ctx)
    assert gate.actions.route == "fail"

    final = nodes.report_failure(ctx, summary)
    run_id = ctx.state["run_id"]
    manifest = json.loads((local_output / f"runs/{run_id}/manifest.json").read_text())
    assert manifest["status"] == "failed_quality_gate"
    assert any("WPU101 unique_dates" in r for r in manifest["failure_reasons"])
    assert not (local_output / "site").exists()   # nothing published
    assert final.output["status"] == "failed_quality_gate"
