# 文档索引

完整了解本项目，阅读 [项目详细说明](PROJECT_SUMMARY.md)：用途与操作链路、个人开发和测试职责、系统架构、模块通信、采集数据、事务与恢复、解析和可视化，以及真实测试结果和已知限制。AI 诊断只作补充。

阅读 [Drop 与 AI-CRM 项目技术说明](PROJECT_BRIEF.md)，可以在一万字以内了解两个项目的架构和开发、测试工作。Drop 展开功能与接口回归、自动化工具、采集分析验收、性能复现及缺陷定位；AI-CRM 展开任务编排、知识与 Skill、MCP 工具、权限审批、恢复与评测，各项连接到数据、状态、异常和验证细节。

需要先看懂数据怎样流转，可以直接打开 [14 张系统图解](diagrams/drop-system/index.html)：架构、数据流、采集/分析/AI时序、状态、恢复、业务信号和监控，附采集内容对照及“怎么点”的操作链。

| 目的                         | 文档                                                                                                                                                                     |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 从头学习这个项目             | [22 章项目教程](PROJECT_TUTORIAL.md)：源码精读、四语言实作、十二个机制实验，入口 `/book/chapter1/`；[实作材料](../tutorials/README.md)                                   |
| Agent本轮扩展实现与边界      | [八项扩展与使用](AGENT_EXPANSION.md) · [实时双服务遥测](../integrations/observability-demo/README.md) · [通用Python项目与回归](../integrations/generic-python/README.md) |
| 诊断方法、个人经验与固定评测 | [诊断知识层](DIAGNOSTIC_KNOWLEDGE.md) · [评测运行说明](../scripts/evaluation/README.md)                                                                                  |
| 按源码学习与准备讲解         | [中文注释阅读路线](CODE_READING_GUIDE.md)：普通采集、AgentScope循环、复测与隔离修复                                                                                      |
| 理解当前实现                 | [项目上下文](PROJECT_CONTEXT.md)、[权威契约](CONTRACT.md)、[HTTP OpenAPI](openapi.yaml)                                                                                  |
| 开发与运行                   | [本地开发](LOCAL_DEVELOPMENT.md)、[运维手册](RUNBOOK.md)                                                                                                                 |
| Agent 设计与责任边界         | [AgentScope 接入](AGENTSCOPE_INTEGRATION.md)、[诊断与修复闭环](DIAGNOSIS_AGENT_V2.md)、[产品与研究对比](AGENT_V2_RESEARCH.md)                                            |
| 实时演示                     | [面试步骤](INTERVIEW_DEMO.md)、[多场景](DEMO_SCENARIOS.md)、[Saber 接入](SABER_INTEGRATION.md)                                                                           |
| 当前交付                     | [AgentScope 发布](releases/20261020-agentscope.md)、[交付矩阵](DELIVERY_MATRIX.md)、[剩余边界](CAPABILITY_GAPS.md)                                                       |
| 性能测试口径与实测           | [性能测试](PERFORMANCE_TESTING.md)：已有四类测量的对象、指标口径、Locust/JMeter 取舍、公开层实测收据                                                                      |
| GitHub 源码与录像            | [发布方式及公开范围](GITHUB_PUBLICATION.md)                                                                                                                              |

`history/` 和较早的 `evidence/` 是历史记录。最新版本及实际运行证据以当前发布说明为准，不将旧版本测试自动算作当前版本重新通过。
