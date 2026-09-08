# Drop Insight V2 接口契约

当前公开前缀为 `/api/v2`。Go API 完成认证、请求限制和传输，Python Diagnosis Worker 通过私有
gRPC 执行领域逻辑。机器可读路径以 `openapi.v1.json` 为准。

## 通用响应

```json
{
  "code": 0,
  "message": "ok",
  "data": {}
}
```

浏览器通过 `X-API-Key` 认证。所有 ID、目标和授权主体都由服务端校验，不能信任模型自行生成的
字段。

## 创建会话

```http
POST /api/v2/diagnoses
Content-Type: application/json

{
  "query": "order-service CPU 持续升高",
  "mode": "AUTONOMOUS",
  "skill_policy": "AUTO",
  "target": {
    "service": "order-service",
    "environment": "production",
    "agent_id": "agent-prod-01",
    "pid": 12345
  }
}
```

`mode`：

- `AUTONOMOUS`：Scope 确认后会话级预授权，Worker 自动规划、取证与复测；
- `ASSISTED`：中风险探针逐项等待人工审批。

自主模式仍执行目标绑定、Capability、参数 Schema、预算和风险门禁。

`skill_policy`：

- `AUTO`（默认）：允许 Planner 检索和应用已发布 Skill；
- `DISABLED`：本会话完全绕过 Skill 检索，用于在线 A/B 控制组。

服务端把该值写入 Diagnosis 和 `diagnosis.created` 事件。实验结束后不能把 No-Skill
结果重新标记成 Skill 结果。

## 会话与探索树

| 方法和路径 | 作用 |
|---|---|
| `GET /api/v2/diagnoses` | 会话列表 |
| `GET /api/v2/diagnoses/{id}` | 会话事实快照 |
| `DELETE /api/v2/diagnoses/{id}` | 逻辑归档 |
| `POST /api/v2/diagnoses/{id}/clarify` | 补充 Scope |
| `GET /api/v2/diagnoses/{id}/target-candidates` | 查询目标候选 |
| `GET /api/v2/diagnoses/{id}/exploration-tree` | 动态探索树 |
| `GET /api/v2/diagnoses/{id}/budget` | 预算使用量 |

## 假设、工具与证据

| 方法和路径 | 作用 |
|---|---|
| `GET/POST /api/v2/diagnoses/{id}/hypotheses` | 查询/创建可证伪假设 |
| `GET /api/v2/diagnostic-tools` | 当前工具目录 |
| `POST /api/v2/diagnoses/{id}/tool-calls/preview` | 预览 Policy 判定 |
| `GET/POST /api/v2/diagnoses/{id}/tool-calls` | 查询/请求受控探针 |
| `PUT /api/v2/diagnoses/{id}/tool-calls/{call_id}` | 更新待执行参数 |
| `POST /api/v2/diagnoses/{id}/tool-calls/{call_id}/decision` | Assisted 审批 |
| `GET/POST /api/v2/diagnoses/{id}/evidence` | 查询/添加外部证据 |
| `POST /api/v2/diagnoses/{id}/evidence/import-task` | 从已完成 Task 导入 Evidence |

工具调用不是 Shell 命令。它必须映射到受支持 TaskKind，再由 C++ Agent 执行。采集失败、范围不匹配、
Hash/Schema 不通过或样本不足都不能提高假设置信度。

## Agent 控制

以下接口主要用于人工调试和验收。`AUTONOMOUS` 会话通常由 Worker 自动调用相同领域函数：

| 方法和路径 | 作用 |
|---|---|
| `POST /api/v2/diagnoses/{id}/planner/run` | 运行一轮计划器 |
| `POST /api/v2/diagnoses/{id}/orchestrator/advance` | 推进已完成 Task/Evidence |
| `GET/POST /api/v2/diagnoses/{id}/reports` | 查询/生成带引用报告 |
| `GET/POST /api/v2/diagnoses/{id}/feedback` | 查询/提交反馈 |
| `GET /api/v2/diagnoses/{id}/fix` | 修复验证列表 |
| `POST /api/v2/diagnoses/{id}/fix/verify` | 对比修复前后 Task |

