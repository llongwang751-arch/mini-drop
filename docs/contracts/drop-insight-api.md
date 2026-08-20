# Drop Insight 接口契约

> 状态：设计评审稿  
> API 版本：v2  
> 前缀：`/api/v2`  
> 约束：本文档只定义团队独有 AI 方案，不沿用 Legacy AI 的业务决策逻辑。

---

## 1. 设计目标

Drop Insight 接口必须满足：

1. 模型不能绕过 Server 直接控制 Agent；
2. 诊断目标、时间、环境必须显式；
3. 工具调用必须有固定 Schema；
4. 高风险工具必须经过审批；
5. 证据必须可追溯到 Task 和 Artifact；
6. 诊断过程可以暂停、恢复、取消和重放；
7. 证据不足是合法终态；
8. 置信度由程序计算；
9. 所有事件可审计；
10. Legacy AI 与 v2 可以并行运行和评测。

---

## 2. 通用约定

## 2.1 Content-Type

```http
Content-Type: application/json
Accept: application/json
```

事件流：

```http
Accept: text/event-stream
```

## 2.2 身份

第一阶段复用 Server 认证中间件，但 v2 业务对象必须预留：

```text
actor_id
tenant_id
project_id
resource_scope
```

后续从共享 API Key 迁移到用户和 Agent 独立身份。

## 2.3 请求标识

所有写请求支持：

```http
Idempotency-Key: <uuid>
X-Request-ID: <uuid>
```

同一身份、同一路由、同一 `Idempotency-Key`：

- 请求体一致：返回首次结果；
- 请求体不同：返回 `409 IDEMPOTENCY_CONFLICT`。

## 2.4 时间

- 统一使用 RFC 3339；
- 数据库存储 UTC；
- 响应明确时区；
- 不能由模型偷偷添加默认时间窗口；
- 用户没有提供时间时，返回澄清问题。

## 2.5 通用响应

成功：

```json
{
  "code": "OK",
  "message": "ok",
  "data": {},
  "request_id": "req-001"
}
```

失败：

```json
{
  "code": "TARGET_NOT_FOUND",
  "message": "没有找到目标服务对应的运行实例",
  "details": {
    "service": "order-service"
  },
  "retryable": false,
  "request_id": "req-001"
}
```

HTTP 状态和业务错误码必须一致。

---

## 3. 核心对象

## 3.1 DiagnosticTarget

```json
{
  "service": "order-service",
  "environment": "staging",
  "agent_id": null,
  "host_id": null,
  "container_id": null,
  "pid": null,
  "instance_id": null
}
```

规则：

- 用户可以只提供服务名；
- Server 通过 `resolve_target` 补充候选；
- 多个候选时必须由用户确认；
- 模型不能自行选择同名生产实例。

## 3.2 DiagnosticTimeRange

```json
{
  "start": "2026-07-27T10:00:00Z",
  "end": "2026-07-27T10:05:00Z",
  "timezone": "Asia/Shanghai"
}
```

规则：

- `end > start`；
- 最大查询窗口由 Policy 决定；
- 采集窗口和故障窗口分别保存；
- 历史证据必须检查时间重叠。

## 3.3 Symptom

```json
{
  "type": "LATENCY_INCREASE",
  "description": "订单接口过去五分钟明显变慢",
  "signals": [
    {
      "name": "p95_latency",
      "operator": ">",
      "value": 500,
      "unit": "ms"
    }
  ]
}
```

允许类型：

```text
LATENCY_INCREASE
CPU_HIGH
MEMORY_GROWTH
IO_SLOW
NETWORK_ERROR
ERROR_RATE_INCREASE
THROUGHPUT_DROP
UNKNOWN
```

## 3.4 DiagnosisBudget

```json
{
  "max_duration_seconds": 300,
  "max_tool_calls": 12,
  "max_concurrent_tasks": 3,
  "max_hosts": 5,
  "max_artifact_bytes": 524288000,
  "max_risk_level": "R2"
}
```

预算由程序强制执行。

---

## 4. 创建诊断

```http
POST /api/v2/diagnoses
```

请求：

```json
{
  "query": "订单服务过去五分钟变慢，判断是自身热点、宿主机还是下游导致",
  "target": {
    "service": "order-service",
    "environment": "staging"
  },
  "time_range": {
    "start": "2026-07-27T10:00:00Z",
    "end": "2026-07-27T10:05:00Z",
    "timezone": "Asia/Shanghai"
  },
  "mode": "ASSISTED",
  "budget": {
    "max_duration_seconds": 300,
    "max_tool_calls": 12,
    "max_concurrent_tasks": 3,
    "max_hosts": 5,
    "max_risk_level": "R2"
  },
  "baseline": {
    "type": "HEALTHY_PEER",
    "reference": null
  }
}
```

