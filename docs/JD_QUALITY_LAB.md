# 面向测试开发岗位的质量实验

本轮把既有性能诊断平台补为可复现的测试工具包，覆盖业务测试设计、真实接口拒绝矩阵、状态机组合、协议未知输入、内存安全、性能实验和AI评测。入口见 [工具包](../scripts/quality/README.md)；所有实际执行记录均注明来源与边界。

## 岗位要求与落地范围

| 岗位方向 | 新增内容 | 通过依据与边界 |
| --- | --- | --- |
| 测试工具与平台 | 真实CRM联系人契约、模型有界计划、变异负控与重放收据 | 使用实际FastAPI/Pydantic/服务/MemoryRepository，业务记录及身份明确合成；不冒充全CRM/PG/RLS验收 |
| 接口与安全测试 | 41个Go HTTP拒绝场景、2个成功读取对照 | 真实隔离PG；逐次核对18张领域表，不把403状态码本身当作无副作用证明 |
| 并发和后台测试 | 4个固定seed各96步，结合既有取消/派发竞争 | 调用真实Repository、事务、权限与Outbox；不是复制生产转移表测试自己 |
| 协议和解析器 | 3个Go原生Fuzz、固定性质与最小失败语料 | 单worker/时间/输入预算；无崩溃不代表全输入完备 |
| C++质量 | 独立ASan/UBSan/泄漏检查及Werror | 选定6个CTest与协议变异；发布镜像默认不开消毒器 |
| 性能分析 | 真实Core心跳与py-spy扰动、阶梯和持续窗口 | 有限负载和资源上限，不是最大容量或长期稳定性认证 |
| AI辅助质量 | 原生AgentScope工具选择场景、7份实际新诊断与1个非法输入 | 输入/oracle分离，受控标签匹配与人工语义评分分列；不自称外部盲测 |
| 自动化落地 | 独立GitHub质量workflow，精确场景不允许skip冒充通过 | 普通PR不用生产或付费模型，真实模型实验另行运行并保留全部结果 |

## 三种互相补充的缺陷验证

联系人业务契约固定47个场景，包含等价类与边界值、角色/租户判定、分页游标、异常JSON和创建→修改→读取序列。sham包装应保持通过；移除租户过滤、重复手机号防线或角色门槛的合成错误应被杀死。变异只发生在隔离内存副本，不修改原CRM仓库，也不是发现了三个生产漏洞。

状态机测试对实际Repository生成操作序列，检查旧attempt/授权失效、重试命名空间、取消幂等和事件一致性。关键生产函数的语句覆盖基线为Cancel72.0%、Retry75.0%、DispatchOne65.1%，门禁分别为70%、73%、63%；这是选定用例的函数语句覆盖，不是全仓或分支覆盖率。门禁还要求指定场景真正执行成功，不能仅靠覆盖率数字过关。

Agent评测预注册8个不同输入：CPU、正常对照、证据不足、符号/直接指令注入及非法权重。模型只收到问题与输入，拿不到判分oracle。结构标签Top3仅比较预注册标签与受约束`hypotheses.kind`；它不验证自由解释、真实业务根因或正常负载误报。独立人工模板绑定实际报告/分析摘要，未有真人评审时语义准确率、语义误报和拒答指标继续保持null。

## 实际结果与复现

本轮执行结果在完成验证后同步到[交付矩阵](DELIVERY_MATRIX.md)，命令、输出协议与预算分别见各模块README。每次执行使用新输出目录；原失败和后续修复回归分开保存。真实业务源码、凭据、Cookie、RAW和私有诊断正文不进入GitHub证据。

已执行的直接证据：[本机CRM与变异](evidence/jd-quality-20261023/business-acceptance.json)、[服务器CRM](evidence/jd-quality-20261023/business-server.json)、[实际模型计划](evidence/jd-quality-20261023/business-model-plan.json)、[HTTP与PG安全](evidence/jd-quality-20261023/security-postgres.json)、[风险覆盖](evidence/jd-quality-20261023/lifecycle-risk-gate.json)、[C++消毒器](evidence/jd-quality-20261023/cpp-sanitizers.json)、[真实采样分析](evidence/jd-quality-20261023/sampling-analysis-chain.json)、[心跳](evidence/jd-quality-20261023/core-heartbeat.json)、[最终扰动测量](evidence/jd-quality-20261023/performance-final.json)、[Agent评分](evidence/jd-quality-20261023/agent-score.json)。

两轮扰动数据均保留：[第一轮](evidence/jd-quality-20261023/performance-first.json)与[最终驱动轮](evidence/jd-quality-20261023/performance-final.json)。结果波动明显，尤其配额、调度和短窗口会影响相对数值；不能从它们推导采样频率和开销始终单调，或宣称业务没有受到影响。

面试建议展示“风险→设计方法→实际执行→缺陷→最小复现→修复→回归”链条，选择能解释清楚的案例。平台指标、采样器指标、业务指标及模型指标分别说明分母、单位、环境和未覆盖范围。

## 保留的验收边界

Android/iOS真机、跨架构实机、完整平台主机故障/长稳、全部采集器扰动、真实CRM数据库/RLS和所有业务接口尚未由本实验覆盖。真实Runner→S3→Analyzer门禁不包含Go HTTP建任务、Agent gRPC投递或浏览器；原有真实平台发布验收与分层回归继续保留。独立人工语义评审需要真实审阅者，不能由开发Agent填写为“已人工通过”。
