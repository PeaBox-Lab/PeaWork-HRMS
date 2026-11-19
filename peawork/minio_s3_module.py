"""
MinIO / AWS S3 Object Storage Support for R5.

Provides a unified interface for MinIO (self-hosted) and Amazon S3 cloud storage.
This module is optional — when ``object_storage_enabled`` is ``False`` (default) it
is a no‑op and all existing file storage (local ``storage_dir``) continues to work unchanged.
"""

from __future__ import annotations

import io
import os
from datetime import datetime, timedelta
from typing import Any, BinaryIO, Optional, Union

from .config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Provider‑agnostic helpers
# ---------------------------------------------------------------------------

def _normalize_key(key: str) -> str:
    """Ensure a storage key starts without a leading slash and uses forward slashes."""
    k = key.lstrip("/")
    return k.replace("\\", "/")


# ---------------------------------------------------------------------------
# MinIO implementation (self‑hosted)
# ---------------------------------------------------------------------------

# MinIO client is lazily initialised; import is guarded so the module works
# when minio is not installed (no‑op fallback).
_minio_client: Optional[object] = None
_minio_initialised: bool = False


def _init_minio() -> None:
    global _minio_client, _minio_initialised
    if _minio_initialised:
        return
    try:
        from minio import Minio  # type: ignore[import]

        endpoint = settings.object_storage_endpoint or "localhost:9000"
        access_key = settings.object_storage_access_key or "minioadmin"
        secret_key = settings.object_storage_secret_key or "minioadmin"
        secure = settings.object_storage_secure if settings.object_storage_secure is not None else endpoint.startswith("https")
        _minio_client = Minio(endpoint, access_key_id=access_key, secret_access_key=secret_key, secure=secure)
        _minio_initialised = True
    except ImportError:
        _minio_client = None
        _minio_initialised = True


def minio_object_exists(bucket: str, key: str) -> bool:
    """Check if an object exists in MinIO."""
    _init_minio()
    if _minio_client is None:
        return False
    try:
        _minio_client.stat_object(bucket, key)
        return True
    except Exception:  # broad – we just want a True/False result
        return False


def minio_put_object(bucket: str, key: str, data: bytes, length: int, content_type: str = "application/octet-stream") -> None:
    """Upload an object to MinIO."""
    _init_minio()
    if _minio_client is None:
        return
    _minio_client.put_object(bucket, key, io.BytesIO(data), length, content_type=content_type)


def minio_get_object(bucket: str, key: str) -> bytes:
    """Download an object from MinIO."""
    _init_minio()
    if _minio_client is None:
        raise FileNotFoundError("MinIO not initialised")
    return _minio_client.get_object(bucket, key).data


def minio_remove_object(bucket: str, key: str) -> None:
    """Remove an object from MinIO."""
    _init_minio()
    if _minio_client is None:
        return
    _minio_client.remove_object(bucket, key)


def minio_list_objects(bucket: str, prefix: str = "", recursive: bool = True) -> list:
    """List objects in a MinIO bucket."""
    _init_minio()
    if _minio_client is None:
        return []
    objects = []
    for obj in _minio_client.list_objects(bucket, prefix=prefix, recursive=recursive):
        objects.append(
            {
                "key": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified,
                "etag": obj.etag,
            }
        )
    return objects


# ---------------------------------------------------------------------------
# AWS S3 implementation
# ---------------------------------------------------------------------------

_s3_client: Optional[object] = None
_s3_resource: Optional[object] = None
_s3_initialised: bool = False


