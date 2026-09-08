# AI 诊断 Go 接口边界

## 当前边界

Go API 是唯一公开 HTTP/SSE 入口。浏览器只访问 `/api/**`；Python Diagnosis Worker
不监听公网 HTTP，而是接收 Go 发送的私有 `DiagnosticAI.Invoke` gRPC 信封。

| 路径 | 所有者 | 作用 |
|---|---|---|
| `/api/tasks/**` | Go | Task、Attempt、Artifact 和取消/归档 |
| `/api/schedules/**` | Go | 周期任务 CRUD、手动触发和执行记录 |
| `/api/events/stream` | Go | 平台持久化事件流 |
| `/api/v2/diagnoses/{id}/events/stream` | Go | 从 PostgreSQL 投影 AI 会话事件 |
| 其余 `/api/v2/**` | Go 入口 + Python 领域服务 | Go 鉴权、限流和透传；Python 推理与证据编排 |

`apiserver/internal/httpapi/server.go` 注册公开路由；
`server/app/diagnostic_ai_rpc.py` 是 `/api/v2` 的私有 RPC 分发表；
`docs/contracts/openapi.v1.json` 只记录这两处代码实际支持的路径。

## 创建自主诊断

```http
POST /api/v2/diagnoses
Content-Type: application/json
X-API-Key: ...

{
  "query": "checkout 服务 CPU 持续升高",
  "mode": "AUTONOMOUS",
  "target": {
    "agent_id": "agent-prod-01",
    "pid": 12345
  }
}
```

`AUTONOMOUS` 表示用户在会话范围内预授权中风险只读取证。Worker 会自动启动计划、创建 ToolCall、
等待 Task/Analyzer、吸收 Evidence 并继续下一轮。它不等于任意执行：目标不一致、Agent 无能力、
参数越界、预算不足或 R3 操作仍会被 Policy 拒绝。

`ASSISTED` 使用相同推理闭环，但中风险 ToolCall 会停在 `WAITING_APPROVAL`，由用户逐步决定。

## 查询与事件

- `GET /api/v2/diagnoses`：会话列表；
- `GET /api/v2/diagnoses/{id}`：单会话事实快照；
- `GET /api/v2/diagnoses/{id}/events`：持久化事件列表；
- `GET /api/v2/diagnoses/{id}/events/stream?after=N`：支持断点续传的 SSE；
- `GET /api/v2/diagnoses/{id}/exploration-tree`：由事实表投影的探索树；
- `GET /api/v2/diagnoses/{id}/budget`：本次会话资源消耗。

完整路径以 OpenAPI 为准。不存在 `/api/v1/diagnoses`、`/api/diagnostic-cases` 或
`/api/composite-tasks` 的兼容入口；文档和测试不得再把这些旧接口写成当前能力。

## 可靠性语义

- Go 创建 Task 时使用 `Idempotency-Key` 和数据库唯一约束防重；
- Go Scheduler 轮询到期计划，计划槽位唯一键阻止多副本重复触发；
- Diagnosis Worker 使用租约推进会话，并在同一循环投递 Outbox；
- PostgreSQL 环境由 `pg_notify('mini_drop_events', payload)` 唤醒订阅者；
- SQLite 只用于本地测试，Outbox 日志充当可观察的本地 sink。

## 最小验收

```powershell
python -m pytest tests/test_contracts.py tests/test_diagnostic_ai_rpc.py -q
go test ./...
```

容器验收还要从 Go 入口创建会话，证明响应头 `X-Mini-Drop-AI-Transport: grpc`，并核对
Task、Attempt、Artifact、AnalysisJob、Evidence 和报告引用。直接调用 Python 函数不算入口验收。
