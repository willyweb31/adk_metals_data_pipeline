import pytest

from metals_pipeline import config
from metals_pipeline.storage import LocalStore, get_store

RUN = "20261115T180211Z"


def test_writes_and_reads_contract_paths(tmp_path):
    store = LocalStore(tmp_path)
    paths = {
        config.raw_path("WPU10", "2026-11-15", RUN):
            "raw/fred/WPU10/ingest_date=2026-11-15/20261115T180211Z.json",
        config.processed_path(RUN, "monthly.csv"):
            "processed/metals_ppi/run_id=20261115T180211Z/monthly.csv",
        config.site_path(RUN): "site/run_id=20261115T180211Z/index.html",
        config.SITE_LATEST_PATH: "site/latest/index.html",
        config.manifest_path(RUN): "runs/20261115T180211Z/manifest.json",
    }
    for built, expected in paths.items():
        assert built == expected
        store.put_text(built, f"content of {built}")
        assert (tmp_path / expected).is_file()
        assert store.exists(built)
        assert store.get_text(built) == f"content of {built}"


def test_json_roundtrip(tmp_path):
    store = LocalStore(tmp_path)
    store.put_json("runs/x/manifest.json", {"a": 1, "b": [1, 2]})
    assert store.get_json("runs/x/manifest.json") == {"a": 1, "b": [1, 2]}


@pytest.mark.parametrize("bad", ["../escape.txt", "raw/../../escape.txt", ""])
def test_rejects_paths_outside_root(tmp_path, bad):
    with pytest.raises(ValueError):
        LocalStore(tmp_path).put_text(bad, "nope")


def test_get_store_uses_env(local_output):
    store = get_store()
    assert store.backend == "local"
    assert store.location == str(local_output.resolve())
