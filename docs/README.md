# 文档入口

- [`DEMO_WALKTHROUGH.md`](DEMO_WALKTHROUGH.md)：当前云端从 AGI-saber 上传、知识库问答到 Mini-Drop 指标、体检树和故障恢复的截图式演示步骤

- [2026-09-24 云端验收数据清理](../reports/business-acceptance/cloud-data-cleanup-20260924.md)：旧长文档与构建缓存清理、磁盘回收、向量检索重启复验

- [`FULL_CHAIN_ACCEPTANCE.md`](FULL_CHAIN_ACCEPTANCE.md)：AGI-saber 百万字上传、真实向量检索、Mini-Drop 后台指标/排查树、故障撤销与验收判据

- [2026-09-19 Agent 预算与证据缺口改进](../reports/architecture/agent-deadline-20260919.md)：模型阶段预算、晚到审批拒绝、缺口字段与云端回归

- [SRE 诊断 Agent 定位与开源源码对照](../reports/architecture/sre-diagnosis-agent-design-20260919.md)：循证、性能树、ReAct/LATS 分层、三路 RAG、记忆与 Harness

仓库维护当前实现文档，以及明确标注未实现范围的后续设计：

- [2026-09-19 知识扩充与质量改进](../reports/architecture/sre-quality-roadmap-20260919.md)：39 块真实检索、采样口径实验、开源集成边界与后续验收

- [2026-09-19 SRE Agent v5 云端发布](../reports/architecture/cloud-release-20260919.md)：实际运行、ReAct/LATS 链路验收、版本与回滚，以及尚未达成的根因/修复验证

- [2026-09-19 Windows 本地运行验证](../reports/architecture/local-sre-run-20260919.md)：本机入口、真实模型诊断、检索与采集结果，以及未完成边界

- [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md)：跨聊天使用的当前架构、部署决定与权威文档索引
- [`INTERVIEW_DEMO_GUIDE.md`](INTERVIEW_DEMO_GUIDE.md)：面试现场的页面点击路径、讲稿、预期结果、真实性边界与故障速查
- [`INTERVIEW_DEEP_DIVE.md`](INTERVIEW_DEEP_DIVE.md)：100 道项目与基础知识深挖题，覆盖 PostgreSQL、操作系统、计网、Go/C++/Python、Agent/RAG/Skill/LATS、大模型基础、真实问题复盘，含回答要点、追问、源码与资料来源、三场模拟面试及十四天训练；是面试专题材料，架构事实仍以当前上下文为准
- [`PROJECT_LEARNING_GUIDE.md`](PROJECT_LEARNING_GUIDE.md)：唯一的项目总教程；覆盖页面控件、可放大插图（含修复后的 Java、报告、探索树与三个 Agent 云端实拍）、基础采集、AI/LATS/Skill/记忆链路、540/500 测试集、云端演示、面试延伸，前置知识、40 个专题问答，以及由生成器维护的目录和逐文件字典；可生成离线 HTML 阅读版
- [`REPLICATION.md`](REPLICATION.md)：单机/多机复刻、真实故障与 FULL_LATS 冻结回放验收
- [`AI_DIAGNOSIS.md`](AI_DIAGNOSIS.md)：AI 诊断流程、自动范围、LATS 运行语义与证据门禁
- [`FAULT_PLAZA_ACCEPTANCE.md`](FAULT_PLAZA_ACCEPTANCE.md)：21 场景严格验收协议、链路/根因/恢复的分项口径与原始证据位置
- [`BUSINESS_ACCEPTANCE.md`](BUSINESS_ACCEPTANCE.md)：业务请求样例、同负载修复比较、测试计划与 CI、页面入口；明确本地测量与 AI 根因验收边界
- [`SERVICE_INTEGRATION.md`](SERVICE_INTEGRATION.md)：原办公助手及四个轻量业务的独立部署、原网页、request_id 关联、可信进程绑定与 JVM 采集保护
- [`BUSINESS_ONBOARDING_DESIGN.md`](BUSINESS_ONBOARDING_DESIGN.md)：业务方如何接入、请求与阶段关联、当前代码缺口、一个完整修复案例的实施顺序；首批四业务已接入请求关联，函数阶段、数据库连接器与完整修复闭环仍为后续设计
- [轻量业务接入与验收](../reports/business-acceptance/轻量业务接入与验收-20260914.md)：四个原业务入口、账号位置、六张截图、基础操作、采集失败与修复复验、未完成项
- [2026-09-19 Agent 改造实施记录](../reports/architecture/performance-sre-agent-implementation-20260919.md)：混合检索、按需工具、历史记忆、Grafana 适配及 ReAct 对照；区分本地验证与尚未发布范围
- [`AGENT_RUNTIME.md`](AGENT_RUNTIME.md)：框架选择、Runtime、Harness、Theme、上下文、记忆与可恢复搜索
- [`COMPLETION_AUDIT_20260908.md`](COMPLETION_AUDIT_20260908.md)：九项未闭环问题的逐项状态、证据与不可夸大边界
- [`SKILL_AB_INSUFFICIENT_EVIDENCE_POSTMORTEM_20260908.md`](SKILL_AB_INSUFFICIENT_EVIDENCE_POSTMORTEM_20260908.md)：Skill A/B 证据不足的真实时间线、队列/范围/编排缺陷、修复和真机复测
- [`SKILLS.md`](SKILLS.md)：Skill 格式、检索、激活和发布边界
- [`COMPETITOR_DESIGN_DECISIONS.md`](COMPETITOR_DESIGN_DECISIONS.md)：竞品机制、设计取舍、代码与测试证据
- [`contracts/`](contracts/)：OpenAPI、事件和采集协议

历史汇报、截图、面试稿、旧架构说明、旧 Benchmark 报告与测试数据集不再作为仓库事实来源。
