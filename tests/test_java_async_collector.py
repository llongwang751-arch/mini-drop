"""Tests for Java async-profiler collector."""

from __future__ import annotations

import os
from unittest import mock

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.java_async import JavaAsyncProfilerCollector


class TestJavaAsyncProfiler:
    @staticmethod
    def _task(**kwargs) -> CollectorTask:
        return CollectorTask(
            id="java_test_001",
            collector_type="java_async",
            target_pid=1234,
            sample_rate=99,
            duration_sec=10,
            options=kwargs.get("options", {}),
        )

    def test_profiler_not_installed(self):
        collector = JavaAsyncProfilerCollector()
        with mock.patch.object(collector, "_find_profiler", return_value=None):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "不可用" in result.reason

    def test_pid_not_exists(self):
        collector = JavaAsyncProfilerCollector()
        with mock.patch.object(collector, "_find_profiler", return_value="/opt/async-profiler/profiler.sh"), \
             mock.patch.object(collector, "_pid_exists", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "PID" in result.reason and "不存在" in result.reason

    def test_not_java_process(self):
        collector = JavaAsyncProfilerCollector()
        with mock.patch.object(collector, "_find_profiler", return_value="/opt/async-profiler/profiler.sh"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch.object(collector, "_is_java_process", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "JVM" in result.reason

    def test_invalid_event(self):
        collector = JavaAsyncProfilerCollector()
        task = self._task(options={"event": "invalid"})
        with mock.patch.object(collector, "_find_profiler", return_value="/opt/async-profiler/profiler.sh"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch.object(collector, "_is_java_process", return_value=True):
            result = collector.collect(task)
        assert result.ok is False
        assert "不支持的 event" in result.reason

    def test_valid_events_are_accepted(self):
        assert "cpu" in JavaAsyncProfilerCollector.VALID_EVENTS
        assert "alloc" in JavaAsyncProfilerCollector.VALID_EVENTS
        assert "lock" in JavaAsyncProfilerCollector.VALID_EVENTS

    def test_modern_asprof_is_executed_directly(self):
        collector = JavaAsyncProfilerCollector()
        assert collector._launcher("/opt/async-profiler/bin/asprof") == [
            "/opt/async-profiler/bin/asprof"
        ]

    def test_legacy_python_launcher_uses_current_interpreter(self):
        collector = JavaAsyncProfilerCollector()
        launcher = collector._launcher("/opt/async-profiler/profiler.py")
        assert os.path.basename(launcher[0]).lower().startswith("python")
        assert launcher[1] == "/opt/async-profiler/profiler.py"

    def test_target_output_uses_mount_namespace_root(self, tmp_path):
        collector = JavaAsyncProfilerCollector()
        target_root = tmp_path / "proc" / "1234" / "root"
        target_root.mkdir(parents=True)
        with mock.patch("os.path.isdir", return_value=True), \
             mock.patch("os.makedirs") as makedirs:
            target, visible = collector._target_output_paths(
                1234, "task_java", "/tmp/local.html",
            )
        assert target == "/tmp/mini-drop/task_java/java_flamegraph.html"
        assert visible.replace("\\", "/").endswith(
            "/proc/1234/root/tmp/mini-drop/task_java/java_flamegraph.html"
        )
        makedirs.assert_called_once()

    def test_target_library_is_copied_into_target_mount_namespace(self):
        collector = JavaAsyncProfilerCollector()
        with mock.patch("os.path.isfile", side_effect=[True, False]), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("os.makedirs") as makedirs, \
             mock.patch("shutil.copy2") as copy2:
            target = collector._target_library_path(
                "/opt/async-profiler/bin/asprof", 1234,
            )

        assert target == "/tmp/mini-drop/runtime/libasyncProfiler.so"
        makedirs.assert_called_once()
        source, visible = copy2.call_args.args
        assert source.replace("\\", "/").endswith(
            "/opt/async-profiler/lib/libasyncProfiler.so"
        )
        assert visible.replace("\\", "/").endswith(
            "/proc/1234/root/tmp/mini-drop/runtime/libasyncProfiler.so"
        )

    def test_stop_profiler_is_best_effort_and_uses_argv(self):
        collector = JavaAsyncProfilerCollector()
        with mock.patch("subprocess.run") as run:
            collector._stop_profiler("/opt/async-profiler/bin/asprof", 1234)
        assert run.call_args.args[0] == [
            "/opt/async-profiler/bin/asprof", "stop", "-o", "flat", "1234",
        ]
        assert run.call_args.kwargs["check"] is False
