# 规划四态与无答案检索的新冻结评估

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

# 新目录才可执行；first-run 存在后不可重复请求
python -B scripts/evaluate_planning_retrieval_v2.py --provider ssh-current --output output/acceptance/planning-retrieval-v2-20261002/first-run
python -B scripts/evaluate_planning_retrieval_v2.py --verify-report output/acceptance/planning-retrieval-v2-20261002/first-run/report.json

# 可在 clean checkout 用 --remote-provider-helper 显式指向既有可信 SSH helper
python -B scripts/evaluate_planning_retrieval_v2.py --retrieval-only --backend HYBRID --output output/acceptance/planning-retrieval-v2-20261002/hybrid-first-run
```

当前出题与代码门禁完成；真实执行状态必须以新目录原始报告为准，不能从单元测试虚构供应商成绩。正式发布报告与项目上下文登记实际结果。

## 旧报告的历史复算

新的生产 AST 和提示已发生变化，旧评估器直接核验当前源码应当拒绝；不能放宽原门禁来伪装版本相同。[历史复算入口](../scripts/verify_historical_heldout.py)只在自有临时目录恢复原 26 个 SHA 固定输入、原 BM25、corpus 和 prompt；不替换仓库源码，不调用供应商。原 17 个评分/提示/解析/供应商节点、原问题真值和原报告字节保持。

```powershell
python -B scripts/verify_historical_heldout.py reports/quality/interview-release-20261002/heldout/first-run/report.json
python -m pytest tests/test_heldout_diagnosis.py tests/test_historical_heldout.py tests/test_planning_retrieval_v2.py -q --basetemp=output/acceptance/planning-retrieval-v2-20261002/pytest-unique
```

58 个原门禁继续执行全部原断言，只把测试 fixture 的来源显式限定为历史沙箱；新生产行为由 v2 门禁和实际部署验收另证。

首个 CI 在 Python 3.11 发现历史 Recall 均值末位为 `0.7583333333333334`，原 Python 3.14 报告为 `0.7583333333333333`；所有 24 个逐题判断、请求与排名完全相同。Python 3.12 改进了浮点 `sum` 算法，见[官方说明](https://docs.python.org/3.12/library/functions.html#sum)。历史沙箱显式重放该原报告有界浮点项的高精度求和，整数/布尔计数保持整数运算；原 17 个评分节点和原报告字节保持，仍严格相等比较，连一 ULP 篡改也拒绝。新 v2 在任何真实调用前固定 `math.fsum` 求浮点均值，避免解释器版本改变末位；不修改问题、真值、分母或评分规则。
