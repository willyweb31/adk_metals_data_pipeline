"""Object storage backends with one interface.

LocalStore writes under `output/`; S3Store writes to an Amazon S3 bucket;
AzureBlobStore writes to an Azure Blob Storage container; GCSStore writes to a
Google Cloud Storage bucket. All use the same object paths, so switching is one
env var: STORAGE_BACKEND=local|s3|azure|gcs.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from typing import Any

from . import config


def _clean(path: str) -> str:
    """Normalise an object path and refuse anything that escapes the root."""
    p = PurePosixPath(path.replace("\\", "/").lstrip("/"))
    if not p.parts or ".." in p.parts:
        raise ValueError(f"Invalid object path: {path!r}")
    return str(p)


class Store(ABC):
    backend: str
    location: str  # local folder, s3://bucket, gs://bucket or container URL

    @abstractmethod
    def put_bytes(self, path: str, data: bytes, content_type: str) -> str:
        """Write an object and return its URI."""

    @abstractmethod
    def get_bytes(self, path: str) -> bytes: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def uri(self, path: str) -> str: ...

    # Convenience helpers shared by all backends.
    def put_text(self, path: str, text: str, content_type: str = "text/plain") -> str:
        return self.put_bytes(path, text.encode("utf-8"), f"{content_type}; charset=utf-8")

    def get_text(self, path: str) -> str:
        return self.get_bytes(path).decode("utf-8")

    def put_json(self, path: str, obj: Any) -> str:
        return self.put_text(path, json.dumps(obj, indent=2, default=str), "application/json")

    def get_json(self, path: str) -> Any:
        return json.loads(self.get_text(path))


class LocalStore(Store):
    backend = "local"

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.location = str(self.root)

    def _full(self, path: str) -> Path:
        return self.root / _clean(path)

    def put_bytes(self, path: str, data: bytes, content_type: str) -> str:
        full = self._full(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        return self.uri(path)

    def get_bytes(self, path: str) -> bytes:
        return self._full(path).read_bytes()

    def exists(self, path: str) -> bool:
        return self._full(path).is_file()

    def uri(self, path: str) -> str:
        return str(self._full(path))


class GCSStore(Store):
    backend = "gcs"

    def __init__(self, bucket: str, project: str | None = None, client: Any = None):
        if client is None:
            from google.cloud import storage  # imported lazily: local mode never needs it

            client = storage.Client(project=project)
        self.bucket_name = bucket
        self.location = f"gs://{bucket}"
        self._bucket = client.bucket(bucket)

    def put_bytes(self, path: str, data: bytes, content_type: str) -> str:
        blob = self._bucket.blob(_clean(path))
        blob.upload_from_string(data, content_type=content_type)
        return self.uri(path)

    def get_bytes(self, path: str) -> bytes:
        return self._bucket.blob(_clean(path)).download_as_bytes()

    def exists(self, path: str) -> bool:
        return self._bucket.blob(_clean(path)).exists()

    def uri(self, path: str) -> str:
        return f"gs://{self.bucket_name}/{_clean(path)}"


class AzureBlobStore(Store):
    """Azure Blob Storage container.

    Auth, in order of preference:
      1. AZURE_STORAGE_ACCOUNT_URL (or AZURE_STORAGE_ACCOUNT) + DefaultAzureCredential,
         which picks up `az login`, so no key ever sits in a file.
      2. AZURE_STORAGE_CONNECTION_STRING from .env (contains the account key;
         .env is gitignored).
    """

    backend = "azure"

    def __init__(self, container: str, account_url: str | None = None,
                 connection_string: str | None = None, client: Any = None):
        if client is None:
            # Imported lazily: other backends never need the Azure SDK.
            from azure.storage.blob import BlobServiceClient

            if account_url:
                from azure.identity import DefaultAzureCredential

                service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
            elif connection_string:
                service = BlobServiceClient.from_connection_string(connection_string)
            else:
                raise RuntimeError(
                    "STORAGE_BACKEND=azure needs AZURE_STORAGE_ACCOUNT_URL (with `az login`) "
                    "or AZURE_STORAGE_CONNECTION_STRING in .env."
                )
            client = service.get_container_client(container)
        self.container_name = container
        self._container = client
        self.location = str(client.url).rstrip("/")

    def put_bytes(self, path: str, data: bytes, content_type: str) -> str:
        from azure.storage.blob import ContentSettings

        self._container.upload_blob(
            name=_clean(path), data=data, overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        return self.uri(path)

    def get_bytes(self, path: str) -> bytes:
        return self._container.download_blob(_clean(path)).readall()

    def exists(self, path: str) -> bool:
        return bool(self._container.get_blob_client(_clean(path)).exists())

    def uri(self, path: str) -> str:
        return f"{self.location}/{_clean(path)}"


class S3Store(Store):
    """Amazon S3 bucket.

    Credentials: S3_AccessKeyId / S3_SecretAccessKey from .env if set, otherwise
    boto3's default chain (AWS_ACCESS_KEY_ID env vars, ~/.aws, SSO, IAM role).
    """

    backend = "s3"

    def __init__(self, bucket: str, region: str | None = None,
                 credentials: tuple[str, str] | None = None, client: Any = None):
        if client is None:
            import boto3  # imported lazily: other backends never need it

            kwargs: dict[str, Any] = {"region_name": region}
            if credentials:
                kwargs["aws_access_key_id"], kwargs["aws_secret_access_key"] = credentials
            client = boto3.client("s3", **kwargs)
        self.bucket_name = bucket
        self.location = f"s3://{bucket}"
        self._s3 = client

    def put_bytes(self, path: str, data: bytes, content_type: str) -> str:
        self._s3.put_object(Bucket=self.bucket_name, Key=_clean(path), Body=data,
                            ContentType=content_type)
        return self.uri(path)

    def get_bytes(self, path: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket_name, Key=_clean(path))["Body"].read()

    def exists(self, path: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket_name, Key=_clean(path))
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def uri(self, path: str) -> str:
        return f"s3://{self.bucket_name}/{_clean(path)}"


def get_store() -> Store:
    backend = config.storage_backend()
    if backend == "local":
        return LocalStore(config.output_dir())
    if backend == "s3":
        return S3Store(config.s3_bucket(), region=config.aws_region(),
                       credentials=config.s3_credentials())
    if backend == "gcs":
        return GCSStore(config.gcs_bucket(), project=config.gcp_project())
    if backend == "azure":
        return AzureBlobStore(
            config.azure_container(),
            account_url=config.azure_account_url(),
            connection_string=config.azure_connection_string(),
        )
    raise RuntimeError(
        f"Unknown STORAGE_BACKEND {backend!r}; use 'local', 's3', 'gcs', or 'azure'."
    )
