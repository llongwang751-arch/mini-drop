# 规划四态与无答案检索的新冻结评估

实现来源同时固定原始源码字节和 canonical AST 摘要。AST 递归保留节点类型、值与非空字段，只省略解释器版本新增的空列表字段；不使用各版本格式不同的 ast.dump。非空参数、装饰器和表达式变化仍拒绝。此规则在真实首轮零调用时固定，不能事后修改来源合同。

本轮使用新的 24 题和新的评分合同，评价正常、缺测、拒绝与可执行调查的规划结构，以及知识库是否应当召回内容。原 24 题的结构 15/24、判断 11/24、无答案误召回 3/4 和两次超时保持原样。

## 公共生产合同

生产单一入口是 `server/app/agent_runtime/planning_output.py` 的 `validate_planning_output`，传统规划和 LangGraph 都复用它。`schema_version` 为 `mini-drop.planning-output.v2`，`disposition` 分为：

| 结果 | 合法结构 | 边界 |
|---|---|---|
| INVESTIGATE | 白名单工具、1–3 个可证伪假设及非空支持/反证条件 | 只是下一步取证规划，仍要经过目标、审批、预算与 Evidence 门禁 |
| NORMAL | `tool_name=null`、`hypotheses=[]`、非空范围说明 | 仅描述输入中的已检查范围；模型规划不产生真实健康检查结果 |
| INSUFFICIENT_EVIDENCE | 无工具、无假设、非空缺失观测说明 | 缺失不能补零，不能包装成正常；能安全补采时仍可提出调查 |
| REFUSED | 无工具、无假设、非空权限/能力范围说明 | 与缺测区分，不通过虚构异常让拒绝结果满足旧结构 |

所有结果 `causal_root_cause_verified=false`。知识没有命中也不表示服务正常，它只表示没有相关排查先验。检索可用性 `HEALTHY/DEGRADED` 与业务健康分列。

无答案修复采用 `knowledge-subject-admission-v2`。主能力由公开 catalog 的 title、keywords 和 applies_to 声明，summary 中的备选诊断不能扩大覆盖范围；查询中的具名主体须由当前候选条目的公开主能力覆盖，不能因为另一条目提及该主体而获得准入，明确排除的技术替代对象不能借作锚点。无品牌黑名单或评测 case ID 规则；新增主体/能力可通过新 catalog 条目扩展。CPU、TCP 等日常同名词需技术语境，未知应用同时出现独立进程 CPU/RSS 观测时仍可保留通用 Linux 指南。BM25、dense、RRF、rerank 和降级路线共用准入判定，排序分数不等于事实置信度。

## 先冻结再调用

公共题、评分真值、规则与预算在评估器实现和任何供应商调用前，于 **2026-10-02 08:07:24 UTC** 固定。此前已协商公共 schema 接口且生产模块文件已存在，因此不声称在全部生产实现前冻结。题目由维护者编写，不称第三方独立作者。

- [公共问题](../benchmarks/retrieval/planning_retrieval_v2_public.json)：24 个不同的新合成题，仅含问题、运行时和合成观察。
- [私有评分真值](../benchmarks/retrieval/planning_retrieval_v2_private.json)：只用于评分，不进入模型或检索；文件“私有”指评估角色隔离，不代表 Git 访问权限。
- [冻结规则](../benchmarks/retrieval/planning_retrieval_v2_manifest.json)：manifest SHA `ae57be9a369db8df7c10131810f4deaa68bf2ea91104911233a6ad67c8f58d4e`。
- 16 个有相关知识题、8 个无答案题；8 个调查、8 个正常/有界反证、4 个缺测、4 个拒绝。
- 与此前 15 个开发问题和 24 个保留问题，经 NFKC、大小写、去标点归一化后交集为零。

当前 corpus 按预注册 `LF_TEXT` 合同固定。离线 BM25 在自有临时目录恢复规范换行字节，跨 Windows/Git/Linux 复算不改变 chunk SHA。公共题/私有真值/manifest 本身按原字节校验，不归一化或重写。

## 真实模型范围与预算

[评估器](../scripts/evaluate_planning_retrieval_v2.py)使用生产 `SYSTEM_PROMPT`、数值判据要求、公共 `planning_output_schema` 和公共 validator。JSON 输出适配只使模型直接返回该 DTO，不调用实际 function tool。它不运行 LangGraph/LATS、Skill、Task、采集或 Evidence Gate，不能计为完整 Agent 成功率或现场因果准确率。

24 次真实 chat 调用、每题一次、最多 2 路并发、读取超时 40 秒、输出上限 1200 tokens、温度 0.1、零重试。超时/错误计入全部 24 题的模型分母。使用上线环境已有鉴权与供应商传输；密钥不进入请求存档、命令或报告。调用前核对上线提示与各运行时公共 schema，不一致便在消耗 chat 预算前拒绝。

