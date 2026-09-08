# Mini-Drop

Mini-Drop 是一个面向 Linux 多节点的证据驱动性能诊断系统。C++ Agent 负责采集，Go Control/API 负责身份、任务与事件流，Python Agent Runtime 负责受约束的诊断决策，React Web 展示诊断过程和可引用证据。

## 当前保留的主线

- 项目当前上下文（后续任务先读）：[`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md)
- 从 0 到 1 学习项目：[`docs/PROJECT_LEARNING_GUIDE.md`](docs/PROJECT_LEARNING_GUIDE.md)
- 基础复刻与部署：[`docs/REPLICATION.md`](docs/REPLICATION.md)
- AI 诊断方案：[`docs/AI_DIAGNOSIS.md`](docs/AI_DIAGNOSIS.md)
- Agent Runtime、Harness、Theme、上下文与记忆：[`docs/AGENT_RUNTIME.md`](docs/AGENT_RUNTIME.md)
- Skill 结构与生命周期：[`docs/SKILLS.md`](docs/SKILLS.md)
- 可执行协议与 Schema：[`docs/contracts/`](docs/contracts/)

## 运行边界

```text
Web ──HTTP/SSE──> Go API ──gRPC──> C++ Control ──mTLS──> Linux Agents
                     │
                     ├── PostgreSQL：任务、证据、诊断状态、Checkpoint
                     ├── MinIO：原始采集物与分析产物
                     └── Python Diagnosis Worker：LangGraph Agent Runtime
```

AI 只在服务端签发的目标和工具白名单内选择下一步。原始 PID、命令执行权限、证据真值和发布权限都不交给模型。

## 快速验证

```bash
python -m pytest -q
cd web && npm test && npm run build
cd apiserver && go test ./...
```

生产环境配置不得提交仓库。复制 `.env.example` 或 `deploy/env/*.example` 后，在部署机器上填写密钥。