`mode`：

- `ASSISTED`：高风险步骤需要用户批准；
- `OBSERVE_ONLY`：只使用历史和只读证据；
- `REPRODUCTION`：标准故障评测；
- `REPLAY`：重放已有证据，不执行新任务。

响应：

```json
{
  "code": "OK",
  "message": "ok",
  "data": {
    "diagnosis_id": "diag-001",
    "status": "UNDERSTANDING",
    "created_at": "2026-07-27T10:06:00Z",
    "next_action": "WAIT"
  },
  "request_id": "req-001"
}
```

---

## 5. 查询诊断

## 5.1 列表

```http
GET /api/v2/diagnoses?status=COLLECTING&service=order-service&offset=0&limit=20
```

## 5.2 详情

```http
GET /api/v2/diagnoses/{diagnosis_id}
```

响应核心：

```json
{
  "diagnosis_id": "diag-001",
  "status": "WAITING_APPROVAL",
  "query": "订单服务过去五分钟变慢",
  "intent": {},
  "context": {},
  "budget": {},
  "budget_usage": {},
  "hypotheses": [],
  "plan": [],
  "pending_approvals": [],
  "evidence_summary": {},
  "report": null,
  "version": 7,
  "created_at": "2026-07-27T10:06:00Z",
  "updated_at": "2026-07-27T10:06:15Z"
}
```

`version` 用于乐观并发控制。

---

## 6. 澄清问题

当目标、时间或范围不明确时，状态进入：

```text
NEEDS_CLARIFICATION
```

提交回答：

```http
POST /api/v2/diagnoses/{diagnosis_id}/clarifications
```

请求：

```json
{
  "answers": [
    {
      "question_id": "q-001",
      "value": "staging"
    },
    {
      "question_id": "q-002",
      "value": "2026-07-27T10:00:00Z/2026-07-27T10:05:00Z"
    }
  ],
  "expected_version": 2
}
```

禁止使用自由回答覆盖已经确认的目标。

---

## 7. 诊断计划

## 7.1 Hypothesis

```json
{
  "hypothesis_id": "hyp-001",
  "type": "SELF_CPU_HOTSPOT",
  "scope": "PROCESS_SCOPE",
  "statement": "目标进程内部存在持续 CPU 热点",
  "status": "PROPOSED",
  "required_evidence": [
    "PROCESS_CPU_HIGH",
    "HOT_FUNCTION_PRESENT"
  ],
  "supporting_evidence_refs": [],
  "counter_evidence_refs": [],
  "missing_evidence": [
    "PROCESS_CPU_HIGH",
    "HOT_FUNCTION_PRESENT"
  ],
  "confidence": 0.0
}
```

## 7.2 DiagnosticPlanStep

```json
{
  "step_id": "step-001",
  "sequence": 1,
  "tool_name": "collect_sys_metrics",
  "purpose": "区分单进程 CPU 热点和宿主机整体饱和",
  "target": {
    "agent_id": "worker-a",
    "pid": 1234
  },
  "arguments": {
    "duration_seconds": 15,
    "sample_rate": 1
  },
  "risk_level": "R1",
  "requires_approval": false,
  "status": "READY",
  "hypothesis_ids": ["hyp-001", "hyp-002"]
}
```

步骤状态：

```text
PROPOSED
POLICY_REJECTED
WAITING_APPROVAL
APPROVED
READY
DISPATCHED
RUNNING
COMPLETED
FAILED
CANCELLED
SKIPPED
```

---

## 8. 工具定义

```http
GET /api/v2/diagnostic-tools
```

工具定义：

```json
{
  "name": "start_perf_profile",
  "version": "1.0",
  "description": "对指定 Linux PID 进行 CPU Profile",
  "input_schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["agent_id", "pid", "duration_seconds", "sample_rate"],
    "properties": {
      "agent_id": {"type": "string", "minLength": 1},
      "pid": {"type": "integer", "minimum": 1},
      "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
      "sample_rate": {"type": "integer", "minimum": 1, "maximum": 999}
    }
  },
  "risk_level": "R2",
  "requires_approval": true,
  "required_capabilities": ["perf_cpu"],
  "supported_os": ["linux"],
  "idempotent": false
}
```

模型只看工具定义，不能拿到任意命令执行接口。

---

## 9. Policy Decision

每个计划步骤都产生：

```json
{
  "decision_id": "policy-001",
  "step_id": "step-001",
  "decision": "REQUIRE_APPROVAL",
  "checks": [
    {"name": "SCHEMA", "result": "PASS"},
    {"name": "TARGET_SCOPE", "result": "PASS"},
    {"name": "ACTOR_PERMISSION", "result": "PASS"},
    {"name": "AGENT_CAPABILITY", "result": "PASS"},
    {"name": "BUDGET", "result": "PASS"},
    {"name": "RISK", "result": "REQUIRE_APPROVAL"}
  ],
  "reason": "perf_cpu 为 R2 采集"
}
```

