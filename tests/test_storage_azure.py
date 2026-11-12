"""AzureBlobStore against a mocked container client: no network, no credentials."""

from unittest.mock import MagicMock

import pytest

from metals_pipeline.storage import AzureBlobStore, get_store

URL = "https://myacct.blob.core.windows.net/metals"


def make_store():
    container = MagicMock()
    container.url = URL
    return AzureBlobStore("metals", client=container), container


def test_put_get_exists_use_the_same_object_paths():
    store, container = make_store()
    container.download_blob.return_value.readall.return_value = b"hello"
    container.get_blob_client.return_value.exists.return_value = True

    uri = store.put_text("/processed/metals_ppi/run_id=R/latest.csv", "a,b\n", "text/csv")

    kwargs = container.upload_blob.call_args.kwargs
    assert kwargs["name"] == "processed/metals_ppi/run_id=R/latest.csv"
    assert kwargs["data"] == b"a,b\n"
    assert kwargs["overwrite"] is True
    assert kwargs["content_settings"].content_type == "text/csv; charset=utf-8"
    assert uri == f"{URL}/processed/metals_ppi/run_id=R/latest.csv"

    assert store.get_text("runs/R/manifest.json") == "hello"
    container.download_blob.assert_called_with("runs/R/manifest.json")
    assert store.exists("runs/R/manifest.json")
    container.get_blob_client.assert_called_with("runs/R/manifest.json")
    assert store.location == URL


def test_get_store_requires_container(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "azure")
    monkeypatch.delenv("AZURE_STORAGE_CONTAINER", raising=False)
    with pytest.raises(RuntimeError, match="AZURE_STORAGE_CONTAINER"):
        get_store()


def test_get_store_requires_some_credential(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "azure")
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "metals")
    for var in ("AZURE_STORAGE_ACCOUNT_URL", "AZURE_STORAGE_ACCOUNT",
                "AZURE_STORAGE_CONNECTION_STRING"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="AZURE_STORAGE_ACCOUNT_URL"):
        get_store()
