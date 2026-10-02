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
| 发布版 CI | [14/14 作业](https://github.com/llongwang751-arch/mini-drop/actions/runs/36966142203) |
| 发布版 Python | 1682 通过、16 登记跳过；真实 PG 与 Chroma 由独立作业执行 |
| 发布版 Web / Chromium | 317 项 / 8 项；Chromium 为固定数据回归 |
| 真实 PostgreSQL | 14 项、0 跳过 |
| 线上浏览器 | 41 份下载 SHA 一致、48 次布局检查通过 |
| 线上版本 | `20261002T045234Z`，源码 `de094fff754f2d8cb7139dd99e31c7a30d5fb754` |

性能实验中的工程判断包含支持观测和有效反证，不表示每类都定位了根因。
真实业务同负载修复案例是独立 HTTP/SQLite 样例；模型规划、知识检索与真实采集分别验收。

## 本轮交付收尾

- **干净 Linux 核心平台复刻：PASSED**。从全新 Ubuntu 22.04 Runner、精确源码 34b74e7b 构建七个镜像；10 个阶段全部通过，真实 Agent→Task→S3 Artifact→Analyzer 与 SQL 身份绑定、HTTP 401、清理均验证。87 项门禁测试（含负向用例）零跳过；范围限核心平台及 sys_metrics。
  [原始记录](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/report.json)。
- **新题规划与检索盲测：COMPLETED**。24 题预先冻结，真实模型零重试，22 响应/2 超时；结构与分类 15/24、判断 11/24、下一工具 13/24。BM25 Recall@3 75.83%、MRR 0.80、无答案误召回 3/4。部分指标命中，效果仍有缺口；干净 Git 独立复算一致，不计为现场因果准确率。
  [原始记录](../reports/quality/interview-release-20261002/heldout/independent-review-r2/audit.json)。
- **文档与求职材料统一：COMPLETED**。六份当前设计与求职文档、既有本地简历段落和五分钟讲稿已统一，保留真实参与边界；个人简历未提交，用户本人学习和彩排仍须完成。
  [原始记录](../reports/quality/interview-release-20261002/materials/doc-final-review-public.json)。
- **本轮源码主 CI：PASSED**。源码 34b74e7b 的官方 CI 36975451853 实际 14/14；Python 1827 通过/16 登记跳过、Web 317、真实 PostgreSQL 14 零跳过、Native CTest 5、Chromium 8 固定数据。与上方现有线上 de094fff 发布 CI 分开。
  [原始记录](../reports/quality/interview-release-20261002/main-ci-34b74e7b/summary.json)。

## 面试材料与能力边界

测试开发重点是预先声明判据、旧缺陷负向复现、真实数据库竞争、原始数据复算及失败保留。
后端开发重点是异步状态机、幂等、事务与行锁、取消竞争、授权和可恢复事件。
Agent 开发重点是结构化规划、工具白名单、预算、检索与证据门禁，说明实际评估范围。
正式讲稿见 [INTERVIEW_DEMO_GUIDE](INTERVIEW_DEMO_GUIDE.md)，剩余扩展见 [剩余工作](REMAINING_WORK_20260930.md)。

每份源码 CI 与部署回执对应自己的提交，不能将后续工具或文档提交冒充当前线上应用来源。
