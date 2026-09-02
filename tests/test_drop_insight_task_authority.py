from server.app.drop_insight.service import _task_upload_object_keys


def test_pyspy_upload_authority_is_attempt_scoped() -> None:
    assert _task_upload_object_keys("task-1", "attempt-1", "pyspy") == [
        "tasks/task-1/attempts/attempt-1/raw/pyspy-speedscope.json",
        "tasks/task-1/attempts/attempt-1/manifest.json",
    ]


def test_ebpf_upload_authority_covers_the_generated_contract() -> None:
    assert _task_upload_object_keys("task-2", "attempt-2", "ebpf_io") == [
        "tasks/task-2/attempts/attempt-2/raw/ebpf_metrics.json",
        "tasks/task-2/attempts/attempt-2/raw/io_latency.txt",
        "tasks/task-2/attempts/attempt-2/manifest.json",
    ]
