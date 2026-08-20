# C++ 核心模块

- `control/`：C++ 控制面（默认 `control-plane`，50051），负责 Agent 注册、心跳、任务领取、取消和结果回调。
- `agent/`：C++ 原生采集 Agent（默认 `native-agent`），通过 Collector Registry 支持 `perf_cpu`、`ebpf_io`、`pyspy`、`go_pprof`、`memory_smaps`、`sys_metrics`、`continuous_perf` 插件。

默认 Compose 栈即 C++ 控制面 + C++ 原生 Agent；Python 控制面与 Python 兼容 Agent 作为回滚路径保留（分别经 `docker-compose.python-control.yml` 与 `--profile python-agent`）。当前边界见 `docs/architecture/implementation-status.md`。
