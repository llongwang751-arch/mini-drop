# 诊断 Skill、案例经验与固定评测

本模块在 AgentScope ReAct 中加载版本化诊断方法，并按当前测量条件检索个人历史案例。方法与经验只提供先验，不训练 DeepSeek 权重，也不能替代本次测量和根因验证。

## 一次诊断如何使用知识

1. 新请求默认 `knowledge_mode=experience`，也可选 `skills` 或 `off`。模式持久化并纳入幂等摘要。SQL009将旧记录设为off，保留旧摘要与报告。
2. Go读取当前E1摘要，启用知识时自动调用 `read_diagnostic_knowledge` 形成E2，计入原有八次工具总预算。
3. 根据实际采集类型、热点提示、测量单位和质量匹配方法；用户问题不能扩展工具权限。经验模式进一步筛选本人有效案例：同采集类型、Agent、源码项目/版本，再按脱敏热点指纹和质量条件排序，最多50个候选、返回3条。
4. 当前问题、E1和知识进入同一次ReAct。Go检查Skill必需工具确实执行，每项假设至少引用本轮测量观测；仅引用历史先验、仅引用复测建议或模型自报knowledge均被拒绝。
5. 模型结束后重新检查权限、撤销、有效期及来源。重复读取必须与首次快照一致，不能用新空集掩盖已经撤销的旧经验。终态事务按统一锁序固定来源任务、案例和授权行后再次验证；报告的knowledge字段由程序附加。

## 版本化方法

目录：[diagnostic_skills.json](../api/internal/service/diagnostic_skills.json)，通过Go embed编入二进制。每个方法包含适用TaskKind、必要工具、步骤、反证、停止条件和验证标准。目录修改需要代码审查与版本更新，当前不支持用户上传可执行Skill或远程提示文件。

| ID | 重点 | 必须保留的边界 |
|---|---|---|
| cpu_hotspot | self/total热点及调用路径 | 高占比不证明业务回退或请求延迟 |
| allocation_gc | 分配、保留量与GC | 分配/GC符号不证明泄漏或暂停时长 |
| lock_io | 锁、等待、IO线索 | 正常事件循环也可能出现等待符号 |
| sampling_quality | 单位、样本、窗口、导入与函数栈 | 缺少负载与可比性时停止收益或因果推断 |

## 经过复核的经验

用户主动确认后才能保存。记录不包含用户问题、模型报告正文、原符号、源码或自由备注；仅固定枚举、脱敏条件、方法版本及来源身份进入经验。

- `reviewed_correction`：人工确认的证据不足、热点解释、因果、可比性或回归纠正；标为 `human_reviewed`，不称为机器验证根因。
- `verified_repair`：管理员保存同一调查关联的真实成功修复。核验当前登记源码版本、success/accepted、功能回归及原修复验收，标为 `controlled_benchmark_verified`。仅限隔离组件基准，不代表整站收益或生产发布。

每例绑定task/attempt/analysis job与run/诊断及可选修复；保存和审计原子提交，唯一键防并发重复及载荷替换。默认90天有效、可撤销；每次检索重验当前权限、最新采集轮次、结果摘要和源码版本。撤销保留原报告和审计。

当前只复用本人案例，按结构化条件与脱敏指纹检索；未实现跨团队共享、向量检索或模型自主学习。召回与复杂故障泛化仍需真实评测。

源码登记版本在数据库事务外经只读describe核验，不能宣称与外部执行器登记变更原子提交。describe限3秒，知识读取和列表整体限8秒，超时返回固定错误而非假装没有案例。

## HTTP与页面

- `GET /api/v1/diagnostic-skills`：方法目录。
- `GET /api/v1/diagnostic-cases?task_id=...`：本人可审阅案例，任务过滤可选。
- `POST /api/v1/tasks/:id/diagnoses/:run_id/cases`：确认保存，使用Idempotency-Key。
- `POST /api/v1/diagnostic-cases/:id/revoke`：撤销，正文为 `{}`。
- `/knowledge`：浏览方法、案例来源和撤销状态；诊断结果显示实际知识引用；成功源码调查提供验证案例保存入口。

字段与限制以 [HTTP契约](openapi.yaml) 和 [SQL009](../db/migrations/009_diagnostic_knowledge.sql) 为准。

## 固定故障评测

入口为 `scripts/evaluation/`，合成样本显式标记imported/synthetic，区分开发与留出样本。离线回归直接调用实际Go匹配器和报告验收器；Skill路由命中不能当作模型根因正确率。

真实对照保存相同固定输入在知识关闭/启用时的实际报告与工具轨迹。根因正确、误报与拒答的语义判断需独立人工标注；缺少标注、真实token或价格时对应指标留空，不编造分数。实跑检查与部署证据见本轮发布记录，本文描述实现，不能自行充当验收证据。
