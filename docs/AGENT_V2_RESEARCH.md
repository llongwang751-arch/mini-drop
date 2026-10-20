# 性能诊断 Agent V2：公开方案对比与实现取舍

核对日期：2026-10-20。以下依据厂商文档与论文，不是对商业产品准确率的独立测评。本仓库是导师复刻链路上的开发实现，不代表腾讯内部 Drop Agent 源码或认证。

| 方案 | 可借鉴的能力 | 本项目采用的做法与差距 |
| --- | --- | --- |
| [Datadog Automated Analysis](https://docs.datadoghq.com/profiler/automated_analysis/) / [Bits Code](https://docs.datadoghq.com/bits_ai/bits_code/) | 从持续剖析找代码问题，将观测证据带入代码修复 | 保存真实热点、源码SHA和测试结果；当前只有登记的 Python CRM 场景，没有其持续剖析规模和代码托管集成 |
| [Grafana Assistant](https://grafana.com/docs/grafana-cloud/platform/grafana-assistant/guides/querying/) | 自然语言查询已配置的指标、日志、追踪和 Pyroscope 数据 | 计划器读取实际 Agent 能力与参数契约，复测比较可比性和证据限制；尚未实现跨信号的通用事故调查 |
| [Dynatrace Agentic Incident Analysis](https://www.dynatrace.com/hub/detail/agentic-incident-analysis/) | 结合问题上下文的调查工作流 | 调查、复测、源码修复分开持久化，绑定身份与版本；没有其服务拓扑和全站因果模型 |
| [PerfAgent 论文](https://arxiv.org/abs/2607.19653) | profiler 引导、验证器反馈、迭代性能优化；通过测试并不等于优化有效 | 真实 profile → 读源码 → 隔离补丁 → 原测试 → 输出一致性 → 交替A/B → 补丁后 profile。实现完成后补充发现该论文，属于方向对照，没有采用其代码或复现其基准 |
| [ReAct 原论文项目](https://react-lm.github.io/) | 动态选择工具，接收观测后调整下一步行动 | DeepSeek tool_calls 交互循环；展示行动、观测和简短理由，不保存隐藏思维链。不是前端 React，也不是固定工具顺序的包装 |

旧版本的问题确实存在：从已有任务开始、同类复测、人工再点诊断、没有源码干预。V2增加了从零规划的持久化调查和复测后自动再次诊断，并把真实 CRM 源码测试接入工具循环。

证据编号校验只能证明引用存在。源码SHA、真实行号、未修改的测试、输出摘要和多组CPU对照分别验证版本、位置、功能与性能；它们仍不能证明模型每句话正确。重复采样是现象复现，不是因果干预。多个优化一起修改时，测试验证的是优化组合，不能归因到其中每一行。

不以引入框架名称代替能力：Drop 的长任务、授权、租约和SQL状态机仍在Go层；C++采集和Python分析独立。CRM原有AI编排没有改写，也没有把另一个Agent框架植入它的业务流程。
