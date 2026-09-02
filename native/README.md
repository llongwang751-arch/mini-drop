# C++ 核心模块

- `control/`：C++ 控制面（默认 `control-plane`，50051），负责 Agent 注册、心跳、任务领取、取消和结果回调。
- `agent/`：C++ 原生采集 Agent（默认 `native-agent`），通过 Collector Registry 支持 `perf_cpu`、`ebpf_io`、`pyspy`、`go_pprof`、`memory_smaps`、`sys_metrics`、`continuous_perf` 插件。

默认 Compose 栈只使用 C++ Control 与 C++ Agent。Go API 通过 Control gRPC 创建任务，Agent 通过心跳领取任务；不存在 Python 采集回滚路径。
