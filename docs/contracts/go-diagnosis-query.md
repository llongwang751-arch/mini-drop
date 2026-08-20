# AI 诊断 Go 接口边界

## 1. 为什么把入口治理和领域推理解耦

AI 诊断不是普通 CRUD。创建诊断后还会经历意图解析、范围确认、假设生成、工具选择、风险审批、采集、证据归一化、反证和报告校验。把这些逻辑一次性改写成另一种语言，风险高且难以证明行为一致。

因此本阶段采用“稳定控制面 + 专业分析面”的边界：

| 职责 | 当前实现 | 原因 |
|---|---|---|
| 会话列表、持续触发记录、统一案例列表与详情 | Go + PostgreSQL | 高频只读、无副作用、便于扩容 |
| 诊断创建与探针审批的入口校验、预算、安全策略和审计 | Go | 统一外部写边界，拒绝越权或不完整命令 |
| 假设推进、模型和工具调用、证据闭环 | Python | 保留成熟的分析库和领域编排逻辑 |
| 任务与产物控制 API | Go | 已完成事务化迁移 |
| 原始性能数据分析 | Python Analyzer | 适合数据处理与 AI 生态 |

## 2. Go 原生接口

### `GET /api/v1/diagnoses`

直接分页读取集群诊断会话，不触发状态推进，不申请诊断租约。

参数：

- `limit`：默认 100，范围 1～1000；
- `offset`：默认 0，不允许负数。

### `GET /api/v1/continuous-diagnosis-triggers`

读取“持续采样异常被提升为 AI 诊断”的幂等记录，包括检测器版本、异常评分、来源任务和生成的诊断 ID。

### `GET /api/diagnostic-cases`

合并两套仍然保留的 AI 路径：

- `cluster_diagnosis_v1`：多实例、同宿主机和上下游范围诊断；
- `drop_insight_v2`：证据—假设—反证—报告工作流。

统一结果按 `updated_at` 倒序分页，并保留 `legacy_links`，旧页面和旧接口仍可继续使用。

### `GET /api/diagnostic-cases/{case_id}`

由 Go 直接组装统一详情：

- 集群诊断返回事件、拓扑快照、探针、覆盖矩阵、证据、证据快照、流水线节点和最新结论；
- Drop Insight 返回原生会话快照及统一统计字段；
- 查询不会领取租约、推进状态或初始化缺失节点；
- 响应中的 `served_by=go-apiserver` 可作为运行时路由证据。

历史 Python 详情会在读取旧会话时补建流水线节点，这种“GET 产生写入”的兼容行为没有迁入 Go。Go 只返回数据库中已经存在的事实快照。

## 3. 不在本阶段迁移的接口

- `GET /api/v1/diagnoses/{diagnosis_id}`（旧接口仍包含兼容性的推进语义）
- `/api/v2/diagnoses/**` 下的假设、证据、报告、工具调用和编排写接口

`POST /api/v1/diagnoses` 与 `POST /api/v1/diagnoses/{diagnosis_id}/approvals` 已由 Go 接管入口治理。Go 使用严格 JSON Schema、1 MiB 请求上限、预算/策略白名单和单次审批范围校验；通过后写控制命令审计，再转交 Python 领域引擎。新页面应优先使用统一详情接口；旧详情接口待调用方完成切换后再拆除“GET 推进状态”的历史兼容行为。

## 4. 人工门禁的正确语义

以下状态不是“正在运行”，而是“暂停等待人”：

- `NEEDS_SCOPE_CONFIRMATION`：目标服务、实例、Agent 或 PID 信息不足；
- `WAITING_APPROVAL`：存在需要人工批准的中风险探针。

后台 Worker 不应反复获取租约或改写更新时间。系统现在从候选扫描阶段就排除它们，避免数据库写放大、虚假实时更新和 SSE 噪声。

## 5. 验收命令

```powershell
# 查看 Go 原生诊断会话列表
docker compose exec -T web sh -lc "wget -qO- 'http://apiserver:8080/api/v1/diagnoses?limit=2&offset=0'"

# 查看持续采样异常触发记录
docker compose exec -T web sh -lc "wget -qO- 'http://apiserver:8080/api/v1/continuous-diagnosis-triggers?limit=10&offset=0'"

# 查看统一案例
docker compose exec -T web sh -lc "wget -qO- 'http://apiserver:8080/api/diagnostic-cases?limit=10&offset=0'"

# 查看统一详情（把 CASE_ID 换成列表返回的 case_id）
docker compose exec -T web sh -lc "wget -qO- 'http://apiserver:8080/api/diagnostic-cases/CASE_ID'"
```

四个响应的 `data.served_by` 应为 `go-apiserver`。

## 6. 下一阶段

1. 让前端诊断详情统一消费 `/api/diagnostic-cases/{case_id}`；
2. 逐步把旧 `GET /api/v1/diagnoses/{id}` 的状态推进语义移交给 Worker；
3. 补充统一查询契约的端到端测试和时间格式归一化；
4. 在原生 Ubuntu 22.04 完成 eBPF IO 异常 `DONE` 验收。
