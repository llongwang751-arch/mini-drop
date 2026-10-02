# Agent/RAG 新题冻结评估

> 本文对应首次历史实验。当前生产四态输出与统一相关性门禁另见 [v2 合同](PLANNING_RETRIEVAL_V2.md)。原报告使用 `python -B scripts/verify_historical_heldout.py reports/quality/interview-release-20261002/heldout/first-run/report.json` 隔离复算，不重新调用模型。当前生产已变，原 CLI 直接验证当前源码会拒绝漂移；下文旧执行命令保留作当时记录。

这是离线模型规划与公共知识检索评估。模型调用真实供应商，检索实际调用现有 BM25；不创建 Diagnosis/Task、不注入故障、不修改线上业务或索引，也不评价云端根因准确率。

## 冻结范围

24 个新合成题在评估器实现前冻结，覆盖同义描述、正常检查、缺测、复合异常、误导指令、支持与反证。题目由项目维护者编写，不声称第三方独立出题。与既有 15 题开发集经过 NFKC、大小写及去标点归一化比较，交集为零。已有 540/500 条确定性回放另按自己的合同统计，不能与这 24 个真实模型请求相加为新实诊断成绩。

- [公共题目](../benchmarks/retrieval/heldout_20261002_public.json)：只有问题、运行时及合成观测。
- [评分真值](../benchmarks/retrieval/heldout_20261002_private.json)：相关知识 ID、可接受分类/判断/下一工具，仅供评分。
- [冻结合同](../benchmarks/retrieval/heldout_20261002_manifest.json)：题目/真值/开发集、语料及生产源码 SHA，预算与后端。
- [原输入包](../benchmarks/retrieval/heldout_20261002_frozen_inputs.json)：只包装上述合同已登记的 26 份原始输入字节，不含私有题目真值、环境变量或线上配置。
- [评估器](../scripts/evaluate_heldout_diagnosis.py)：校验冻结字节、构造公共请求、调用模型、保存原输出，然后读取真值评分。
- [负向门禁](../tests/test_heldout_diagnosis.py)：真值泄漏、漂移、非法工具、未知 usage、重复记录、重新签 SHA 后篡改排名/来源/分数等反例。

冻结源码来源为 `5cd4a1c4879df2f32bf37caea5c06635d167ab3f`，当时线上应用为 `de094fff754f2d8cb7139dd99e31c7a30d5fb754`。工作树教学注释及换行按实际字节独立固定；真实调用前另核对容器内 7 份规划/Provider/检索相关源码与应用提交的 Git blob SHA。不能把工作树 SHA 或这 7 份核对扩大为整个线上新版本发布。

冻结 manifest SHA：`d6fde80a176a268ef9c13cd6ca5ddce2a8d33755769b0dc2a00ab6f6ce15e0c2`。预实现回执在 `output/acceptance/heldout-20261002/pre-implementation-freeze.json`。17 篇白名单文档、19 条目录、39 个块；本轮不补语料、不改排名阈值或问题/真值来追求通过。新一轮问题或方法应使用新的冻结版本，保留本轮失败。

## 模型与统计口径

生产 `adaptive_planner.SYSTEM_PROMPT` 和性能规划要求保持原样，追加统一的 JSON 输出适配器，读出分类、合成观测判断、下一工具及简短说明。适配器在第一轮调用前固定，24 题使用相同适配器，没有按题预填类别、规则基线或评分答案。

这个适配器没有运行 LangGraph/LATS、Skill、审批、原生采集、Evidence Gate 或报告闭环，所以结果仅评价模型规划输出；`tool_choice_allowlisted` 是选择合法性，不是真实工具执行成功率。`OBSERVATION_SUPPORTED/REFUTED` 是对给定合成窗口的判断，不是平台对真实 Artifact 的验收。

真实线上聊天配置为 SiliconFlow / `deepseek-ai/DeepSeek-V3.2`，沿用当前 `enable_thinking=false`。只读现有配置，凭据仅留在容器内存；SSH 验证现有主机密钥，Provider HTTPS 使用现有客户端默认 TLS 校验，不关闭证书验证。评估不打印或归档密钥。

预注册预算是 24 次、每题一次、最多两路并发、读取超时 40 秒、输出上限 1200 tokens，零重试。连接超时沿用现有客户端配置；这不是全请求硬实时截止保证。请求超时与 API 错误计入所有 24 题的模型指标分母，不能排除失败后再计算成功率。检索实际后端固定为 BM25，Top-K 为 3；没有测试云端 Chroma/Embedding/Reranker 的效果或成本。

检索 recall@3 与 MRR@3只在 20 个有相关知识题上计算；无答案 FPR 分母为 4 题，包含没有专用知识条目的 Java 锁题、队列题、缺测及非诊断问题。相关 ID 是预注册的文档相关性判断，不是事故真值。小样本均值不能推广为生产质量。