## 事件

- `GET /api/v2/diagnoses/{id}/events`：持久化事件；
- `GET /api/v2/diagnoses/{id}/retrievals`：只读当前诊断的 Knowledge/RAG 检索轨迹；知识片段不是 Evidence；
- `GET /api/v2/diagnoses/{id}/events/stream?after=N`：Go 输出 SSE，按序号断点续传。

事件来自数据库事实与 Outbox，不以 Web 页面内存作为事实源。

## Skill

| 方法和路径 | 作用 |
|---|---|
| `GET /api/v2/diagnostic-skills` | Skill 列表 |
| `GET /api/v2/diagnostic-skills/{skill_id}` | Skill 详情 |
| `POST /api/v2/diagnostic-skills/{skill_id}/{action}` | `evaluate/campaign/publish/quarantine/rollback`；发布前必须登记通过的跨环境 Campaign |
| `POST /api/v2/diagnoses/{id}/diagnostic-skills/candidate` | 从已验证会话生成候选 |
| `GET /api/v2/diagnoses/{id}/diagnostic-skill-activations` | 本会话 Skill 激活记录 |

仓库内置 Skill 在 Worker 启动时自动装载；学习型 Skill 必须通过 Evidence、评测和状态门禁。

## Skill 随机实验

| 方法和路径 | 作用 |
|---|---|
| `GET/POST /api/v2/diagnostic-experiments` | 查询或创建持久化实验 |
| `POST /api/v2/diagnostic-experiments/{id}/assign` | 按服务端加盐哈希把一个流量单元粘性分到 `AUTO/DISABLED`，并创建真实 Diagnosis |
| `POST /api/v2/diagnostic-experiments/{id}/diagnoses/{diagnosis_id}/outcome` | 记录人工或受控 Oracle 的根因正确性与安全事件 |
| `POST /api/v2/diagnostic-experiments/{id}/evaluate` | 计算两比例检验、95% 置信区间、效果量和护栏，并保存时间序列快照 |
| `POST /api/v2/diagnostic-experiments/{id}/approve` | approver 人工批准已经满足门禁的发布建议 |

原始流量单元 ID 不入库，只保存 SHA-256；浏览器不能指定实验臂。默认门禁为每臂至少 30 个已标注样本、`p <= 0.05`、至少提升 5 个百分点、0 安全违规，且可信报告率回退不超过 5 个百分点。Worker 每五分钟检查是否出现新标签，有变化才追加指标快照。通过门禁只会生成 `ROLLOUT_RECOMMENDED`，不会自动改 Prompt、代码、权限、工具白名单或生产配置。

## 显式长期偏好

| 方法和路径 | 作用 |
|---|---|
| `GET /api/v2/operator-memories?project_scope=*` | 查询当前认证主体明确保存的偏好 |
| `PUT /api/v2/operator-memories` | 写入语言、解释深度、时区或低风险优先偏好 |
| `DELETE /api/v2/operator-memories` | 删除一项偏好 |

这里只保存 `EXPLICIT_USER` 来源的窄范围偏好，不从普通对话自动抽取，也不保存旧根因、PID、目标绑定、Shell 指令或工具权限。它是 Agent Prompt 的可选上下文，不是 Evidence，也不能覆盖服务端 Policy 与预算。

## 当前不存在的接口

以下路径不属于当前契约：`/api/v1/diagnoses`、`/api/diagnostic-cases`、
`/api/composite-tasks`。`/api/metrics` 已由 Go 提供轻量 Prometheus 进程指标，但不属于 AI V2
命名空间。旧路径只可作为历史讨论或待实现缺口出现。

## 验收

```powershell
python -m pytest tests/test_contracts.py tests/test_diagnostic_ai_rpc.py -q
```

接口单测不替代完整平台验收。完整链路要证明 Go 响应头为
`X-Mini-Drop-AI-Transport: grpc`，并能追踪 Task、Attempt、Artifact、AnalysisJob、Evidence 与
Report 引用。
