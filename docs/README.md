# Mini-Drop 文档导航

## 新会话接手

- [`Mini-Drop新会话交接文档.md`](Mini-Drop新会话交接文档.md)：跨会话续作的完整入口，记录当前代码现场、架构、云端状态、最近修复、启动验收、测试矩阵、常见故障、剩余优先级和禁止操作。

## 汇报与演示

- [`guides/Mini-Drop全功能跑通手册-复刻功能与AI方案.md`](guides/Mini-Drop全功能跑通手册-复刻功能与AI方案.md)：唯一的全流程操作主稿，覆盖本地/云端启动、基础采集、AI 循证诊断、统一测试集、真实 Campaign、按钮含义和故障排查。

文档按“架构、操作、AI、接口、测试集”分组。阶段性聊天记录、旧页面截图、重复方案和临时评审不放在 `docs/`。

## 建议阅读顺序

第一次接触项目：

1. [零基础教程](guides/getting-started.md)
2. [复刻功能与 AI 全功能跑通手册](guides/Mini-Drop全功能跑通手册-复刻功能与AI方案.md)
3. [完整运行手册](guides/operations.md)
4. [全功能验收方案](guides/acceptance.md)
5. [详细流程图与时序图](guides/Mini-Drop详细流程图与时序图.md)
6. [复刻指南](architecture/replication-guide.md)

## architecture：系统边界与实现状态

- [系统设计](architecture/system-design.md)：领域对象、状态机、数据流和部署设计。
- [Drop 复刻指南](architecture/replication-guide.md)：C++ 核心、Go API、Python 分析和 React Web 的目标架构。
- [跨语言实现状态](architecture/implementation-status.md)：当前已迁移能力、真实链路和剩余迁移边界。
- [仓库结构](architecture/repository-layout.md)：每个顶层目录的职责与迁移期保留原因。
- [未完成项](architecture/remaining-work.md)：按复刻指南划分的 P0、P1、P2 后续工作。

## guides：运行与验收

- [零基础教程](guides/getting-started.md)
- [Control SSH 准备手册](guides/control-ssh-preparation.md)
- [复刻功能与 AI 全功能跑通手册](guides/Mini-Drop全功能跑通手册-复刻功能与AI方案.md)
- [完整运行手册](guides/operations.md)
- [全功能验收方案](guides/acceptance.md)
- [详细流程图与时序图](guides/Mini-Drop详细流程图与时序图.md)
- [多节点部署](guides/multi-node-deployment.md)
- [原生 Linux eBPF 验收](guides/native-linux-ebpf-acceptance.md)
- [数据库迁移操作手册](guides/database-migrations.md)

## ai：AI 诊断设计

- [循证开放式性能诊断](ai/evidence-driven-diagnosis.md)：假设、取证、反证、结论和验证闭环。
- [导师意见与设计取舍](ai/design-decisions.md)：测试集、开放世界诊断及公开项目采用边界。
- [多轮反证闭环](ai/falsification-loop.md)：工具预算、人工审批和停止条件。
- [Golden 质量门禁](ai/quality-gates.md)：回归场景与发布阈值。
- [任务归档与证据保留](ai/task-archive-policy.md)：归档任务时保留 AI 证据链。

## contracts：接口与证据契约

- [Drop Insight API](contracts/drop-insight-api.md)
- [采集器、分析与 AI 证据契约](contracts/collector-evidence.md)
- [Go 诊断查询接口](contracts/go-diagnosis-query.md)

## benchmarks：统一测试集

- [开源项目、论文与资料索引](benchmarks/sources.md)
- [测试集执行结果](../reports/benchmark/README.md)
- 机器可读用例位于仓库根目录 `benchmarks/`。
- Golden 回归输入位于仓库根目录 `golden_scenarios/`。

## 维护规则

1. 完整端到端顺序更新 `guides/Mini-Drop全功能跑通手册-复刻功能与AI方案.md`；
2. 运维命令更新 `guides/operations.md`，专项验收步骤更新 `guides/acceptance.md`；
3. 接口字段变化同步修改 `contracts/`；
4. AI 策略变化同步修改 `ai/`，不再新增相近的阶段方案；
5. 机器执行结果放入 `reports/`，文档只保留摘要和复现入口；
6. 构建产物、依赖缓存和本地密钥不进入仓库。
7. 教程截图只维护 `images/tutorial-v2/`；页面改版后直接替换，不再保留旧版截图目录。
