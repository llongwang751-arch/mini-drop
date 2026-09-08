from types import SimpleNamespace

from server.app.drop_insight.service import (
    _lock_profile_tool,
    _planner_tool_arguments,
)


def _target() -> dict:
    binding = {
        "agent_id": "agent-java",
        "pid": 42,
        "boot_id": "boot-1",
        "process_start_ticks": 100,
        "pid_namespace_inode": 200,
        "namespace_pid": 42,
        "executable_identity": "java",
        "process_snapshot_id": "snapshot-1",
        "snapshot_generation": 1,
        "snapshot_received_at": "2026-09-07T12:00:00+00:00",
    }
    return {"agent_id": "agent-java", "pid": 42, "process_binding": binding}


def test_jvm_profile_event_follows_diagnosis_intent():
    target = _target()

    assert _planner_tool_arguments(
        "start_jvm_profile", target, query="Java GC 分配压力"
    )["event"] == "alloc"
    assert _planner_tool_arguments(
        "start_jvm_profile", target, query="Java ReentrantLock 锁竞争"
    )["event"] == "lock"
    assert _planner_tool_arguments(
        "start_jvm_profile", target, query="Java 下游响应慢"
    )["event"] == "wall"
    assert _planner_tool_arguments(
        "start_jvm_profile", target, query="Java CPU 热点"
    )["event"] == "cpu"


def test_lock_probe_follows_bound_runtime_identity():
    java = SimpleNamespace(query="锁竞争", target_json=_target())
    native_target = _target()
    native_target["process_binding"]["executable_identity"] = "cpp-hotspot"
    native = SimpleNamespace(query="锁竞争", target_json=native_target)

    assert _lock_profile_tool(java) == "start_jvm_profile"
    assert _lock_profile_tool(native) == "collect_sys_metrics"