先把完整公共请求和原始响应落盘，全部请求结束后才解析私有真值。分别统计结构、disposition、下一工具/null；不因判定准确而将结构失败改为成功。没有固定价格证据时费用为 null，缺失 usage 保持未知。

## 检索分开统计

BM25 只读调用当前生产检索函数，不用评分真值改写查询。HYBRID 独立在已部署容器进行只读查询，原问题同时作为 relevance query；不创建索引、改配置文件或业务数据。临时评估进程选择 hybrid，实际 DENSE/RERANK/fallback、降级原因与空结果保留。Embedding/Rerank 调用不是 chat 调用，其 token 使用和价格未取得时明确未知。

Recall@3 与 MRR@3 分母为 16 个有答案题；无答案误召回分母为 8 个无答案题。BM25 和实际 HYBRID 分开统计，不把降级 BM25 叫作向量路线通过。小样本结果不能推广为生产准确率，也不与旧题作相同题目的成对效果提升。

```powershell
# 只核验冻结，不调用模型
python -B scripts/evaluate_planning_retrieval_v2.py --check-freeze

# 已完成首轮必须从归档的原始源码复核；不重新请求供应商
python -B reports/quality/planning-retrieval-v2-20261002/evaluation/original-source/scripts/evaluate_planning_retrieval_v2.py --verify-report reports/quality/planning-retrieval-v2-20261002/evaluation/first-run/report.json
```

首轮实评已经完成，原始目录不可覆盖。新的模型实评必须建立新的题目、冻结合同和调用批次；下面的结果不由单元测试推断。

## 首轮真实结果与后续回归边界

精确源码 `df0d3ef00a945e7d909f73868819799b2b7cc7f7` 在发布 `20261002T085733Z` 后完成一次实评：24 次真实 chat、21 次 HTTP 200、3 次 ReadTimeout，零重试。21 份成功响应均通过共享生产 parser，结构、disposition、下一工具分别为 21/24；结果分布为 NORMAL 8、INSUFFICIENT_EVIDENCE 4、REFUSED 4、INVESTIGATE 5。超时没有可判断的内容，保持在分母中，不能说成合同错误或删去。已知 usage 为 90,583 tokens，另外 3 次 usage 和所有价格未知。

| 检索路线 | Recall@3（16 有答案题） | MRR@3 | 无答案误召回（8 题） |
|---|---:|---:|---:|
| BM25 | 0.96875 | 0.875 | 5/8 |
| 实际 HYBRID | 0.90625 | 0.9375 | 1/8 |

HYBRID 实际 21 次经过 BM25/entity/Chroma/RRF/Rerank，3 次无候选止于 RRF；全部无降级原因。原始 24 请求/响应、24 条 HYBRID trace、原始语料/chunk SHA、254 个精确 Git 文件和独立复算已归档于 `reports/quality/planning-retrieval-v2-20261002/evaluation/`。首轮结果不会随修复改写；后续使用同题只能称 `REGRESSION_ON_EXPOSED_V2_QUESTIONS_NOT_NEW_BLIND`，只运行检索、不增加本次 24 次模型预算。旧源码已归档，首轮复核从该版本执行，不能假称与更新后的生产源码相同。

实际 LangGraph 只读规划烟测首批 2/3：缺测、拒绝输出合法，无新工具、采集任务或 Evidence；NORMAL 请求遭供应商 OpenAITimeoutError，没有伪造 NORMAL。真实浏览器已检查两张输出卡和无答案提示、四种宽度，无 console/network 错误；没有本轮证据下载。后续正常分支补验必须保留这个首批结果并单列新批次。检查点隔离另外由真实 LangGraph 测试证实，应使用带版本的 thread_id，顶层 checkpoint_ns 可以为空。

## 已曝光问题回归与最终部署

新冻结 24 题首轮仍为 df0d3ef0 上的 21 响应/3 超时，结构、判断和下一工具均 21/24，零重试；首轮 BM25 Recall@3 0.96875、无答案误召回 5/8；HYBRID Recall@3 0.90625、误召回 1/8。a6a36260 的同题已曝光检索回归中，两路无答案误召回均 0/8，两路 Recall@3 均 0.875，BM25 MRR@3 0.90625、HYBRID MRR@3 0.875；主体准入减少误召回也损失相关内容覆盖。这不是新的盲测。最终 73b4b18a 的知识语料与三个检索实现文件与 a6a36260 Git tree 相同，由发布 manifest 证明来源等价，没有重复消耗 chat 或检索实评预算。

