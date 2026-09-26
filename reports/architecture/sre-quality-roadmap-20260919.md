# 性能 SRE Agent：知识扩充与质量改进

本批目标是提升可验证能力。未经执行的实验保持待办，不能把采集成功、报告 VERIFIED 和同负载修复混成一个成绩。

## 发布与实际验证

- 已发布 `/opt/mini-drop-releases/20260919T132400Z`，仅滚动替换 Diagnosis Worker，镜像 `mini-drop-knowledge:20260919T132400Z`。旧版本、数据库/对象卷、旧知识快照保留。健康接口三个依赖 healthy，三台 Agent 在线。
- 词法与云端真实混合检索：12 个正例 Recall@3=1.0、MRR@3=1.0；3 个无答案例均返回空。混合查询最长 2.618 秒。原始逐例结果 `output/cloud-sre-20260919/expanded-rag-hybrid.json`。样本小且为开发集，不外推盲测效果。
- 新严格基础链路 ReAct 诊断 `insight_33b3eaf7b0ec44cd83fbd06848654a9a` 通过：3/3 工具完成、13 Evidence、13 Artifact，所有任务有有效产物，真实模型规划和混合检索出现，故障清理确认成功。报告仍为一份 INSUFFICIENT_EVIDENCE、两份 PARTIAL_WITHOUT_COUNTER；没有 VERIFIED，没有修复闭环。本批未重跑 LATS 配对实验。
- 本地定向测试 33 passed、2 skipped；跳过项依赖本机 Chroma SDK，云端实际索引/查询另已通过。编译检查通过。首次测试因脚本包导入失败而未执行，修正后重新通过。
- 发布前两次评测因入口脚本降权导致结果文件写权限错误，分别保存在 131700Z、132000Z 日志中，未切换线上版本；第三次仅在一次性评测容器覆盖入口后通过。
- 可复刻源码包 `source-final.tgz`：308 文件，SHA-256 `74b9b3149fbf2fce859afe1419c81333615cffed26b3b3406b705fcce6b8ce6a`。私有 Compose 不在源码包内。

## 本批实现

- 知识目录由 9 条、7 份 Markdown、9 块扩为 19 条、17 份 Markdown、39 块。新增内容是项目编写的调查指南，标注官方来源或项目协议、观察要求与适用边界，没有复制整篇上游文档。
- 增加 `benchmarks/retrieval/sre_queries.json` 的 12 条正例、3 条无答案开发查询，`scripts/evaluate_sre_retrieval.py` 同时支持 BM25 与实际混合后端。保留语料/查询 hash、逐例排名、实际后端、降级原因和耗时。这是随知识编写的开发集，非独立测试集，不能当事故准确率。
- 收紧 `verify_local_sre.py`：所有工具成功、每个任务有完整性通过的非空产物，并存在门禁接受的证据才通过采集链。以前任一工具成功就可能通过，容易掩盖部分失败。
- 增加受限的云端 Python 采样口径实验，只有隔离演示进程，故障自动到期且 finally 清理；结果不进入 Agent 证据表。

## Python 归因异常的实际调查

8 秒、99Hz、同一 source-hotspot 故障，两种采集模式顺序执行：

| 模式 | 样本数 | 观察 |
|---|---:|---|
| 默认 nonblocking | 2373 | `_sample` 33.3%，指标线程 sleep 行 31.7%，源码热点各行合计约 33.3% |
| nonblocking + gil | 704 | `source_hot_function` 各行合计约 99.4% |

py-spy 版本 0.4.2。采样线程名称含容器 TID 7、8、15；宿主对应 TID 是 906552、906553、906560。上游 `python_spy.rs` 使用 OS TID 查活动状态，查不到默认 active，再使用空闲启发式。因此**PID namespace 下线程身份映射是有直接证据支持的优先排查方向**，还未用带插桩的上游构建证明完整内部执行路径。

原始文件在云端 `/opt/mini-drop-releases/20260919T131700Z/sampling-experiment/`；本地摘要在 `output/cloud-sre-20260919/sampling-mode-experiment.json` 与 `thread-namespaces.json`。故障已撤销。这是采样器实验，不是 ReAct/LATS 比较或 Agent VERIFIED 根因。

