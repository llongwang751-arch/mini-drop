# Mini-Drop 任务归档与 AI 证据保留策略

## 1. 为什么任务列表中的“删除”采用软删除

性能诊断任务不仅是一行任务数据，还关联状态迁移、执行尝试、原始采集文件、火焰图、AI 假设、探针执行结果和审计日志。直接级联删除会破坏以下能力：

1. 无法解释历史诊断结论依据了什么数据；
2. 无法复盘 Agent、Analyzer 或 AI 在哪个阶段出错；
3. 无法使用历史任务构建 baseline 和回归测试集；
4. 审计日志引用的任务会失去上下文。

因此，控制台“删除”实际执行的是**归档**：任务从正常业务列表隐藏，底层证据继续保留。

## 2. 当前接口语义

```http
DELETE /api/tasks/{task_id}?reason=用户填写的原因
```

允许归档的状态：

- `DONE`
- `FAILED`
- `CANCELLED`

`PENDING / RUNNING / UPLOADING / ANALYZING` 返回 HTTP 409，提示先取消或等待任务结束。

成功响应包含：

```json
{
  "task_id": "task_xxx",
  "deleted": true,
  "deletion_mode": "soft",
  "evidence_retained": true,
  "served_by": "go-apiserver"
}
```

## 3. 数据库行为

Go API 在同一 PostgreSQL 事务中：

1. `FOR UPDATE` 锁定任务，避免重复归档和状态竞争；
2. 校验任务处于终态；
3. 写入 `deleted_at / deleted_by / delete_reason`；
4. 写入 `TASK_ARCHIVED` 审计日志；
5. 提交事务。

归档后默认任务查询增加 `deleted_at IS NULL`，因此 Web 列表和任务详情都不再显示该任务。

以下数据继续保留：

- `task_status_events`：状态迁移证据；
- `task_attempts`：Agent 执行与租约记录；
- `artifacts` 与 MinIO 对象：原始数据、火焰图和分析结果；
- `analysis_jobs`：Analyzer 执行记录；
- `diagnosis_runs` 及其报告、工具结果、反馈：AI 诊断证据；
- Drop Insight 探针、快照、工具调用和持续诊断触发记录；
- `audit_logs`：操作者和归档原因。

## 4. 安全与一致性

- 活跃任务归档返回 HTTP 409，不会中断正在执行的 Agent；
- 已归档任务再次归档返回 HTTP 404，避免重复审计；
- Python 兼容仓储与 Go API 使用相同软删除语义；
- Agent 拉取任务时过滤已归档任务；
- 归档操作不立即删除 MinIO 对象。

## 5. 后续物理清理

物理清理应作为独立的管理员保留策略，而不是普通用户按钮。建议后续增加：

1. 默认保留 30/90/180 天；
2. 合规锁定和 Golden 测试集任务永久保留；
3. 清理前生成 manifest，列出数据库行和 MinIO 对象；
4. 先删除对象存储，再按外键顺序删除数据库证据；
5. 清理动作写入独立、不可随任务删除的审计记录。

当前阶段只实现归档，不执行自动物理清理。
