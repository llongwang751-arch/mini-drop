# 文档入口

仓库维护当前实现文档，以及明确标注未实现范围的后续设计：

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
- [`AGENT_RUNTIME.md`](AGENT_RUNTIME.md)：框架选择、Runtime、Harness、Theme、上下文、记忆与可恢复搜索
- [`COMPLETION_AUDIT_20260908.md`](COMPLETION_AUDIT_20260908.md)：九项未闭环问题的逐项状态、证据与不可夸大边界
- [`SKILL_AB_INSUFFICIENT_EVIDENCE_POSTMORTEM_20260908.md`](SKILL_AB_INSUFFICIENT_EVIDENCE_POSTMORTEM_20260908.md)：Skill A/B 证据不足的真实时间线、队列/范围/编排缺陷、修复和真机复测
- [`SKILLS.md`](SKILLS.md)：Skill 格式、检索、激活和发布边界
- [`COMPETITOR_DESIGN_DECISIONS.md`](COMPETITOR_DESIGN_DECISIONS.md)：竞品机制、设计取舍、代码与测试证据
- [`contracts/`](contracts/)：OpenAPI、事件和采集协议

历史汇报、截图、面试稿、旧架构说明、旧 Benchmark 报告与测试数据集不再作为仓库事实来源。
