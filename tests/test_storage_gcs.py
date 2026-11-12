"""GCSStore against a mocked client: no network, no credentials."""

from unittest.mock import MagicMock

from metals_pipeline.storage import GCSStore


def test_put_get_exists_use_the_same_object_paths():
    client = MagicMock()
    bucket = client.bucket.return_value
    blob = bucket.blob.return_value
    blob.download_as_bytes.return_value = b"hello"
    blob.exists.return_value = True

    store = GCSStore("my-bucket", client=client)
    uri = store.put_text("/processed/metals_ppi/run_id=R/latest.csv", "a,b\n", "text/csv")

    client.bucket.assert_called_once_with("my-bucket")
    bucket.blob.assert_called_with("processed/metals_ppi/run_id=R/latest.csv")
    blob.upload_from_string.assert_called_once_with(
        b"a,b\n", content_type="text/csv; charset=utf-8"
    )
    assert uri == "gs://my-bucket/processed/metals_ppi/run_id=R/latest.csv"
    assert store.get_text("runs/R/manifest.json") == "hello"
    assert store.exists("runs/R/manifest.json")
    assert store.location == "gs://my-bucket"
