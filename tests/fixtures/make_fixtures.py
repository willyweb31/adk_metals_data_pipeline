"""Regenerate the trimmed FRED fixtures used by the unit tests.

Values follow simple formulas so expected metrics can be checked by hand:
  WPU10:  200 + i   for month i = 0 (2019-01) .. 29 (2021-06); 2020-07 is "."
  WPU101: 300 + 3i  for the same months, no missing values
  PCOPPUSDM: 6000 + 100i  U.S. dollars per metric ton, no missing values
Run:  python tests/fixtures/make_fixtures.py
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
MONTHS = [f"{2019 + i // 12}-{i % 12 + 1:02d}-01" for i in range(30)]


def wrap(series_id: str, title: str, units: str, values: list[str]) -> dict:
    return {
        "series_id": series_id,
        "fetched_at": "2021-07-15T12:00:00Z",
        "run_id": "20210715T120000Z",
        "request": {"observation_start": "2019-01-01"},
        "series_info": {"id": series_id, "title": title, "units": units, "frequency": "Monthly"},
        "response": {
            "observation_start": "2019-01-01",
            "units": "lin",
            "count": len(values),
            "observations": [
                {"realtime_start": "2021-07-15", "realtime_end": "2021-07-15",
                 "date": d, "value": v}
                for d, v in zip(MONTHS, values)
            ],
        },
    }


def main() -> None:
    wpu10 = [f"{200 + i:.3f}" for i in range(30)]
    wpu10[MONTHS.index("2020-07-01")] = "."
    wpu101 = [f"{300 + 3 * i:.3f}" for i in range(30)]
    fixtures = {
        "WPU10": wrap("WPU10", "Producer Price Index by Commodity: Metals and Metal Products",
                      "Index 1982=100", wpu10),
        "WPU101": wrap("WPU101",
                       "Producer Price Index by Commodity: Metals and Metal Products: Iron and Steel",
                       "Index 1982=100", wpu101),
        "PCOPPUSDM": wrap("PCOPPUSDM", "Global price of Copper", "U.S. Dollars per Metric Ton",
                          [f"{6000 + 100 * i:.6f}" for i in range(30)]),
    }
    for sid, data in fixtures.items():
        (HERE / f"{sid}.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
