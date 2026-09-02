from server.app.diagnosis_worker import _has_process_binding_authority


def test_process_binding_authority_rejects_legacy_target() -> None:
    assert not _has_process_binding_authority(
        {"agent_id": "agent-1", "pid": 42, "service": "legacy"}
    )


def test_process_binding_authority_accepts_attested_target() -> None:
    assert _has_process_binding_authority(
        {
            "process_binding": {
                "agent_id": "agent-1",
                "pid": 42,
                "boot_id": "boot-1",
                "process_start_ticks": 123,
                "pid_namespace_inode": 456,
                "namespace_pid": 42,
                "executable_identity": "/usr/bin/python3",
                "process_snapshot_id": "psnap-1",
                "snapshot_generation": 7,
                "snapshot_received_at": "2026-09-01T00:00:00Z",
            }
        }
    )