当前已部署 `20261002T103825Z`，应用源码 `73b4b18ad83553a5012a0025dfb78bb21ff8dc7f`。本次仅更换 Diagnosis Worker 和 Analyzer；Web 保留 a6a36260/20261002T095001Z 的运行镜像，完整 Web Git tree 与最终源码相等，58 个公网资源 SHA 一致；Worker/Analyzer 各 219 个源码文件逐一核对，13 服务健康，其余 11 个容器及紧邻部署前 Office/API/Native/CPP、环境和挂载保持。故障广场 21 场景均 inactive，展示工程判断 21/21、路径 6/21、反证 8 条；没有重跑故障或一小时实验。

精确源码 [主 CI 36996499159](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499159) 实际 14/14，Python 2019 通过/16 登记跳过，Web 330、真实 PostgreSQL 14 零跳过、Chromium 固定数据 8；Chroma 独立作业通过。[干净核心 CI 36996499257](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499257) 87 项门禁零跳过、10 阶段通过。原失败和原断言保持，专项数量不重复加到主套件。

Java 正常 CPU 缺少明确进程语境、同主机块设备竞争同义表达，以及缺少主能力 I/O 锚点的低延迟同步写反证，仍存在覆盖缺口。没有按私有真值添加 case ID 或品牌特例；后续新增公共能力后要用另一批未曝光问题验证。

实际 NORMAL 首批超时、第二批非法 INVESTIGATE 后纠正超时均保留。旧提示存在无条件取证的冲突已修复；这不能证明任一次供应商超时的具体原因。

v7 提示部署后的同一 NORMAL 问题新会话补验 1 次，结果未通过；原始状态 `FAILED`，预期 NORMAL，实际 `INSUFFICIENT_EVIDENCE`，实际持久采集任务 0。本次返回合法 INSUFFICIENT_EVIDENCE 并直接 finish：只有进程身份、没有性能基线或时间窗口，且用户要求不采集。没有供应商超时、检索循环、非法计划或语义重试；合法停止合同已实证，但该问题的 NORMAL 标签未通过。当前浏览器实际呈现 2/3 张规划结果卡、3 个无答案通知，24 次四视口布局检查通过，TLS 证书校验开启，无 console/network 错误，本次没有证据下载。缺测与拒绝卡的产生源码是 df0d3ef0；新 NORMAL 会话的产生源码是 73b4b18a，逐例来源分开保存，不宣称三个状态都在最终版本重新实跑。

补验使用 ASSISTED R0、最多一轮，规划预算 60 秒，单次模型调用上限 min(45 秒, 剩余预算)、SDK 零重试；会话总预算 120 秒不代表规划预算。一个 planner invocation 可能有多个模型节点，底层 HTTP 调用数未取得时保持 null。

[检索独立复算](../reports/quality/planning-retrieval-v2-20261002/retrieval-regression/independent-audit.json)，[真实规划与浏览器](../reports/quality/planning-retrieval-v2-20261002/live/post-prompt/manifest.json)。

## 旧报告的历史复算

新的生产 AST 和提示已发生变化，旧评估器直接核验当前源码应当拒绝；不能放宽原门禁来伪装版本相同。[历史复算入口](../scripts/verify_historical_heldout.py)只在自有临时目录恢复原 26 个 SHA 固定输入、原 BM25、corpus 和 prompt；不替换仓库源码，不调用供应商。原 17 个评分/提示/解析/供应商节点、原问题真值和原报告字节保持。

```powershell
python -B scripts/verify_historical_heldout.py reports/quality/interview-release-20261002/heldout/first-run/report.json
python -m pytest tests/test_heldout_diagnosis.py tests/test_historical_heldout.py tests/test_planning_retrieval_v2.py -q --basetemp=output/acceptance/planning-retrieval-v2-20261002/pytest-unique
```

58 个原门禁继续执行全部原断言，只把测试 fixture 的来源显式限定为历史沙箱；新生产行为由 v2 门禁和实际部署验收另证。

首个 CI 在 Python 3.11 发现历史 Recall 均值末位为 `0.7583333333333334`，原 Python 3.14 报告为 `0.7583333333333333`；所有 24 个逐题判断、请求与排名完全相同。Python 3.12 改进了浮点 `sum` 算法，见[官方说明](https://docs.python.org/3.12/library/functions.html#sum)。历史沙箱显式重放该原报告有界浮点项的高精度求和，整数/布尔计数保持整数运算；原 17 个评分节点和原报告字节保持，仍严格相等比较，连一 ULP 篡改也拒绝。新 v2 在任何真实调用前固定 `math.fsum` 求浮点均值，避免解释器版本改变末位；不修改问题、真值、分母或评分规则。
