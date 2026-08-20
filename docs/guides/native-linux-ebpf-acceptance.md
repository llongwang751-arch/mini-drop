# 原生 Linux eBPF 验收

该验收专门区分“代码已实现”和“原生 Linux 已现场跑通”。Windows Docker
Desktop 适合验证权限不足的失败分支，最终 `DONE` 结果应在 Ubuntu 22.04
或同等原生 Linux 主机执行。

## 前置条件

- 内核支持 BPF 与 block tracepoint；
- Docker Engine、Compose、`curl`、`jq` 已安装；
- 当前用户能执行 Docker；
- 项目已配置 `.env`。

## 一键验收

```bash
cd /path/to/mini-drop
bash scripts/verify_native_ebpf.sh
```

脚本会启动原生 C++ Agent、核对 tracefs、制造真实磁盘 IO、通过 Go API
创建 eBPF 任务、等待状态机进入 `DONE`，最后验证 `ebpf_metrics` 产物中的
`total_samples > 0`。任一条件未满足都会以非零状态退出并打印 Agent 日志。

验收报告至少保存：主机内核版本、任务 ID、状态事件、指标 JSON、Agent 日志。