`decision`：

```text
ALLOW
REQUIRE_APPROVAL
DENY
```

---

## 10. 审批

```http
POST /api/v2/diagnoses/{diagnosis_id}/approvals
```

请求：

```json
{
  "step_id": "step-001",
  "decision": "APPROVE",
  "reason": "允许在测试环境执行 15 秒 perf",
  "expected_version": 5
}
```

`decision`：

```text
APPROVE
REJECT
```

审批必须记录：

- 用户；
- 时间；
- 目标；
- 工具；
- 参数；
- 风险；
- 原因。

---

## 11. 证据

## 11.1 EvidenceEnvelope

```json
{
  "evidence_id": "ev-001",
  "diagnosis_id": "diag-001",
  "evidence_type": "PERF_HOT_FUNCTION",
  "source": {
    "tool_name": "start_perf_profile",
    "task_id": "task-001",
    "task_attempt_id": "attempt-001",
    "artifact_id": "artifact-001",
    "analyzer_version": "1.0"
  },
  "scope": {
    "agent_id": "worker-a",
    "host_id": "host-a",
    "service": "order-service",
    "instance_id": "order-service-b",
    "container_id": "container-a",
    "pid": 1234
  },
  "time_range": {
    "start": "2026-07-27T10:07:00Z",
    "end": "2026-07-27T10:07:15Z"
  },
  "observation": {
    "metric": "cpu_sample_percent",
    "entity": "calculate_price",
    "value": 72.4,
    "unit": "percent"
  },
  "quality": {
    "level": "HIGH",
    "sample_count": 8231,
    "degraded": false,
    "target_match": true,
    "time_overlap": true
  },
  "limitations": [],
  "created_at": "2026-07-27T10:07:20Z"
}
```

## 11.2 查询证据

```http
GET /api/v2/diagnoses/{diagnosis_id}/evidence
GET /api/v2/diagnoses/{diagnosis_id}/evidence/{evidence_id}
```

原始 Artifact 仍通过通用 Artifact API 访问。

## 11.3 证据质量拒绝

以下情况不得进入支持结论的高质量证据：

- 目标不匹配；
- 时间不重叠；
- 空样本；
- 样本数量不足；
- degraded；
- Artifact 校验失败；
- Analyzer 版本未知；
- 来自未授权目标；
- 重复证据。

---

## 12. 事件流

```http
GET /api/v2/diagnoses/{diagnosis_id}/events
Accept: text/event-stream
```

事件示例：

```text
diagnosis.created
diagnosis.status_changed
diagnosis.clarification_required
diagnosis.context_resolved
hypothesis.proposed
plan.created
policy.decision
approval.required
approval.resolved
tool.dispatched
tool.completed
tool.failed
evidence.accepted
evidence.rejected
hypothesis.updated
scope.expanded
report.generated
diagnosis.completed
diagnosis.failed
```

每条事件包含：

```json
{
  "event_id": "evt-001",
  "diagnosis_id": "diag-001",
  "event_type": "evidence.accepted",
  "sequence": 17,
  "occurred_at": "2026-07-27T10:07:21Z",
  "actor": "SYSTEM",
  "payload": {}
}
```

客户端通过 `Last-Event-ID` 续传。

---

## 13. 取消

```http
POST /api/v2/diagnoses/{diagnosis_id}/cancel
```

请求：

```json
{
  "reason": "用户终止诊断",
  "expected_version": 8
}
```

行为：

- 停止创建新步骤；
- 尝试取消正在运行的 Task；
- 已有证据保留；
- 状态进入 `CANCELLED`；
- 写审计；
- 不删除 Artifact。

---

## 14. 报告

```http
GET /api/v2/diagnoses/{diagnosis_id}/report
```

响应：

