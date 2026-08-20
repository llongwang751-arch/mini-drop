from __future__ import annotations

import hashlib

import pytest

from server.app.artifact_integrity import (
    ArtifactIntegrityError,
    prepare_artifact,
    verify_artifact_bytes,
)
from server.app.analyzer_runner import _upload_outputs


def test_prepare_and_verify_local_artifact(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"mini-drop-evidence")

    artifact = prepare_artifact("task-1", {
        "artifact_type": "raw",
        "local_path": str(path),
        "filename": path.name,
    })

    assert artifact["sha256"] == hashlib.sha256(b"mini-drop-evidence").hexdigest()
    assert artifact["manifest"]["task_id"] == "task-1"
    assert artifact["integrity_status"] == "VERIFIED"
    assert verify_artifact_bytes(artifact)[0] == "VERIFIED"


def test_prepare_rejects_declared_hash_mismatch(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"actual")

    with pytest.raises(ArtifactIntegrityError, match="mismatch"):
        prepare_artifact("task-1", {
            "artifact_type": "raw",
            "local_path": str(path),
            "sha256": "0" * 64,
        })


def test_verify_rejects_tampered_local_artifact(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"before")
    artifact = prepare_artifact("task-1", {
        "artifact_type": "raw", "local_path": str(path),
    })
    path.write_bytes(b"after")

    with pytest.raises(ArtifactIntegrityError, match="SHA-256 mismatch"):
        verify_artifact_bytes(artifact)


def test_legacy_artifact_is_explicitly_unverified():
    status, reason = verify_artifact_bytes({"artifact_type": "raw"})
    assert status == "LEGACY_UNVERIFIED"
    assert "no SHA-256" in reason


def test_analyzer_upload_binds_temporary_output_to_verified_digest(tmp_path, monkeypatch):
    path = tmp_path / "top.json"
    path.write_bytes(b'{"top":[]}')
    uploaded = []
    monkeypatch.setattr(
        "server.app.analyzer_runner.storage.upload_file",
        lambda local_path, bucket, object_key, content_type: uploaded.append(
            (local_path, bucket, object_key, content_type)
        ),
    )
    artifacts = [{
        "artifact_type": "top_json",
        "filename": "top.json",
        "local_path": str(path),
        "content_type": "application/json",
        "size_bytes": path.stat().st_size,
    }]

    _upload_outputs("task-verified", artifacts, "mini-drop")

    assert uploaded
    assert artifacts[0]["local_path"] is None
    assert artifacts[0]["integrity_status"] == "VERIFIED"
    assert artifacts[0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert artifacts[0]["manifest"]["task_id"] == "task-verified"