下一修复应支持明确的 Python 采样模式和 namespace 能力探测：解释器 CPU 可以选择 GIL 模式，释放 GIL 的原生扩展需 native/perf 补充；观察语义要进入 Artifact/Evidence 元数据。不能直接把所有 py-spy 任务全局加 `--gil`，也不能按已知真值过滤后台函数。

参考：[py-spy 行为说明](https://github.com/benfred/py-spy)、[上游活动线程判定](https://github.com/benfred/py-spy/blob/v0.4.2/src/python_spy.rs)。已核对部署对应的 v0.4.2 标签源码；尚未对运行二进制做带插桩的内部路径验证。

## 后续实施顺序与验收

| 优先级 | 交付 | 通过条件 |
|---|---|---|
| P0 | Python 采集模式与目标身份 | CPU 忙线程/睡眠线程/释放 GIL 扩展/容器 PID 四类回归；语义记录完整；错误目标拒绝 |
| P0 | 基础链路持续回归 | 发现→绑定→规划→工具→Artifact→Analyzer→Evidence→Report→页面，每一步独立状态；故障/超时保留原始失败 |
| P1 | 缺失证据驱动的下一动作 | 按 Claim 的具体缺口请求独立进程压力或对照；没有可用工具就报告缺口，禁止强造反证或放宽覆盖率 |
| P1 | 真正同负载修复案例 | 选择一个独立样例服务，固定输入/并发/到达率/预热/时长；保存变更与回滚 ID；比较吞吐、P95、错误率、CPU，撤销注入不算修复 |
| P1 | ReAct/LATS 配对实验 | Python CPU、Go CPU、JVM GC、依赖延迟、无故障负例，各策略每场至少 5 次；模型/预算/知识快照相同、顺序交替、真值隔离；逐例报告正确、误报、弃权、耗时和用量；样本不足不宣布胜者 |
| P2 | 检索盲测与记忆评测 | 增加未参与文档编写的改写问题、跨领域干扰和过期版本；做 BM25/混合/无 RAG 消融，确认知识能指导正确工具而非只命中文档 |
| P2 | 运维系统验收 | 隔离环境测试运行中 Worker 重启、Agent 隧道断连、模型/Chroma 超时、独立备份恢复；阶段性 1h/24h soak，记录内存、磁盘、队列和重复任务；测得结果后再声明容量 |

保留现有默认 LATS；没有实验结果前不把策略切换或复杂度增加当作质量提升。可组合“外层候选假设选择、内层观察行动循环”，但线上现场无法回滚，不能宣称完全等价于可复位环境里的 MCTS。

## 开源借鉴与真实集成边界

| 项目 | 已采用/参考 | 下一步 | 尚未做到 |
|---|---|---|---|
| LangChain/LangGraph | 实际运行依赖，工具循环及 PostgreSQL checkpoint | 沿用已有 Runtime 加验收，避免新增平行状态机 | 框架不保证根因正确 |
| Chroma | 已部署实际向量库，Qwen Embedding/Reranker 与 BM25/RRF | 不可变知识快照及质量门禁 | 无多节点容量成绩 |
| [HolmesGPT](https://github.com/HolmesGPT/holmesgpt) | 调研过工具循环、重复调用治理、大结果控制；现有实现是机制借鉴 | 可做隔离咨询对照；只读输入及结果来源保持可追踪 | 未启动 HolmesGPT Agent，也未直接使用其全套 toolsets |
| [Redis SRE Agent](https://github.com/redis-applied-ai/redis-sre-agent) | 之前核对过用户/资产记忆分离设计，当前历史报告召回是本项目实现 | 评估过期事故与失败经验的效益 | 未引入 Redis 记忆服务或复制其实现 |
| [AIOpsLab](https://github.com/microsoft/AIOpsLab) | 本批参考故障、负载、观察、评测分离 | 将 Mini-Drop 接口做成独立 Agent 适配器后，在额外实验环境跑其适配场景 | 未运行它的基准；现有三台主机不等价于其 Kubernetes 实验环境 |
| [LATS 官方实现](https://github.com/andyz245/LanguageAgentTreeSearch) | 假设搜索机制参照 | 共享 Harness 下与强 ReAct 基线比较 | 未证明线上更准确或更省成本 |

之前逐文件调研与固定源码快照见 [开源调研记录](performance-sre-agent-research-20260919.md)。不能将“阅读过源码”“自己实现相同机制”和“直接对接运行”写成同一件事。
