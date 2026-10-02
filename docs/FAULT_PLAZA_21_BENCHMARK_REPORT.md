> **2026-09-19 更正：本文原为未获原始实验支持的历史草稿，不是有效 Benchmark 报告；虚构正文已于 2026-09-20 删除。**
> 原文声称的 20/21、95.2%、Baseline 9/21、采纳率及"性能提升"均没有可回放的对应实验，从未运行过任何 ReAct baseline，禁止引用为项目成绩。

## 当前可核查的事实口径

- 21 个白名单故障场景（Python 7、Go 4、Java 5、C++ 5）的定义、注入物理量与 Oracle 以 `server/app/drop_insight/fault_plaza.py` 的 `SCENARIOS` 为准；本文不复制场景清单，避免与代码漂移。
- 严格验收运行记录：[`fault-plaza-strict-21-20260910-r2.json`](../reports/ai-diagnosis/fault-plaza-strict-21-20260910-r2.json)，21 项执行，1 项通过，20 项未通过。通过数不是根因准确率；且该成绩在 2026-09-20 证据门禁收紧（取消捏造覆盖槽位）之前测得，在完成 21 场景复测之前不得引用为当前能力。协议见 [严格验收协议](FAULT_PLAZA_ACCEPTANCE.md)。
- 可复现的受控回放基准（非真机实验）见 `reports/evaluation/evaluation_report.json` 与 [SKILLS.md](SKILLS.md) 的口径说明；"多轮 LATS vs 单轮 ReAct"的公平对照尚未完成，受控路线回放环境不适合做该对照，正确 venue 是冻结回放环境。
- 背景与教训见 [实施报告](../reports/architecture/performance-sre-agent-implementation-20260919.md)。
