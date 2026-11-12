"""Root agent: an ADK 2.x Workflow graph.

    START -> planner -> validate_plan -> ingest -> transform -> quality_gate
    quality_gate --pass--> analyst -> publish
    quality_gate --fail--> report_failure

`adk run metals_pipeline "..."` and `adk web` both load `root_agent`.
"""

from __future__ import annotations

from google.adk import Workflow

from .agents import analyst, planner
from .nodes import (
    ingest,
    publish,
    quality_gate,
    report_failure,
    transform,
    validate_plan,
)

root_agent = Workflow(
    name="metals_pipeline",
    description=(
        "Refreshes six FRED metals producer price indexes, computes trend and "
        "volatility metrics, and publishes an HTML dashboard with AI commentary."
    ),
    edges=[
        (
            "START",
            planner,
            validate_plan,
            ingest,
            transform,
            quality_gate,
            {"pass": analyst, "fail": report_failure},
        ),
        (analyst, publish),
    ],
)
