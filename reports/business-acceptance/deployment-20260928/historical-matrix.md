# 2026-09-28 严格验收准备：2026-09-20 原始失败矩阵

只读核对原始 campaign 与 21 个 case 的规范化 SHA；没有修改历史成绩，也没有向远端注入故障。

实际汇总：{'lineage_verified': 10, 'root_cause_accepted': 1, 'cleanup_verified': 21, 'recovery_observed': 21, 'root_gate_verified': 1}。当前文档中“链路合同没有失败项”与原始字段不符；旧索引的 12/21 来自其他批次，不能混用。

| 场景 | 链路 | 根因 | 根因门禁 | 最后报告 | 诊断 ID |
|---|---|---|---|---|---|
| cpu-hotspot | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_337db834b6bc4f979e48413480ff6956` |
| source-hotspot | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_025469967add4a66a8c2329569729775` |
| memory-pressure | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_1a3e73cbd9a348b2b8c286b06e283dda` |
| io-write-latency | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_fbc80b80e540430c941d6d8111a8d5ee` |
| noisy-neighbor | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_07607d9fde9b4cc6ad51a079f9d521bf` |
| load-saturation | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_1b654a786f5442bf89374f42e6853bee` |
| queue-backlog | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_8ef7b69b14d944a6a8315b0981b7adf6` |
| go-cpu-hotspot | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_dbd860689e4645239b8852802f4ae55f` |
| go-network-latency | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_031b7253521548139fe38b208ae42529` |
| go-memory-growth | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_533b9fe6bdde40ddb59200d133283671` |
| go-file-io | False | False | False | INSUFFICIENT_EVIDENCE | `insight_12caeb81fc5c4d2895cc78a3d481e05c` |
| java-gc-pressure | True | True | True | PARTIAL_WITHOUT_COUNTER | `insight_3255fe70fe4f45f98e9cdacb9995c0a6` |
| java-lock-contention | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_b7c819648c5049de800d42fc7a680629` |
| java-downstream-latency | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_cdb9dbf502ba4023b9db9fb7bf66760f` |
| java-offheap-growth | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_e811d9081ac14208972b9dc422ab9fc8` |
| java-file-io | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_1c57db1277504610ad70e918f9ba9616` |
| cpp-cpu-hotspot | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_4cef207cc5ef45a7af435df2ba39bad0` |
| cpp-lock-contention | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_dc1bc44e1add44ce8b46de3e3e75b807` |
| cpp-memory-growth | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_f522bad903c34ba48fcb559434a90738` |
| cpp-file-io | False | False | False | PARTIAL_WITHOUT_COUNTER | `insight_582f8206b7a244628ff6f5bf7ababa04` |
| cpp-downstream-latency | True | False | False | PARTIAL_WITHOUT_COUNTER | `insight_b2bb7338f5fa43b3b8f7086bbe427021` |

优先级：先复验 source-hotspot、go-cpu-hotspot、cpp-cpu-hotspot，它们已有具体且场景匹配的发现但缺独立对照；java-gc-pressure 为防回归通过基准。然后处理 Java lock（具体结论但链路未通过）；I/O、network、load、memory 等先排采集与证据归属。

memory-pressure 的链路失败为 SSL EOF（读取 task attempts），不能解释成业务内存假设为假。其余失败以逐场原始 error 与 tool/task 记录核对。

新运行建议：所有场景串行；三窗测量、故障最多300秒、诊断240秒、终态排空最多330秒。21场通常80–110分钟；故障撤销恢复不等于同负载代码修复。

运行器现在自动记录本地 Git HEAD 与执行源码SHA；主协调器必须传入只含公开版本信息的 deployment_provenance（实际 release、image ID、服务源码SHA、native agent版本），不能传环境变量或凭据。没有来源时显式 NOT_PROVIDED，不能宣称同版验收。

每批采用新 output 路径；已有 campaign 或 cases 目录一律拒绝覆盖。新结果须独立存档、人工审查具体发现后由生成器更新索引，不能手工回填历史成绩。