分类、判断、结构和工具预期命中分别统计。只有供应商响应包含有效且自洽的 usage 才累加 token；缺失保持未知，不能补零。没有固定价格证据，`cost_usd=null`。

## 运行与独立复核

在已有可信供应商环境中：

```powershell
python -B scripts/evaluate_heldout_diagnosis.py --provider ssh-current --output output/acceptance/heldout-20261002/first-run
python -B scripts/evaluate_heldout_diagnosis.py --verify-report output/acceptance/heldout-20261002/first-run/report.json
python -m pytest tests/test_heldout_diagnosis.py -q
```

输出目录必须不存在，不覆盖旧证据。以上 `first-run` 已存在，不能再次用该目录发请求；本轮已用尽 24 次预注册调用预算，不重试这些题。`ssh-current` 复用当前环境已有的 `output/acceptance/deployment-20260930/run_strict.py` SSH 配置，通过只读 stdin 运行模型请求；没有将该文件中的旧场景真值带入检索或模型。普通复刻可在私有进程环境提供现有模型配置，使用 `--provider local`，不得把密钥写入命令、仓库或公开报告。

24 份请求、24 份响应记录及两份来源回执全部 SHA 固定。复核重新从冻结公共题调用实际检索、重建出站 payload、核对原始响应解析、最后依据冻结真值重算成绩；只提交高分摘要而没有原始记录会被拒绝。单元测试生成的虚构响应仅用于门禁测试，绝不归档为真实模型结果。

## 本轮实跑

首轮输出保留在 `output/acceptance/heldout-20261002/first-run/`，24 次调用中 22 次 HTTP 200、2 次 ReadTimeout、API 错误 0，无重试。以下分母和评分均按第一轮冻结规则，未剔除失败：

| 指标 | 第一轮结果 |
|---|---|
| BM25 recall@3，20 个相关题 | 75.83% |
| BM25 MRR@3，20 个相关题 | 0.80 |
| 无答案题仍召回内容 | 3/4，75% |
| 完整输出结构 | 15/24 |
| 分类符合冻结真值 | 15/24 |
| 合成窗口判断符合冻结真值 | 11/24 |
| 完整结构下工具选择合法 | 15/24 |
| 完整结构下下一工具符合冻结预期 | 13/24 |
| 实际工具执行、故障注入 | 0、0 |
| 22 个有 usage 响应 token | 输入 72,728；输出 3,303；合计 76,031 |
| 两个超时请求的 token、全部调用价格 | 未知；cost=null |

重要失败：h06/h07 超时；h14/h18/h24 无相关知识仍召回，h24 的和声问题召回了 Agent/容器知识；h02/h09/h11/h13 在已有有界测量时仍给证据不足；h05 选择无需再采、h13 选择内存剖析，与预注册下一工具预期不一致。

正常、缺测或拒绝类 h17/h18/h20/h21/h22/h23/h24 的条件数组为空，不满足适配器的非空结构合同，因此结构和关联模型指标均计失败。只读补充审计发现全部 22 个成功响应选择都在允许范围内、因果标志均 false；这是对原始 JSON 的解释，不替代预注册的 15/24 合法性成绩，也不证明任何工具被真实执行。后续结构设计应分别处理异常假设与正常/拒绝结果；不能改本轮真值或结构规则把失败改成通过。

首轮完成后发现工作树教学注释及 Windows CRLF 与 clean Git 字节不同。为跨平台复算，新增原输入包只保存原 manifest SHA 对应的原字节；当前 Python 源必须与冻结源完整 AST 等价，语料与开发集仅接受 CRLF/LF 等价。实际检索在全新拥有的临时目录重放原语料字节，从而保持 chunk/content SHA，不覆盖当前源码或知识库。

原首轮评估器字节保留为 `first-run/evaluator-source.py`，SHA `89f1b4abeba669f564974126b5d946de6e36663e8bf019f37dae8cb757bc5d99`；首轮报告未重写，SHA `c0940e9dd2e70ceccc313c2fd6359b701c0cc710b00fc4480237d8fe0cf3a6f9`。新验证器要求原评估器 SHA 正确，且原/新 17 个评分、解析、提示、分类与 Provider 执行节点 AST 相同后才复算旧报告。58 项门禁回归（含负向用例）通过；独立审计在新临时目录使用 clean Git 的 server/knowledge/benchmarks，再叠加新评估器和冻结输入，真实重算所有指标与第一轮完全一致，50 份来源/请求/响应 pins 保持。审计记录为 `output/acceptance/interview-release-20261002/heldout-independent-review/audit.json`。顶部 schema、范围、作者独立性、适配器、实际后端、知识非 Evidence 与未知费用均另设固定门禁，不能只保持数值就把报告改称云端因果准确率。

这些结果完成了“独立于开发集、先冻结、有真实模型和原始失败”的评估流程，揭示了检索拒答与输出适配的实际不足；不能称为已达成高质量 Agent 盲测或生产准确率。
