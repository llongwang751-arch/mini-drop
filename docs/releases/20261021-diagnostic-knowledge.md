# 诊断方法、经验复用与评测发布（2026-10-21）

本次为 AgentScope ReAct 增加四类版本化诊断 Skill、个人案例检索和固定评测。它们已部署至 [Drop](https://82.157.81.79/knowledge)，功能源码为 `387144ad25b584a78a887a647acb01e886967ac7`；后续文档提交不改变运行镜像。架构、权限与接口见 [实现说明](../DIAGNOSTIC_KNOWLEDGE.md)。

## 当前行为

- 四类 Skill：CPU 热点、分配/GC、锁/IO、采样质量。每类包含版本、必要工具、步骤、反证、停止条件和验证标准；按本轮测量匹配并约束工具执行。
- 三种知识模式：`off`、`skills`、`experience`；默认经验模式。每个诊断持久化选择与实际知识来源，支持相同输入对照。
- 本人案例：用户确认的固定纠正类别，或管理员关联的真实受控修复回执；有效期90天，可撤销。按权限、采集类型、Agent、源码项目/版本、单位、质量和脱敏指纹筛选，最多50候选/3返回。没有向量库、跨团队共享或模型自主学习。
- 历史只作先验：模型假设必须引用本轮观测；Go 核对必要工具和首次知识快照，在终态事务中锁定来源、案例、权限并再次校验。外部源码登记与 PostgreSQL 不具有跨系统原子性。
- 前端 `/knowledge` 展示方法、来源和撤销状态；诊断结果显示实际加载知识，并提供经过确认的案例保存入口。

DeepSeek 首轮曾提出超出每轮两个工具的批次，诊断33在执行前失败，记录保留。修复后对合法但数量超限的批次整批不执行，在原四次普通模型预算内反馈重选；不提高预算或截取部分执行。未知工具、非法参数和格式专用轮工具仍拒绝。

## 实际执行的验证

| 检查                              | 实际结果与范围                                                                                                                     |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Go1.26.10 + 真实隔离 PostgreSQL17 | 全九次迁移，256通过，1项真实模型 opt-in 跳过；vet通过                                                                              |
| AgentScope SDK2.0.10              | Windows28通过/9 Linux专用跳过；最终Linux候选镜像37/37、0skip                                                                       |
| Web                               | 143单测、构建、Prettier；知识UI9场景，5次axe零违规                                                                                 |
| 契约                              | HTTP80路由/97 schemas、18 TaskKinds/4 RPC、V1负控通过                                                                              |
| 固定评测                          | 22例显式合成输入，19/19合法Skill路由、22/22输入边界、209/209报告防线；评分器16项通过                                               |
| 公网浏览器                        | 1440/375真实认证GET检查，无横溢/页面脚本异常，8 JS/CSS和index与发布产物SHA一致；四次axe零违规，保留桌面既有控件标签的1类incomplete |
| Go漏洞检查                        | 可达漏洞0项；保留1项未调用require模块提示，不等于整个供应链零漏洞                                                                  |

证据：[本地检查](../evidence/diagnostic-knowledge-20261021/local-checks.json)、[真实PG](../evidence/diagnostic-knowledge-20261021/postgres-acceptance.json)、[离线评测](../evidence/diagnostic-knowledge-20261021/evaluation-offline.json)、[公网](../evidence/diagnostic-knowledge-20261021/production/summary.json)、[安全扫描](../evidence/diagnostic-knowledge-20261021/security-scan.json)。GitHub 精确功能提交387144a的[CI收据](../evidence/diagnostic-knowledge-20261021/github-ci.json)最终7/7全部通过，包括Analyzer和维护真实PG/S3集成，实际观测时间2026-10-21 06:05 UTC。[Actions运行](https://github.com/llongwang751-arch/mini-drop/actions/runs/39551510896)对应明确功能提交，后续文档收尾没有重复触发构建。

## 真实模型对照与已知问题

开发种子任务88/89的两个案例为显式合成验收操作，不是独立人工准确率标注。CPU与分配两个可见留出输入分别运行知识关闭/启用：

| 固定导入任务 | 关闭知识   | 启用经验   | 实际加载         |
| ------------ | ---------- | ---------- | ---------------- |
| 90：CPU结构  | 诊断34完成 | 诊断35完成 | case1、对应Skill |
| 91：分配线索 | 诊断36完成 | 诊断37完成 | case2、对应Skill |

四次使用真实 DeepSeek、SDK工具循环和实际API输入；不是新的现场CPU采集。每项假设都引用本轮观测，但引用合法不保证解释正确。定性复审发现：诊断37称排名1为“唯一叶帧热点”，实际有70%和30%两个叶帧；诊断35将部分复测参数归因于E7，但工具实际只返回手动导入建议。另有结构节点数与统计样本量、导入权重物理含义等措辞注意项。

[Agent定性审阅](../evidence/diagnostic-knowledge-20261021/agent-review.json)明确不是独立人工标注。[真实对照评分](../evidence/diagnostic-knowledge-20261021/evaluation-live.json)保留根因命中、拒答、误报语义指标为null；没有定价依据，费用也为null。仅两组各一次off/on，不能认定经验提升或降低准确率，也不能将耗时差解释为性能提升。模型解释仍须复核。

另实际保存case3，复用既有Saber调查11/修复9/任务86/诊断29的真实受控修复回执，核对当前登记版本、success/accepted及功能回归；本次没有新增该业务模型调用、采集或补丁基准。见[修复案例来源收据](../evidence/diagnostic-knowledge-20261021/verified-case.json)。

## 部署与回退边界

首次发布替换API/runtime/Web三个应用，执行SQL009前保存受保护数据库备份；其余24个容器保持。补丁仅替换API/runtime，其他25个容器保持，Web未再次构建。最终API/runtime/Web均为`20261021.2`，SDK2.0.10、SQL9，Agent/Helper仍`20261020.3`。保留原命名镜像、卷和源码执行器，未改业务源码登记快照。

最终只读检查为27容器运行健康、7监控up、HTTPS readiness200、无进行中诊断，三条有效案例和诊断34–37完成。见[首次部署](../evidence/diagnostic-knowledge-20261021/deployment-first.json)、[最终补丁](../evidence/diagnostic-knowledge-20261021/deployment.json)与[最终健康](../evidence/diagnostic-knowledge-20261021/final-health.json)。服务器源文件按发布清单同步，不把整个Git树称为已部署；最终文档/证据收尾无需重建容器。

回退应用可选择保留的旧命名镜像，并按运维手册重新加载代理；SQL009默认兼容旧记录，本次未演练schema降级或数据库恢复。不能把“存在备份”写成“本次已完成恢复演练”。
