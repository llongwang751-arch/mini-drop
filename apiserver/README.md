# Go API 编排模块

Go 服务是 Web 唯一的公开 HTTP/SSE 入口，负责鉴权、Request ID、任务与 Agent
查询、任务创建与取消、任务归档、产物读取、持久化 SSE，以及 AI 诊断接口编排。
任务执行请求通过 gRPC 发给 C++ Control，诊断请求通过私有 gRPC 发给 Python
Diagnosis Worker；不存在公开 FastAPI 服务或 Python HTTP 回退路径。

```bash
go test ./...
go run ./cmd/apiserver
```

接口边界见 `docs/contracts/go-diagnosis-query.md`，当前完成度与缺口见
完整部署边界见 `docs/REPLICATION.md`，AI 诊断边界见 `docs/AI_DIAGNOSIS.md`。