def _init_s3() -> None:
    global _s3_client, _s3_resource, _s3_initialised
    if _s3_initialised:
        return
    try:
        import boto3  # type: ignore[import]
        from botocore.exceptions import ClientError  # type: ignore[import]

        endpoint_url = settings.object_storage_endpoint
        region_name = settings.object_storage_region or "us-east-1"
        access_key = settings.object_storage_access_key
        secret_key = settings.object_storage_secret_key
        if endpoint_url:
            _s3_client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            _s3_resource = boto3.resource(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        else:
            _s3_client = boto3.client(
                "s3",
                region_name=region_name,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            _s3_resource = boto3.resource(
                "s3",
                region_name=region_name,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        _s3_initialised = True
    except ImportError:
        _s3_client = None
        _s3_resource = None
        _s3_initialised = True


def s3_object_exists(bucket: str, key: str) -> bool:
    """Check if an object exists in S3."""
    _init_s3()
    if _s3_client is None:
        return False
    from botocore.exceptions import ClientError  # type: ignore[import]

    try:
        _s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        return e.response["Error"]["Code"] == "404"


def s3_put_object(bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Upload an object to S3."""
    _init_s3()
    if _s3_client is None:
        return
    _s3_client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


def s3_get_object(bucket: str, key: str) -> bytes:
    """Download an object from S3."""
    _init_s3()
    if _s3_client is None:
        raise FileNotFoundError("S3 not initialised")
    return _s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()


def s3_remove_object(bucket: str, key: str) -> None:
    """Remove an object from S3."""
    _init_s3()
    if _s3_client is None:
        return
    _s3_client.delete_object(Bucket=bucket, Key=key)


def s3_list_objects(bucket: str, prefix: str = "", delimiter: str = "") -> list:
    """List objects in an S3 bucket."""
    _init_s3()
    if _s3_client is None:
        return []
    from botocore.exceptions import ClientError  # type: ignore[import]

    objects = []
    try:
        paginator = _s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter=delimiter):
            for obj in page.get("Contents", []):
                objects.append(
                    {
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                        "etag": obj.get("ETag", "").strip('"'),
                    }
                )
    except ClientError:
        pass
    return objects


# ---------------------------------------------------------------------------
# Unified storage adapter
# ---------------------------------------------------------------------------

class ObjectStorageAdapter:
    """Unified adapter for MinIO and S3 storage providers.

    Usage::
        from peawork.storage import get_storage_adapter
        adapter = get_storage_adapter()
        adapter.put_object("bucket", "path/to/file.pdf", pdf_bytes)
        data = adapter.get_object("bucket", "path/to/file.pdf")
    """

    def __init__(self) -> None:
        self.enabled = settings.object_storage_enabled
        self.provider = settings.object_storage_provider or "local"
        self.bucket = settings.object_storage_bucket or "peawork"
        self._minio_initialised = False
        self._s3_initialised = False

    # ---- Lazy‑load MinIO ----

    @property
    def _minio(self):
        if self._minio_initialised:
            return _minio_client  # type: ignore[assignment]
        if self.provider == "minio":
            _init_minio()
            self._minio_initialised = True
        return _minio_client  # type: ignore[assignment]

    @property
    def _s3(self):
        if self._s3_initialised:
            return _s3_client  # type: ignore[assignment]
        if self.provider == "s3":
            _init_s3()
            self._s3_initialised = True
        return _s3_client  # type: ignore[assignment]

    # ---- Public API ----

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Store an object. Falls back to local storage if provider is unavailable."""
        key = _normalize_key(key)
        if not self.enabled or self.provider == "local":
            local_path = os.path.join(settings.storage_dir, key)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(data)
            return

        if self.provider == "minio" and self._minio:
            bucket = self.bucket or "peawork"
            minio_put_object(bucket, key, data, len(data), content_type)
        elif self.provider == "s3" and self._s3:
            bucket = self.bucket or "peawork"
            s3_put_object(bucket, key, data, content_type)
        else:
            # Fallback to local
            local_path = os.path.join(settings.storage_dir, key)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(data)

    def get_object(self, key: str) -> bytes:
        """Retrieve an object. Falls back to local storage if provider is unavailable."""
        key = _normalize_key(key)
        if not self.enabled or self.provider == "local":
            local_path = os.path.join(settings.storage_dir, key)
            if os.path.exists(local_path):
                with open(local_path, "rb") as f:
                    return f.read()
            raise FileNotFoundError(f"Object not found: {key}")

        if self.provider == "minio" and self._minio:
            bucket = self.bucket or "peawork"
            return minio_get_object(bucket, key)
        elif self.provider == "s3" and self._s3:
            bucket = self.bucket or "peawork"
            return s3_get_object(bucket, key)
        else:
            # Fallback to local
            local_path = os.path.join(settings.storage_dir, key)
            if os.path.exists(local_path):
                with open(local_path, "rb") as f:
                    return f.read()
            raise FileNotFoundError(f"Object not found: {key}")

    def remove_object(self, key: str) -> None:
        """Remove an object. Falls back to local storage if provider is unavailable."""
        key = _normalize_key(key)
        if not self.enabled or self.provider == "local":
            local_path = os.path.join(settings.storage_dir, key)
            if os.path.exists(local_path):
                os.remove(local_path)
            return

        if self.provider == "minio" and self._minio:
            bucket = self.bucket or "peawork"
            minio_remove_object(bucket, key)
        elif self.provider == "s3" and self._s3:
            bucket = self.bucket or "peawork"
            s3_remove_object(bucket, key)
        else:
            # Fallback to local
            local_path = os.path.join(settings.storage_dir, key)
            if os.path.exists(local_path):
                os.remove(local_path)

    def list_objects(self, prefix: str = "", recursive: bool = True) -> list:
        """List objects in the bucket."""
        if not self.enabled or self.provider == "local":
            base = settings.storage_dir
            results = []
            for root, _dirs, files in os.walk(base):
                for fname in files:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, base)
                    if prefix and not rel.startswith(prefix):
                        continue
                    results.append(
                        {
                            "key": rel.replace(os.sep, "/"),
                            "size": os.path.getsize(full),
                            "last_modified": datetime.fromtimestamp(os.path.getmtime(full)),
                        }
                    )
            return results

        if self.provider == "minio" and self._minio:
            return minio_list_objects(self.bucket or "peawork", prefix, recursive)
        elif self.provider == "s3" and self._s3:
            return s3_list_objects(self.bucket or "peawork", prefix, "")
        return []


# ---------------------------------------------------------------------------
# Convenience functions (global accessor)
# ---------------------------------------------------------------------------

def get_storage_adapter() -> ObjectStorageAdapter:
    """Return the global ``ObjectStorageAdapter`` instance for the current config."""
    return ObjectStorageAdapter()


def put_object(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Convenience: store an object via the global adapter."""
    get_storage_adapter().put_object(key, data, content_type)

def get_object(key: str) -> bytes:
    """Convenience: retrieve an object via the global adapter."""
    return get_storage_adapter().get_object(key)

def remove_object(key: str) -> None:
    """Convenience: remove an object via the global adapter."""
    get_storage_adapter().remove_object(key)

def list_objects(prefix: str = "", recursive: bool = True) -> list:
    """Convenience: list objects via the global adapter."""
    return get_storage_adapter().list_objects(prefix, recursive)

__all__ = [
    "ObjectStorageAdapter",
    "get_storage_adapter",
    "put_object",
    "get_object",
    "remove_object",
    "list_objects",
]