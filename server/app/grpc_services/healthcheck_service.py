"""HealthCheck gRPC 服务：1 Hz 心跳 + 任务下发。"""

from typing import Any

from server.app.generated import healthcheck_pb2, healthcheck_pb2_grpc, hotmethod_pb2
from server.app.state_machine import TaskStatus


class HealthCheckService(healthcheck_pb2_grpc.HealthCheckServicer):
    """Agent 心跳服务，一次 RPC 同时完成保活和任务拉取。"""

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def Do(self, request: healthcheck_pb2.HealthCheckRequest, context) -> healthcheck_pb2.HealthCheckResponse:
        # 记录心跳，检查有无待执行任务
        if hasattr(self._repo, "record_agent_metrics"):
            self._repo.record_agent_metrics(request.agent_id, _metrics_from_request(request))
        response = healthcheck_pb2.HealthCheckResponse()
        response.status = healthcheck_pb2.HealthCheckResponse.SERVING

        if getattr(request, "busy", False):
            if hasattr(self._repo, "heartbeat_only"):
                self._repo.heartbeat_only(request.agent_id, request.ip_addr)
            active_task_id = getattr(request, "active_task_id", "")
            if active_task_id:
                getter = getattr(self._repo, "get_task", None)
                task = getter(active_task_id) if callable(getter) else self._repo.tasks.get(active_task_id)
                if task is not None:
                    status = task.status.value if isinstance(task.status, TaskStatus) else task.status
                    if status == TaskStatus.CANCELLED.value:
                        response.cancel_task_id = active_task_id
                        response.cancel_reason = task.status_reason or "任务已被取消"
            response.pending = False
            return response

        task = self._repo.heartbeat(request.agent_id, request.ip_addr)

        if task is None:
            response.pending = False
            return response

        # 构造 TaskDesc 并嵌入响应
        response.pending = True
        task_desc = response.task_desc
        task_desc.task_id = task.id
        task_desc.task_type = 0  # 通用任务
        task_desc.profiler_type = self._profiler_type(task.collector_type)
        task_desc.timeout_sec = task.duration_sec + 30  # 留 30 秒余量
        task_desc.sample_argv.hz = task.sample_rate
        task_desc.sample_argv.duration = task.duration_sec
        task_desc.sample_argv.pid = task.target_pid
        options = task.request_params.get("options", {})
        task_desc.sample_argv.callgraph = options.get("callgraph", "fp")
        task_desc.sample_argv.event = options.get(
            "event", self._default_event(task.collector_type)
        )
        task_desc.sample_argv.subprocess = options.get("subprocess", False)
        # container_name is a namespace hint, not a shell fragment. Native agents
        # use it only to decide whether the host PID should be translated through
        # NSpid/nsenter before collection.
        task_desc.container_name = str(options.get("container_name", ""))[:128]
        task_desc.container_type = int(options.get("container_type", 0) or 0)
        task_desc.timeout_sec = int(
            options.get("timeout_sec", max(task.duration_sec + 30, 60)) or 0
        )
        return response

    @staticmethod
    def _profiler_type(collector_type: str) -> int:
        """将 collector_type 字符串映射为 protobuf profiler_type 值。

        Proto 定义（hotmethod.proto）:
          0=perf, 1=async-profiler(Java), 2=pprof(Go), 3=py-spy, 4=bpftrace
          5=memory_smaps, 6=sys_metrics, 7=continuous_perf
        """
        mapping: dict[str, int] = {
            "perf_cpu": 0,
            "java_async": 1,
            "go_pprof": 2,
            "pyspy": 3,
            "ebpf_io": 4,
            "memory_smaps": 5,
            "sys_metrics": 6,
            "continuous_perf": 7,
        }
        return mapping.get(collector_type, 0)

    @staticmethod
    def _default_event(collector_type: str) -> str:
        """Return an event name accepted by the selected collector backend."""
        return {
            "java_async": "cpu",
            "perf_cpu": "cpu-cycles",
            "continuous_perf": "cpu-cycles",
        }.get(collector_type, "")


def _pid_stats_to_dict(stats) -> dict:
    return {
        "cpu_percent": round(float(stats.cpu_percent), 3),
        "rss_mb": round(float(stats.rss_mb), 3),
        "read_kb_s": round(float(stats.read_kb_s), 3),
        "write_kb_s": round(float(stats.write_kb_s), 3),
        "children_count": int(stats.children_count),
    }


def _metrics_from_request(request: healthcheck_pb2.HealthCheckRequest) -> dict:
    return {
        "self": _pid_stats_to_dict(request.self_pstats),
        "children": _pid_stats_to_dict(request.children_pstats),
    }
