# 四态规划与无答案检索优化交付（2026-10-02）

当前已部署 `20261002T103825Z`，应用源码 `73b4b18ad83553a5012a0025dfb78bb21ff8dc7f`。本次仅更换 Diagnosis Worker 和 Analyzer；Web 保留 a6a36260/20261002T095001Z 的运行镜像，完整 Web Git tree 与最终源码相等，58 个公网资源 SHA 一致；Worker/Analyzer 各 219 个源码文件逐一核对，13 服务健康，其余 11 个容器及紧邻部署前 Office/API/Native/CPP、环境和挂载保持。故障广场 21 场景均 inactive，展示工程判断 21/21、路径 6/21、反证 8 条；没有重跑故障或一小时实验。

精确源码 [主 CI 36996499159](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499159) 实际 14/14，Python 2019 通过/16 登记跳过，Web 330、真实 PostgreSQL 14 零跳过、Chromium 固定数据 8；Chroma 独立作业通过。[干净核心 CI 36996499257](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499257) 87 项门禁零跳过、10 阶段通过。原失败和原断言保持，专项数量不重复加到主套件。

## 生产行为

规划输出已统一为 INVESTIGATE / NORMAL / INSUFFICIENT_EVIDENCE / REFUSED。合法非调查结果空假设、空工具，持久化 planner.output_recorded，不新建采集任务；NORMAL 只是输入或规划范围内未提出异常，真实体检仍必须使用采集后的健康判据。NO_RELEVANT_KNOWLEDGE 表示未找到相关知识，不表示业务正常。提示先选四态，只有仍有可验证异常与可执行动作的 INVESTIGATE 才必须扩展假设和数值证伪；目标、权限、预算、数值、采集失败与证据门禁不降低。检查点使用 diagnosis-agent-v7-four-state-prompts:<diagnosis_id>，scope-agent-v1:<diagnosis_id>；旧 v6 和原 ID 检查点保留，SQL 业务 ID 不改变。

四态共享生产 validator，LangGraph finish_diagnosis_plan 与 legacy 都保持兼容。planner.output_recorded 明确 is_evidence=false、health_check_performed=false、causal_root_cause_verified=false。已有证据、任务、取消状态与终态不因非调查输出被清空。

检索按实际问题主体与当前候选公开主能力准入；分类器猜测和其他catalog条目不能授权候选。title/keywords/applies_to 声明能力，summary/body 用于排序。ACL、来源与chunk SHA仍执行，降级路线复用准入；NO_RELEVANT_KNOWLEDGE 和 RETRIEVAL_ONLY 可用性分别显示。

## 效果与限制

新冻结 24 题首轮仍为 df0d3ef0 上的 21 响应/3 超时，结构、判断和下一工具均 21/24，零重试；首轮 BM25 Recall@3 0.96875、无答案误召回 5/8；HYBRID Recall@3 0.90625、误召回 1/8。a6a36260 的同题已曝光检索回归中，两路无答案误召回均 0/8，两路 Recall@3 均 0.875，BM25 MRR@3 0.90625、HYBRID MRR@3 0.875；主体准入减少误召回也损失相关内容覆盖。这不是新的盲测。最终 73b4b18a 的知识语料与三个检索实现文件与 a6a36260 Git tree 相同，由发布 manifest 证明来源等价，没有重复消耗 chat 或检索实评预算。

v7 提示部署后的同一 NORMAL 问题新会话补验 1 次，结果未通过；原始状态 `FAILED`，预期 NORMAL，实际 `INSUFFICIENT_EVIDENCE`，实际持久采集任务 0。本次返回合法 INSUFFICIENT_EVIDENCE 并直接 finish：只有进程身份、没有性能基线或时间窗口，且用户要求不采集。没有供应商超时、检索循环、非法计划或语义重试；合法停止合同已实证，但该问题的 NORMAL 标签未通过。当前浏览器实际呈现 2/3 张规划结果卡、3 个无答案通知，24 次四视口布局检查通过，TLS 证书校验开启，无 console/network 错误，本次没有证据下载。缺测与拒绝卡的产生源码是 df0d3ef0；新 NORMAL 会话的产生源码是 73b4b18a，逐例来源分开保存，不宣称三个状态都在最终版本重新实跑。

首轮模型使用真实生产提示/schema/parser的JSON适配，不执行平台LangGraph/Task/Evidence。真实Agent补验单列；第一次NORMAL超时，第二次错误计划被门禁拒绝后纠正超时，最终一次结果由原始摘要决定。旧提示冲突的修复不等于证明超时根因。

## 失败与来源保留

首次Python3.11历史Recall末位差异、旧页面5秒超时、一次原文案连续短语断言失败均保留。历史浮点仅在隔离旧源码固定原求和口径，所有逐题/报告严格比较；AST跨解释器摘要保留全部非空语法字段。Vitest并发降至2而原超时和断言不改。最终132项提示回归与Ruff通过。

应用源码73b4b18a已推送，后续提交仅固定文档/合同/原始证据。正式旧RC1标签和release不重写；旧评分文件、冻结题目/真值/规则、数据库卷、对象数据和用户教学注释保持。

## 证据入口

- [固定SHA当前事实](../../docs/CURRENT_DELIVERY.md)
- [主CI](../quality/planning-retrieval-v2-20261002/ci/prompt-boundaries/main-final-36996499159/summary.json)
- [干净核心](../quality/planning-retrieval-v2-20261002/ci/prompt-boundaries/clean-final-36996499257/summary.json)
- [最终发布](../quality/planning-retrieval-v2-20261002/deployment/prompt-final/archive-manifest.json)
- [首轮评估](../quality/planning-retrieval-v2-20261002/evaluation/archive-manifest.json)
- [已曝光检索回归](../quality/planning-retrieval-v2-20261002/retrieval-regression/archive-manifest.json)
- [真实规划与浏览器](../quality/planning-retrieval-v2-20261002/live/post-prompt/manifest.json)
- [本轮总清单](../quality/planning-retrieval-v2-20261002/manifest.json)

## 后续优先级

后续最有价值的是补公共知识能力与同义表达的覆盖，以新的未曝光问题重新冻结评估；供应商超时单列为可用性问题，不能把超时强制变成 NORMAL 或刷重试成功率。求职准备继续阅读真实源码、讲清测试判据和失败取舍，并完成本人五分钟彩排。
