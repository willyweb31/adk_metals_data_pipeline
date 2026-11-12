"""Pydantic models passed between workflow nodes.

Nodes hand each other small models (paths, counts, the latest row per series),
never DataFrames or full series. Large data lives in the object store.

LLM-facing schemas (RunPlan, Commentary) use only required fields with no
defaults, which keeps Gemini's structured-output schema simple.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunPlan(BaseModel):
    """What the planner agent produces from the user's request."""

    series_ids: list[str] = Field(
        description="FRED series IDs to refresh, chosen only from the allowlist."
    )
    start_date: str = Field(description="First observation date, YYYY-MM-DD.")
    base_month: str = Field(
        description="Month every series is rebased to 100 at, YYYY-MM."
    )
    focus: str = Field(
        description="Short note on what the user cares about, or empty string."
    )


class ValidatedPlan(RunPlan):
    """RunPlan after allowlist checks, date clamping, and a run_id."""

    run_id: str
    notes: list[str] = Field(default_factory=list)


class SeriesIngest(BaseModel):
    series_id: str
    title: str
    units: str
    raw_path: str
    rows: int
    nulls: int


class IngestResult(BaseModel):
    run_id: str
    series: list[SeriesIngest]


class SeriesLatest(BaseModel):
    series_id: str
    label: str
    as_of: str
    index_value: float | None
    mom_pct: float | None
    yoy_pct: float | None
    vol_12m: float | None
    high_5y: float | None
    low_5y: float | None
    yoy_rank: int | None


class MetricsSummary(BaseModel):
    """Small summary handed to the quality gate and the analyst agent."""

    run_id: str
    data_through: str
    base_month: str
    focus: str
    units_note: str
    series: list[SeriesLatest]
    processed_paths: dict[str, str]


class CheckResult(BaseModel):
    name: str
    series_id: str
    passed: bool
    detail: str


class QualityReport(BaseModel):
    passed: bool
    checks: list[CheckResult]

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


class Commentary(BaseModel):
    """What the analyst agent produces."""

    headline: str = Field(description="One sentence, under 20 words.")
    bullets: list[str] = Field(
        description="3 to 5 insights, each citing figures from the input only."
    )