```json
{
  "diagnosis_id": "diag-001",
  "summary": "订单服务变慢主要由应用自身 CPU 热点造成",
  "status": "COMPLETED",
  "root_causes": [
    {
      "hypothesis_id": "hyp-001",
      "category": "APPLICATION",
      "description": "calculate_price 占 CPU 样本 72.4%",
      "confidence": 0.87,
      "evidence_refs": ["ev-001", "ev-002"],
      "counter_evidence_refs": ["ev-003"]
    }
  ],
  "ruled_out": [
    {
      "hypothesis": "HOST_CPU_SATURATION",
      "reason": "宿主机总 CPU 正常，目标进程 CPU 明显偏高",
      "evidence_refs": ["ev-002", "ev-003"]
    }
  ],
  "limitations": [
    "当前未检查数据库内部执行计划"
  ],
  "coverage": {
    "process": "COMPLETE",
    "host": "COMPLETE",
    "peer_instances": "COMPLETE",
    "dependencies": "NOT_CHECKED"
  },
  "recommendations": [
    {
      "action_id": "rec-001",
      "type": "CODE_REVIEW",
      "description": "检查 calculate_price 循环计算",
      "risk": "LOW",
      "auto_executable": false,
      "evidence_refs": ["ev-001"]
    }
  ],
  "model": {
    "provider": "MODEL_PROVIDER",
    "model": "MODEL_NAME",
    "prompt_version": "drop-insight-report-v1"
  },
  "generated_at": "2026-07-27T10:08:00Z"
}
```

报告生成后必须经过程序验证：

- 所有 evidence ref 存在；
- 根因和证据范围一致；
- 时间一致；
- 置信度来自 Calibrator；
- 禁止建议未授权变更；
- 证据不足时不能输出确定根因。

---

## 15. 反馈

```http
POST /api/v2/diagnoses/{diagnosis_id}/feedback
```

请求：

```json
{
  "root_cause_correct": true,
  "confirmed_hypothesis_id": "hyp-001",
  "evidence_useful": true,
  "comment": "热点函数与人工 perf 结果一致"
}
```

反馈进入评测数据，不直接修改线上规则权重。

---

## 16. 重放与评测

## 16.1 重放

```http
POST /api/v2/diagnoses/{diagnosis_id}/replay
```

请求：

```json
{
  "strategy": "DROP_INSIGHT_CONTROLLED",
  "model": "MODEL_NAME",
  "reuse_evidence": true
}
```

不执行新采集，只基于冻结证据对比推理策略。

## 16.2 Oracle

评测模式额外提供：

```json
{
  "oracle": {
    "scenario_id": "cpu-hotspot-001",
    "expected_category": "APPLICATION",
    "expected_hypothesis": "SELF_CPU_HOTSPOT",
    "expected_entity": "calculate_price"
  }
}
```

Oracle 不得进入模型上下文，只能由评测程序读取。

---

## 17. 错误码

| 错误码 | HTTP | 含义 |
|---|---:|---|
| `INVALID_REQUEST` | 400 | Schema 不合法 |
| `CLARIFICATION_REQUIRED` | 409 | 缺少目标或时间 |
| `TARGET_NOT_FOUND` | 404 | 找不到目标 |
| `TARGET_AMBIGUOUS` | 409 | 多个目标待确认 |
| `AGENT_OFFLINE` | 409 | Agent 离线 |
| `CAPABILITY_MISSING` | 409 | Agent 不支持工具 |
| `APPROVAL_REQUIRED` | 409 | 等待审批 |
| `POLICY_DENIED` | 403 | 策略拒绝 |
| `BUDGET_EXHAUSTED` | 409 | 预算耗尽 |
| `EVIDENCE_INSUFFICIENT` | 422 | 证据不足 |
| `TASK_FAILED` | 502 | 底层采集失败 |
| `ARTIFACT_INVALID` | 422 | Artifact 损坏 |
| `VERSION_CONFLICT` | 409 | 乐观锁冲突 |
| `IDEMPOTENCY_CONFLICT` | 409 | 幂等键冲突 |
| `MODEL_UNAVAILABLE` | 503 | 模型不可用且无法降级 |
| `INTERNAL_ERROR` | 500 | 内部错误 |

---

## 18. 数据表草案

新方案建议独立表：

```text
drop_insight_sessions
drop_insight_events
drop_insight_clarifications
drop_insight_hypotheses
drop_insight_plan_steps
drop_insight_policy_decisions
drop_insight_approvals
drop_insight_evidence
drop_insight_reports
drop_insight_feedback
drop_insight_model_calls
```

通用 Task、TaskAttempt、Artifact 和 AnalysisJob 不放入 AI 专属表。

---

## 19. Legacy 隔离

- v1 与 v2 使用不同路由；
- v2 不导入 Legacy Orchestrator；
- v2 不复用 Legacy Hypothesis 和 Report Schema；
- 可复用通用数据库 Session、Task Service、Agent Registry、Artifact Service；
- 对比评测通过适配层读取两种结果；
- v2 验收后删除 v1 页面、接口和专属数据表。

---

## 20. 契约验收

接口进入实现前必须完成：

- OpenAPI Schema 评审；
- 所有请求设置 `additionalProperties: false`；
- 状态机测试；
- 幂等测试；
- 权限测试；
- 审批测试；
- 预算测试；
- 证据引用测试；
- SSE 重连续传测试；
- Legacy/v2 隔离测试；
- CPU 热点场景契约测试。

