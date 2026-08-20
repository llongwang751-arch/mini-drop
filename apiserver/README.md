# Go API 编排模块

Go 服务是 Web 的统一 API 入口，负责鉴权、Request ID、任务与 Agent 查询、任务归档、产物读取、持久化 SSE 和部分 AI 只读查询。尚未迁移的写接口反向代理到 Python 服务。

```bash
go test ./...
go run ./cmd/apiserver
```

接口迁移边界见 `docs/contracts/go-diagnosis-query.md` 和 `docs/architecture/implementation-status.md`。
