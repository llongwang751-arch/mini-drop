"""py-spy 用户态采集器：对 Python 进程采样并输出 speedscope 原始栈。

py-spy 通过读取目标进程内存直接获取 Python 调用栈，
无需修改目标代码或重启进程。
"""

from __future__ import annotations

import os
import shutil
import subprocess

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


class PySpyCollector:
    """py-spy sampling profiler。"""

    OUTPUT_BASE = "/tmp/mini-drop"

    def collect(self, task: CollectorTask) -> CollectorResult:
        pyspy = shutil.which("py-spy")
        if pyspy is None:
            return CollectorResult(
                ok=False,
                reason="py-spy 命令不可用，请通过 pip install py-spy 安装",
            )

        if not self._pid_exists(task.target_pid):
            return CollectorResult(
                ok=False,
                reason=f"目标 PID {task.target_pid} 不存在",
            )

        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)
        speedscope_path = os.path.join(output_dir, "pyspy-speedscope.json")

        base_cmd = [
            pyspy, "record",
            "-p", str(task.target_pid),
            "-d", str(task.duration_sec),
            "-r", str(task.sample_rate),
            "--format", "speedscope",
            "-o", speedscope_path,
        ]
        # Native unwinding can block indefinitely on some kernels/container
        # combinations. Plain Python stacks are the reliable default; callers
        # may explicitly opt in when C-extension frames are required.
        use_native = bool(task.options.get("native", False))
        cmd = base_cmd + (["--native"] if use_native else [])

        timeout = task.duration_sec + 30

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return CollectorResult(
                ok=False,
                reason=f"py-spy 执行超时 (>{timeout}s)",
            )
        except Exception as exc:
            return CollectorResult(
                ok=False,
                reason=f"py-spy 异常: {exc}",
            )

        if (
            use_native
            and proc.returncode != 0
            and self._should_retry_without_native(proc.stderr)
        ):
            try:
                proc = subprocess.run(
                    base_cmd,
                    capture_output=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return CollectorResult(
                    ok=False,
                    reason=f"py-spy 降级重试超时 (>{timeout}s)",
                )
            except Exception as exc:
                return CollectorResult(
                    ok=False,
                    reason=f"py-spy 降级重试异常: {exc}",
                )

        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            return CollectorResult(
                ok=False,
                reason=f"py-spy 执行失败 (exit={proc.returncode}): {err[:200]}",
            )

        if not os.path.isfile(speedscope_path) or os.path.getsize(speedscope_path) == 0:
            return CollectorResult(
                ok=False,
                reason="py-spy 未产出 speedscope 文件",
            )

        size = os.path.getsize(speedscope_path)
        return CollectorResult(
            ok=True,
            reason="py-spy 采集完成",
            artifacts=[
                {
                    # The Analyzer turns this raw sampled profile into
                    # flamegraph_json + top_json with verifiable sample counts.
                    "artifact_type": "raw",
                    "filename": "pyspy-speedscope.json",
                    "local_path": speedscope_path,
                    "content_type": "application/json",
                    "size_bytes": size,
                    "metadata": {
                        "schema_version": "pyspy.speedscope.v1",
                        "duration_sec": task.duration_sec,
                        "sample_rate": task.sample_rate,
                        "expected_samples": task.duration_sec * task.sample_rate,
                    },
                }
            ],
        )

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        return os.path.isdir(f"/proc/{pid}")

    @staticmethod
    def _should_retry_without_native(stderr: bytes) -> bool:
        text = stderr.decode("utf-8", errors="replace")
        return "UNW_EBADREG" in text or "bad register number" in text
