"""S3Store against a mocked boto3 client: no network, no credentials."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from metals_pipeline.storage import S3Store, get_store


def make_store():
    client = MagicMock()
    return S3Store("metals", client=client), client


def test_put_get_exists_use_the_same_object_paths():
    store, client = make_store()
    client.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"hello"))}

    uri = store.put_text("/processed/metals_ppi/run_id=R/latest.csv", "a,b\n", "text/csv")

    client.put_object.assert_called_once_with(
        Bucket="metals", Key="processed/metals_ppi/run_id=R/latest.csv",
        Body=b"a,b\n", ContentType="text/csv; charset=utf-8",
    )
    assert uri == "s3://metals/processed/metals_ppi/run_id=R/latest.csv"
    assert store.get_text("runs/R/manifest.json") == "hello"
    client.get_object.assert_called_with(Bucket="metals", Key="runs/R/manifest.json")
    assert store.exists("runs/R/manifest.json")
    assert store.location == "s3://metals"


def test_exists_false_on_404_and_reraises_other_errors():
    store, client = make_store()
    client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    assert not store.exists("runs/R/manifest.json")

    client.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
    with pytest.raises(ClientError):
        store.exists("runs/R/manifest.json")


def test_rejects_path_escape():
    store, _ = make_store()
    with pytest.raises(ValueError):
        store.put_text("../secret.txt", "x")


def test_get_store_requires_bucket(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("Bucket", raising=False)
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        get_store()


def test_get_store_rejects_half_set_keys(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "metals")
    monkeypatch.setenv("S3_AccessKeyId", "AKIAEXAMPLE")
    monkeypatch.delenv("S3_SecretAccessKey", raising=False)
    with pytest.raises(RuntimeError, match="S3_SecretAccessKey"):
        get_store()
