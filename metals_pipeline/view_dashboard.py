"""Open the latest dashboard from whichever storage backend is configured.

    python -m metals_pipeline.view_dashboard              # latest run
    python -m metals_pipeline.view_dashboard <run_id>     # a specific run

For cloud backends (s3, azure, gcs) the HTML is downloaded with the same
credentials the pipeline uses, saved under output/downloaded/, and opened in
the default browser. No public bucket or signed link is needed.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

from . import config
from .storage import get_store


def main(argv: list[str]) -> int:
    run_id = argv[0] if argv else None
    path = config.site_path(run_id) if run_id else config.SITE_LATEST_PATH
    store = get_store()

    if store.backend == "local":
        target = Path(store.uri(path))
        if not target.is_file():
            print(f"No dashboard at {target}. Run the pipeline first.")
            return 1
    else:
        if not store.exists(path):
            print(f"No dashboard at {store.uri(path)}. Run the pipeline first.")
            return 1
        target = config.output_dir() / "downloaded" / (run_id or "latest") / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(store.get_bytes(path))
        print(f"Downloaded {store.uri(path)}")

    print(f"Opening {target}")
    webbrowser.open(target.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
