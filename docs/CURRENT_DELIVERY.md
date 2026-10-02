# 当前面试交付事实

本页由 `contracts/interview_delivery.json` 和 `scripts/build_interview_delivery.py` 生成。
成绩分别说明工程判断、具体定位和反证；既有原始实验报告保留原结论。

| 项目 | 已验证的事实 |
|---|---|
| 工程诊断判断 | 21/21 |
| 具体异常路径 | 6/21 |
| 有效反证 | 8 条 |
| 有证据支持的观测 | 7 类，尚未认定具体异常路径 |
| 实验来源 | 最新 7 个窗口及此前 14 条真实记录 |
| 工程缺陷修复回归 | 4 个独立案例 |
| 发布版 CI | [14/14 作业](https://github.com/llongwang751-arch/mini-drop/actions/runs/36996499159) |
| 发布版 Python | 2019 通过、16 登记跳过；真实 PG 与 Chroma 由独立作业执行 |
| 发布版 Web / Chromium | 330 项 / 8 项；Chromium 为固定数据回归 |
| 真实 PostgreSQL | 14 项、0 跳过 |
| 线上浏览器 | 本次未执行证据下载；24 次布局检查通过 |
| 线上版本 | `20261002T103825Z`，源码 `73b4b18ad83553a5012a0025dfb78bb21ff8dc7f` |

性能实验中的工程判断包含支持观测和有效反证，不表示每类都定位了根因。
真实业务同负载修复案例是独立 HTTP/SQLite 样例；模型规划、知识检索与真实采集分别验收。

## 本轮交付收尾

- **历史 RC1：干净 Linux 核心平台复刻：PASSED**。从全新 Ubuntu 22.04 Runner、精确源码 34b74e7b 构建七个镜像；10 个阶段全部通过，真实 Agent→Task→S3 Artifact→Analyzer 与 SQL 身份绑定、HTTP 401、清理均验证。87 项门禁测试（含负向用例）零跳过；范围限核心平台及 sys_metrics。
  [原始记录](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/report.json)。
- **历史 RC1：新题规划与检索盲测：COMPLETED**。24 题预先冻结，真实模型零重试，22 响应/2 超时；结构与分类 15/24、判断 11/24、下一工具 13/24。BM25 Recall@3 75.83%、MRR 0.80、无答案误召回 3/4。部分指标命中，效果仍有缺口；干净 Git 独立复算一致，不计为现场因果准确率。
  [原始记录](../reports/quality/interview-release-20261002/heldout/independent-review-r2/audit.json)。
- **历史 RC1：文档与求职材料统一：COMPLETED**。六份当前设计与求职文档、既有本地简历段落和五分钟讲稿已统一，保留真实参与边界；个人简历未提交，用户本人学习和彩排仍须完成。
  [原始记录](../reports/quality/interview-release-20261002/materials/doc-final-review-public.json)。
- **历史 RC1：本轮源码主 CI：PASSED**。源码 34b74e7b 的官方 CI 36975451853 实际 14/14；Python 1827 通过/16 登记跳过、Web 317、真实 PostgreSQL 14 零跳过、Native CTest 5、Chromium 8 固定数据。此记录属于历史 RC1，不是当前线上应用源码的 CI。
  [原始记录](../reports/quality/interview-release-20261002/main-ci-34b74e7b/summary.json)。
- **当前源码干净 Linux 核心复刻：PASSED**。最终源码73b4b18a；87门禁/0跳过及10阶段通过，真实 Agent→Task→S3→Analyzer 与身份、mTLS和清理验证；范围限核心平台/sys_metrics，不含外部模型或Office。
  [原始记录](../reports/quality/planning-retrieval-v2-20261002/ci/prompt-boundaries/clean-final-36996499257/reports/clean-stack/report.json)。
- **v2新冻结24题首轮模型与检索：COMPLETED**。首轮源码df0d3ef0；21响应/3超时，结构/判断/下一工具均21/24，零重试；BM25 Recall0.96875/无答案5/8，HYBRID Recall0.90625/无答案1/8。仅规划DTO与检索，不是完整Agent或因果准确率。
  [原始记录](../reports/quality/planning-retrieval-v2-20261002/evaluation/receipts/model-independent-review-r3.json)。
- **已曝光v2问题无答案检索回归：COMPLETED**。新冻结 24 题首轮仍为 df0d3ef0 上的 21 响应/3 超时，结构、判断和下一工具均 21/24，零重试；首轮 BM25 Recall@3 0.96875、无答案误召回 5/8；HYBRID Recall@3 0.90625、误召回 1/8。a6a36260 的同题已曝光检索回归中，两路无答案误召回均 0/8，两路 Recall@3 均 0.875，BM25 MRR@3 0.90625、HYBRID MRR@3 0.875；主体准入减少误召回也损失相关内容覆盖。这不是新的盲测。最终 73b4b18a 的知识语料与三个检索实现文件与 a6a36260 Git tree 相同，由发布 manifest 证明来源等价，没有重复消耗 chat 或检索实评预算。
  [原始记录](../reports/quality/planning-retrieval-v2-20261002/retrieval-regression/independent-audit.json)。
- **最终提示后的真实NORMAL补验：FAILED**。v7 提示部署后的同一 NORMAL 问题新会话补验 1 次，结果未通过；原始状态 `FAILED`，预期 NORMAL，实际 `INSUFFICIENT_EVIDENCE`，实际持久采集任务 0。本次返回合法 INSUFFICIENT_EVIDENCE 并直接 finish：只有进程身份、没有性能基线或时间窗口，且用户要求不采集。没有供应商超时、检索循环、非法计划或语义重试；合法停止合同已实证，但该问题的 NORMAL 标签未通过。当前浏览器实际呈现 2/3 张规划结果卡、3 个无答案通知，24 次四视口布局检查通过，TLS 证书校验开启，无 console/network 错误，本次没有证据下载。缺测与拒绝卡的产生源码是 df0d3ef0；新 NORMAL 会话的产生源码是 73b4b18a，逐例来源分开保存，不宣称三个状态都在最终版本重新实跑。 单列新会话，原两次NORMAL失败保持，无新采集/Evidence，不执行健康检查。
  [原始记录](../reports/quality/planning-retrieval-v2-20261002/live/post-prompt/normal-smoke/summary.json)。

## 面试材料与能力边界

测试开发重点是预先声明判据、旧缺陷负向复现、真实数据库竞争、原始数据复算及失败保留。
后端开发重点是异步状态机、幂等、事务与行锁、取消竞争、授权和可恢复事件。
Agent 开发重点是结构化规划、工具白名单、预算、检索与证据门禁，说明实际评估范围。
正式讲稿见 [INTERVIEW_DEMO_GUIDE](INTERVIEW_DEMO_GUIDE.md)，剩余扩展见 [剩余工作](REMAINING_WORK_20260930.md)。

每份源码 CI 与部署回执对应自己的提交，不能将后续工具或文档提交冒充当前线上应用来源。
