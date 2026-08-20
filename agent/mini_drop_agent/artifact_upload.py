"""Optional Agent-side artifact upload to MinIO."""

from __future__ import annotations

import hashlib
import os
import time

from agent.mini_drop_agent.config import AgentConfig


def maybe_upload_artifacts(task_id: str, artifacts: list[dict], config: AgentConfig) -> list[dict]:
    if not config.upload_artifacts:
        return artifacts
    client = _minio_client(config)
    result: list[dict] = []
    for artifact in artifacts:
        result.append(_upload_one(client, task_id, artifact, config))
    return result


def _minio_client(config: AgentConfig):
    from minio import Minio

    endpoint, inferred_secure = _normalize_endpoint(config.minio_endpoint)
    secure_raw = os.getenv("MINIO_SECURE", "").strip().lower()
    if secure_raw:
        secure = secure_raw in {"1", "true", "yes", "on"}
    else:
        secure = inferred_secure

    return Minio(
        endpoint=endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=secure,
    )


def _normalize_endpoint(endpoint: str) -> tuple[str, bool]:
    endpoint = (endpoint or "").strip()
    if endpoint.startswith("https://"):
        return endpoint.removeprefix("https://"), True
    if endpoint.startswith("http://"):
        return endpoint.removeprefix("http://"), False
    return endpoint, False


def _upload_with_retry(
    client,
    bucket_name: str,
    object_name: str,
    file_path: str,
    content_type: str,
    max_attempts: int = 3,
) -> None:
    """Upload to MinIO with exponential backoff (0.5/1/2s), raising the last error."""
    delays = (0.5, 1.0, 2.0)
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            client.fput_object(
                bucket_name=bucket_name,
                object_name=object_name,
                file_path=file_path,
                content_type=content_type,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                time.sleep(delays[attempt])
    if last_error is not None:
        raise last_error


def _upload_one(client, task_id: str, artifact: dict, config: AgentConfig) -> dict:
    item = dict(artifact)
    local_path = item.get("local_path")
    if not local_path or not os.path.isfile(local_path):
        return item

    filename = item.get("filename") or os.path.basename(local_path)
    object_key = item.get("object_key") or f"tasks/{task_id}/{filename}"
    content_type = item.get("content_type") or "application/octet-stream"
    sha256, size_bytes = _hash_file(local_path)
    _upload_with_retry(client, config.minio_bucket, object_key, local_path, content_type)
    item["bucket"] = config.minio_bucket
    item["object_key"] = object_key
    item["size_bytes"] = size_bytes
    item["sha256"] = sha256
    item["manifest"] = {
        "manifest_version": "mini-drop.artifact.v1",
        "task_id": task_id,
        "artifact_type": item.get("artifact_type", "raw"),
        "bucket": config.minio_bucket,
        "object_key": object_key,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "producer": "mini-drop-agent",
    }
    return item


def _hash_file(path: str, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
