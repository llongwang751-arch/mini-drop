# Mini-Drop 面试源码交付（2026-10-02）

本次将可复现性、新题评估和求职讲解固定为 `interview-20261002-rc1`。它是源码与证据交付版本；云端应用继续使用 `20261002T045234Z` / `de094fff`。事实入口为[当前交付](../../docs/CURRENT_DELIVERY.md)，五分钟演示和三类岗位讲解见[演示指南](../../docs/INTERVIEW_DEMO_GUIDE.md)与[深挖问答](../../docs/INTERVIEW_DEEP_DIVE.md)。

| 交付范围 | 实际结果 | 固定证据 |
|---|---|---|
| 新 Linux 核心平台 | 新 Runner 构建七个镜像，10 阶段全部通过、87 项门禁测试（含负向用例）零跳过 | [官方 Clean CI 36975451670](https://github.com/llongwang751-arch/mini-drop/actions/runs/36975451670)、[实际报告](../quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/report.json) |
| 同源主 CI | 14/14 作业；Python 1827/16 skip、Web 317、真实 PG 14/0 skip、CTest 5、Chromium 8 | [官方 CI 36975451853](https://github.com/llongwang751-arch/mini-drop/actions/runs/36975451853)、[主报告](../quality/interview-release-20261002/main-ci-34b74e7b/summary.json) |
| 24 题真实模型 | 22 响应/2 超时、零重试；结构/类别 15/24、判断 11/24、下一工具 13/24 | [首轮原报告](../quality/interview-release-20261002/heldout/first-run/report.json) |
| 真实只读 BM25 | 20 正向题 Recall@3 75.83%、MRR 0.80；4 无答案题误召回 3/4 | [冻结协议和失败解释](../../docs/HELDOUT_EVALUATION.md)、[独立复算](../quality/interview-release-20261002/heldout/independent-review-r2/audit.json) |
| 求职材料 | 六份当前文档、本地既有简历段落、五分钟讲稿与三类岗位问答统一 | [最终文档核验](../quality/interview-release-20261002/materials/doc-final-review-public.json) |
| 线上既有能力 | 工程判断 21/21、具体路径 6/21、反证 8 类，四项独立工程缺陷回归 | [线上发布及实际浏览器证据](engineering-score-only-20261002.md) |

CI 和新环境精确源码是 `34b74e7b15c81a883e8a7dc2698d8ba7a356dcbf`；PR checkout `72d452542007e1a01cfb2f923c6fa72471b6fb39` 的 Git tree 同为 `5800ff6eecea4f8e3e54b20f888e9cc403ea978f`。最终标签在其后只固定文档、事实合同与证据；每个后续 CI 都保留自身来源，未将工具提交称为线上新版本。

干净环境独立生成 PKI、鉴权值和数据卷，真实 native Agent 在零附加 capabilities 下采集本次 Python 目标。Task 身份与采样前后独立 PID 证据一致；原始产物 12680 bytes、6 样本、5004 ms，SHA `cd5bf02fb4a51404ba996284e7d344fe61f54feeecfcb1e4c3ac2c8ac355fda2`。SQL 证明唯一成功 AnalysisJob 绑定该 Artifact；鉴权未携带密钥返回 401，Nginx 真实 JS/CSS 与 API 依赖健康被核验。这不是额外一轮浏览器验收。

复刻修复了 Control health channel 的客户端证书路径、非 RPC migrate/Analyzer 的 TLS 入口误触发、公开预制 MinIO 镜像拉取失效与验收脚本的健康命令/独立 PID 读取假设。MinIO 由原同版本官方固定 commit 和 tar SHA 构建，不依赖线上旧缓存。原卷、线上容器和历史证据保持。复刻仅覆盖核心服务及 sys_metrics，不含 Office、全部运行时部署、perf/BPF、外部模型质量、OS 安装、离线安装或小时压测。

所有首次取消和失败有原始记录：36971688615 为取消，36971943853 为 MinIO 拉取失败，36973371776 为健康命令验证不兼容，36973829122 为 Runner 用户不能读取目标 PID namespace。四次旧记录不重写为成功；第五次独立新 Runner 执行 36975451670 才是完整通过。主 CI 36973371779 的 CRLF 质量失败同样保留。清理只删除本次十个容器与一个空网络，临时卷留给 Runner 回收，没有全局 prune 或删除生产数据。

模型的首轮评分规则、真值和 50 个请求/响应/来源哈希不变。26 份原输入打包后可在 clean Git 独立重算，兼容 CRLF 与注释差异但不允许代码语义漂移。题目由维护者预先编写，未声称第三方出题；未执行真实工具、故障或 LangGraph/LATS 闭环。22 次 usage 已知共 76031 tokens，两次超时用量及价格未知，费用不填零。完整结构失败包含正常/缺测/拒绝输出空条件数组的合同冲突；不把 7 次结构失败改写为危险工具选择。

下一项有价值的实现是为正常、缺测、拒绝结果明确结构分支，并改善无答案检索与保守返回，使用新冻结集验证。当前版本不因此改规则、真值或删失败。本人学习与现场彩排仍需实际完成：运行五分钟讲稿，能从请求追到 Task、Artifact、AnalysisJob 与证据，并说明自己理解和修改的边界。

全部本轮原始记录与逐文件 SHA 在[归档清单](../quality/interview-release-20261002/manifest.json)。既有用户改动逐字节保持证明随档保留；个人简历正文、真实凭据及私钥不进入仓库。
