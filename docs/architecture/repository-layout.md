# Mini-Drop 仓库结构

仓库按 Drop 复刻指南的运行边界组织。当前处于渐进迁移阶段，Python 兼容实现继续承载尚未迁移的能力。

```text
mini-drop/
├── native/              C++17 核心控制面与原生 Agent
├── apiserver/           Go API 编排与持久化查询
├── analyzer/            Python 离线分析器和火焰图工具
├── server/              Python 兼容 API、gRPC、AI 编排与 Analyzer Worker
├── agent/               Python 兼容 Agent 和完整采集器插件
├── web/                 React Web
├── proto/               跨语言 gRPC 契约
├── benchmarks/          统一 AI 诊断测试用例
├── golden_scenarios/    快速确定性回归输入
├── knowledge/           AI 诊断知识条目
├── demo/                多语言故障目标和演示负载
├── deploy/              Dockerfile、Nginx、环境模板和 systemd
├── scripts/             运维、实验和评测入口
├── tests/               Python 单元、契约、集成与 E2E 测试
├── reports/             机器生成的验收结果
└── docs/                架构、指南、AI、接口和测试集文档
```

## 运行边界

| 模块 | 目标语言 | 当前职责 |
|---|---|---|
| `native/control` | C++ | Agent 注册、心跳、任务抢占、取消和结果回调；灰度运行 |
| `native/agent` | C++ | perf、eBPF 插件、资源限制、超时、取消和产物上传 |
| `apiserver` | Go | Web API 入口、鉴权、任务查询、归档、产物下载、SSE 和部分 AI 只读查询 |
| `analyzer` | Python | 火焰图、TopN 和离线分析工具 |
| `server` | Python | 尚未迁移的兼容 API、gRPC、状态机、数据库、AI 编排和后台 Worker |
| `agent` | Python | py-spy、async-profiler、pprof、内存、持续采样等完整采集器 |
| `web` | React | 任务、Agent、结果、审计和统一 AI 诊断页面；兼容旧诊断路由重定向 |

`server/` 和 `agent/` 目前仍是运行依赖，不属于废弃目录。迁移完成前保留它们，可以避免 eBPF、持续采样和 AI 链路回退。

## 不进入仓库的目录

- `web/node_modules/`、`web/dist/`
- `__pycache__/`、`.pytest_cache/`、`tmp/`
- `artifacts/`、本地 `reports` 临时输出
- `external/` 下载的外部项目
- `.env` 和证书私钥

这些内容由依赖安装、构建、测试或运行命令重新生成。
