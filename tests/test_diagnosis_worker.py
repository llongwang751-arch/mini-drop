from server.app.diagnosis_worker import DiagnosisWorker, _has_process_binding_authority


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


def test_worker_starts_and_advances_autonomous_sessions() -> None:
    calls = []
    worker = DiagnosisWorker(
        drop_insight_advancer=lambda: calls.append("advance") or 2,
        scope_resolver=lambda: calls.append("scope") or 3,
        autonomous_starter=lambda: calls.append("start") or 1,
        frozen_replay_advancer=lambda: calls.append("replay") or 5,
        outbox_dispatcher=lambda: calls.append("outbox") or 4,
        experiment_evaluator=lambda: calls.append("experiment") or 6,
        diagnosis_expirer=lambda: calls.append("expire") or 7,
    )
    assert worker.process_once() == 28
    assert calls == ["expire", "scope", "start", "advance", "replay", "outbox", "experiment"]

    # The evaluator is time-gated; high-frequency diagnosis polling must not
    # create duplicate long-term metric snapshots.
    assert worker.process_once() == 22
    assert calls.count("experiment") == 1
