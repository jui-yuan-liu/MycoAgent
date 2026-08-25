from __future__ import annotations

import asyncio
import io
import os
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ArtifactStore(Protocol):
    """Object store for child outputs. Manager never sees these blobs."""

    async def put(
        self,
        *,
        group: str,
        job_id: str,
        subtask_id: str,
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Store bytes and return an artifact id (object key), never a local path."""

    async def get(self, artifact_id: str) -> bytes:
        """Fetch bytes by artifact id."""


def artifact_key(group: str, job_id: str, subtask_id: str, name: str) -> str:
    safe_name = name.replace("\\", "/").lstrip("/")
    if ".." in safe_name.split("/"):
        raise ValueError("artifact name must not contain '..'")
    return f"{group}/{job_id}/{subtask_id}/{safe_name}"


class MemoryArtifactStore:
    """In-process fake S3 for tests and local runs without MinIO."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(
        self,
        *,
        group: str,
        job_id: str,
        subtask_id: str,
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        del content_type
        key = artifact_key(group, job_id, subtask_id, name)
        self.objects[key] = data
        return key

    async def get(self, artifact_id: str) -> bytes:
        try:
            return self.objects[artifact_id]
        except KeyError as exc:
            raise KeyError(f"artifact not found: {artifact_id}") from exc


class S3ArtifactStore:
    """MinIO / S3-compatible store. Bucket keys are prefixed by group."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        *,
        secure: bool | None = None,
        region: str = "us-east-1",
    ) -> None:
        from minio import Minio

        trimmed = endpoint.strip()
        secure_flag = trimmed.startswith("https://") if secure is None else secure
        host = trimmed.removeprefix("https://").removeprefix("http://").rstrip("/")
        self.bucket = bucket
        self._client = Minio(
            host,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure_flag,
            region=region,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        import time

        last: Exception | None = None
        for _ in range(20):
            try:
                if not self._client.bucket_exists(self.bucket):
                    self._client.make_bucket(self.bucket)
                return
            except Exception as exc:  # noqa: BLE001 — wait for MinIO to accept connections
                last = exc
                time.sleep(0.5)
        raise RuntimeError(f"S3/MinIO bucket not ready: {last}") from last

    async def put(
        self,
        *,
        group: str,
        job_id: str,
        subtask_id: str,
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        key = artifact_key(group, job_id, subtask_id, name)

        def _put() -> None:
            self._client.put_object(
                self.bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )

        await asyncio.to_thread(_put)
        return key

    async def get(self, artifact_id: str) -> bytes:
        def _get() -> bytes:
            response = self._client.get_object(self.bucket, artifact_id)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(_get)


async def upload_workspace_files(
    store: ArtifactStore,
    workspace: Any,
    *,
    group: str,
    job_id: str,
    subtask_id: str,
) -> list[str]:
    """Upload workspace files. Returns artifact ids (object keys), never local paths."""
    from pathlib import Path

    ids: list[str] = []
    root = Path(workspace.root)
    for path in workspace.iter_files():
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        artifact_id = await store.put(
            group=group,
            job_id=job_id,
            subtask_id=subtask_id,
            name=rel,
            data=data,
        )
        ids.append(artifact_id)
    return ids


def artifact_store_from_env() -> ArtifactStore:
    endpoint = os.environ.get("MYCOAGENT_S3_ENDPOINT", "").strip()
    if not endpoint:
        return MemoryArtifactStore()
    return S3ArtifactStore(
        endpoint=endpoint,
        access_key=os.environ.get("MYCOAGENT_S3_ACCESS_KEY", "minioadmin"),
        secret_key=os.environ.get("MYCOAGENT_S3_SECRET_KEY", "minioadmin"),
        bucket=os.environ.get("MYCOAGENT_S3_BUCKET", "mycoagent"),
        secure=_env_bool(os.environ.get("MYCOAGENT_S3_SECURE")),
    )


def _env_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}
