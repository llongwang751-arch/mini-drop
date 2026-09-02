# Python Workers

`server/` 只包含 Python 后台 Worker，不提供公开 HTTP 服务。

- `server.app.diagnosis_worker`：通过私有 gRPC 接收 Go API 的诊断请求，运行 Drop Insight V2、证据门禁和 Skill 演进。
- `server.app.analysis_jobs`：异步消费 MinIO 原始产物，生成结构化分析结果与派生产物。
- `server.app.models` / `server.app.sql_repository`：两个 Worker 共用的 SQLAlchemy 持久化层。

公开 HTTP/SSE 入口只有 Go API；采集执行只有 C++ Control/Agent。Python 只承担分析和 AI Worker 职责。
