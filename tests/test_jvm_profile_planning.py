from types import SimpleNamespace

from server.app.drop_insight.service import (
    _jvm_profile_event,
    _lock_profile_tool,
    _planner_tool_arguments,
    _runtime_compatible_tools,
    _runtime_tool_is_compatible,
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


def test_later_gc_counterexample_does_not_turn_lock_capture_into_allocation():
    query = "Java 服务吞吐下降并疑似锁竞争。先比较 CPU、线程和上下文切换，再用 JVM Profile 定位锁路径，最后寻找 GC、I/O 或纯计算热点反证并观察恢复。"
    assert _planner_tool_arguments("start_jvm_profile", _target(), query=query)["event"] == "lock"
    assert _jvm_profile_event("Java 锁竞争，并非 GC 分配压力") == "lock"
    assert _jvm_profile_event("Java CPU 热点，排除 GC") == "cpu"
    assert _jvm_profile_event("Java CPU 热点。最后寻找 GC、I/O 或调度反证。") == "cpu"
    assert _jvm_profile_event("Java 下游响应慢。最后排除 GC。") == "wall"
    assert _jvm_profile_event("Java 服务GC抖动。") == "alloc"
    assert _jvm_profile_event("Java 服务CPU持续升高。最后寻找GC反证。") == "cpu"


def test_unknown_business_executable_cannot_fall_back_to_jvm_attach():
    target = _target()
    target['process_binding']['executable_identity'] = '/opt/java-service/filebrowser'
    target['service'] = 'java-api.service'
    diagnosis = SimpleNamespace(query='Java JVM wall profiling', target_json=target)
    tools = ['collect_sys_metrics', 'start_perf_profile', 'start_jvm_profile',
             'start_pyspy_profile', 'collect_go_profile']
    assert _runtime_compatible_tools(diagnosis, tools) == ['collect_sys_metrics', 'start_perf_profile']
    assert not _runtime_tool_is_compatible(diagnosis, 'start_jvm_profile')
    assert _runtime_tool_is_compatible(diagnosis, 'collect_sys_metrics')


def test_uwsgi_has_python_profiler_but_no_jvm_attach():
    target = _target()
    target['process_binding']['executable_identity'] = '/usr/bin/uwsgi'
    diagnosis = SimpleNamespace(query='等待时间', target_json=target)
    assert _runtime_tool_is_compatible(diagnosis, 'start_pyspy_profile')
    assert not _runtime_tool_is_compatible(diagnosis, 'start_jvm_profile')
