# 诊断 Agent 八项扩展

本次为 Drop 增加结构化事实校验、动态补证、跨服务业务信号、通用Python项目登记、审阅方法与经验库、持久恢复及容错、可复现评测、生成回归与补丁包。规划、诊断、源码修复继续使用官方 AgentScope 2.0.10 与真实 DeepSeek；Go负责权限、状态、来源与最终接受。

实现与使用见 [扩展说明](../AGENT_EXPANSION.md)，逐项边界见 [交付矩阵](../DELIVERY_MATRIX.md)，现场步骤见 [面试演示](../INTERVIEW_DEMO.md)。当前精确版本、镜像与源摘要以部署收据为准，历史尝试不补发成功标签。

已上线至[当前服务器](https://82.157.81.79)：API20261021.9、AgentScope runtime20261021.4、Web20261021.6、Analyzer20261021.2（0.2.5）、SDK2.0.10、SQL10。功能提交8fab193已推送GitHub；最终一次只替换API，其他28个容器身份、镜像及数据卷、三项目登记快照和执行器broker保持。[实际部署收据](../evidence/agent-expansion-20261021/deployment-r10.json)记录29运行健康、7监控up及readiness200。

已验证的结果分别说明：

- 通用Python修复10实际生成代码与新增回归，基线断言失败、候选通过，原回归、输出类型和值以及性能正负对照均通过。32.36秒与8.2122×只属于合成购物车函数，不代表CRM、Saber或生产延迟。
- 真实双服务调查19按同一437字目标完成Python CPU任务109/诊断93，明确确认followup3后完成RESOURCE任务110/子诊断94；10个实际资源样本、结构化事实的来源/值/单位及程序摘要均通过。实际查询四类信号，指标、日志和Trace来自真实gateway→catalog HTTP、SQLite及CPU负载，changes窗口无匹配变更也保留为空；它是受控合成业务。跨采集器追加观测不作为同类可比复测，不据此宣称已确认根因。
- 固定四场景的API5原始评估仅4/24报告通过，失败全部保留。修复后API7使用同输入、同顺序和全新命名空间完成24/24，off、experience各12/12；这是已见案例的调试复测，指标为程序接受率，独立人工语义准确率与费用仍为空。

证据：[真实修复](../evidence/agent-expansion-20261021/live-generic-acceptance.json)、[真实业务补证闭环](../evidence/agent-expansion-20261021/live-business-acceptance.json)、[两批逐次对比](../evidence/agent-expansion-20261021/evaluation24-api7-debug-retest/acceptance-comparison.json)、[独立人工复核空模板](../evidence/agent-expansion-20261021/evaluation24-api7-debug-retest/human-review-template.json)。

工程验证包括232项Go定向回归及全APIvet、Linux SDK56项、解析器20项，以及真实隔离PG到解码子进程的来源回归。API9另完成资源补证/重复采集回归29项、0skip和全APIvet，包含真实PG生命周期与导入单元负控，见[增量回归收据](../evidence/agent-expansion-20261021/api9-regression.json)。此前整体PG392通过/1真实模型opt-in跳过；各次检查来源与运行范围分开记录，不将早期全量结果算作每版重跑。本次没有改C++或重新验收全部Runner。

现场采样暴露的空栈和数据库来源读取问题已保留失败并补回归：py-spy未知栈使用明确占位符，保留全部权重；仅真实当前attempt来源可以适配，通用collapsed校验不放宽。任务102通过正常HTTP重分析，在原job新run3中成功解析380权重/未知1，原run1/2失败归档与原始SHA保持。未知占位不允许当业务热点或源码位置。

完整现场流程及原失败均保留：API5报告预算、资源事实投影、结构化错误反馈、py-spy空栈及SQL来源别名、E1补证来源的缺口分别修复并新增回归。API8、R9发布保护曾因单次监控/代理瞬态检查触发回滚；保留失败记录后，对同一候选镜像增加有界就绪等待并重新通过，没有删除卷或覆盖旧镜像。

资源快照、业务时长、拓扑与调用栈各有独立单位和来源；新增观测支持继续调查，不能单凭同一时间出现就宣称根因确认。通用源码执行目前验证Python，团队共享的是审阅方法，熔断为进程内策略，业务代码补丁仍需单独发布。自由解释仍可能出错，独立人工语义评估和长期稳定性没有被本次流程验收替代。

公网最终以新调查19/任务109与110实测1440/375两宽，10项检查、8张原始截图通过；事实引用绑定各自诊断93/94，资源样本数与峰值逐值/单位匹配，键盘、axe、页面异常、接口错误及溢出检查通过。见[公网页面收据](../evidence/agent-expansion-20261021/public-api9-root19-resource110/summary.json)。本次确认由助手使用已授权管理员API执行，独立人类点击未观测，工具不会仅因模型提案自行采集。

GitHub前两版功能提交[21aa30d](../evidence/agent-expansion-20261021/github-ci-21aa30d.json)、[4f35e9b](../evidence/agent-expansion-20261021/github-ci-4f35e9b.json)均7/7通过。最新API9提交8fab193于11:18:10 UTC观测为6/7通过，Analyzer仍在运行、无失败；[精确CI状态](../evidence/agent-expansion-20261021/github-ci-8fab193.json)保留观测时间，不将其写成已通过。

最后[只读健康检查](../evidence/agent-expansion-20261021/final-health.json)于11:19:09 UTC通过：29容器运行、配置健康检查全部健康、无OOM，7监控up、严格TLS readiness200；所有执行中任务及待确认补证均为0。冻结项目逐文件校验通过，执行器服务运行，演示负载已停止。该记录是当时状态，不是长期可用性承诺。
