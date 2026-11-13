"""The two LLM agents in the workflow: a run planner and an analyst.

Both use structured output, so the graph receives validated Pydantic data
rather than free text. Instructions deliberately contain no curly braces,
because ADK treats those as session-state placeholders.
"""

from __future__ import annotations

from google.adk import Agent
from google.genai import types

from . import config
from .schemas import Commentary, MetricsSummary, RunPlan

_SERIES_DESCRIPTIONS = {
    "WPU10": "Metals and metal products, all (headline index)",
    "WPU101": "Iron and steel",
    "WPU1017": "Steel mill products",
    "WPU102501": "Aluminum mill shapes",
    "WPU102502": "Copper and brass mill shapes",
    "PCU331110331110": "Iron and steel mills and ferroalloy manufacturing (industry view)",
    "PCOPPUSDM": "Global price of copper, U.S. dollars per metric ton (market price)",
    "PALUMUSDM": "Global price of aluminum, U.S. dollars per metric ton (market price)",
    "PNICKUSDM": "Global price of nickel, U.S. dollars per metric ton (market price)",
    "PZINCUSDM": "Global price of zinc, U.S. dollars per metric ton (market price)",
    "PIORECRUSDM": "Global price of iron ore, U.S. dollars per dry metric ton (market price)",
}
_ALLOWLIST_TEXT = "\n".join(
    f"- {sid}: {_SERIES_DESCRIPTIONS.get(sid, role)}" for sid, role in config.SERIES.items()
)

PLANNER_INSTRUCTION = f"""\
You plan a data refresh for a metals price dashboard built on FRED data:
BLS producer price indexes and IMF global market prices in U.S. dollars per
metric ton. Read the user's request and return a RunPlan.

Allowed series. Use only these IDs, exactly as written:
{_ALLOWLIST_TEXT}

Rules:
- series_ids: include every allowed series unless the user explicitly asks to
  limit the set (for example "only copper and aluminum" or "just market
  prices"). A topic of interest such as "focus on steel" does not remove
  series; it only sets the focus field.
- start_date: YYYY-MM-DD. "since 2018" means 2018-01-01. If no date is given,
  use {config.DEFAULT_START}.
- base_month: YYYY-MM, the month every series is rebased to 100. Use
  {config.DEFAULT_BASE_MONTH} unless the user asks for another.
- focus: a short phrase describing what the user cares about, or an empty
  string if nothing specific.
Never invent series IDs. Return only the RunPlan.
"""

ANALYST_INSTRUCTION = """\
You are a commodities analyst writing the commentary box on a metals price
dashboard. The user message is a JSON MetricsSummary. Each series has a kind:
"market" series are global prices in U.S. dollars per metric ton; "ppi" series
are producer price index levels (points, not dollars). For each series it
gives: label, kind, units, as_of (latest month), value, mom_change and mom_pct
(change from the prior month, in the series' units and in percent),
yoy_change and yoy_pct (change from 12 months earlier, in units and percent),
vol_12m (12-month standard deviation of monthly changes, percentage points),
high_5y and low_5y (range over the last five years, in the series' units),
and yoy_rank (1 = fastest year-over-year increase within its kind). It also
gives data_through, base_month, focus, and a units note.

Write a one-sentence headline and 3 to 5 bullets.

Rules:
- Use only figures that appear in the input, quoted exactly as given. Never
  estimate, recall, round differently, or invent a number.
- Write for a business reader in plain English. Never use field names such as
  mom_pct, yoy_pct, vol_12m, high_5y, or yoy_rank; say "up 2.16% from August",
  "27.6% higher than a year earlier", "the most volatile", "its five-year high".
  Add % to percent changes and "points" to index levels.
- Write market prices as dollars per metric ton with a dollar sign and
  thousands separators, for example "copper averaged $14,473.06 per metric
  ton in September 2026, up $141.79 (0.98%) from August". Never call a market
  price an index, and never compare a dollar price with an index level; compare
  across kinds only by percent change.
- Market prices can be a month behind the indexes, so name the month from each
  series' as_of when quoting it.
- Include at least one bullet about market prices in dollars and at least one
  about the producer price indexes, when both kinds are present.
- Refer to series by their label. Write months as a month and year such as
  September 2026.
- If focus is non-empty, lead with the series most related to it.
- Compare series: who is rising fastest or falling, which is most volatile,
  and where latest levels sit against their five-year high and low.
- Describe the numbers only. Do not attribute moves to causes such as tariffs
  or demand, and do not give investment advice.
- Each bullet is one or two plain sentences.
"""

# Low temperature for consistent output; retry Gemini's transient overload
# errors (429/5xx) with exponential backoff instead of failing the run.
_low_temp = types.GenerateContentConfig(
    temperature=0.2,
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(
            attempts=6,
            initial_delay=2.0,
            max_delay=30.0,
            exp_base=2.0,
            http_status_codes=[429, 500, 502, 503, 504],
        )
    ),
)

planner = Agent(
    name="planner",
    model=config.adk_model(),
    description="Turns a natural-language refresh request into a RunPlan.",
    instruction=PLANNER_INSTRUCTION,
    output_schema=RunPlan,
    generate_content_config=_low_temp,
)

analyst = Agent(
    name="analyst",
    model=config.adk_model(),
    description="Writes 3 to 5 grounded insights from the computed metrics.",
    instruction=ANALYST_INSTRUCTION,
    input_schema=MetricsSummary,
    output_schema=Commentary,
    generate_content_config=_low_temp,
)
