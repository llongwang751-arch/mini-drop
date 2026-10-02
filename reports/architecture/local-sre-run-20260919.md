# Windows 本地 SRE Agent 运行验证（2026-09-19）

本机入口：<http://127.0.0.1:18080/ai-diagnosis>。本批不连接已过期的云服务器。使用独立 Compose 项目 `mini-drop-local-sre`；原有项目、历史报告、数据库与对象存储数据保留。操作入口见 [本地复刻说明](../../docs/REPLICATION.md#2026-09-19-windows-本地-sre-环境)。

## 实际通过范围

- Docker Desktop 修复启动方式启动成功；10 个服务运行，已配置健康检查的服务全部 healthy。数据库迁移成功。
- Go 健康接口确认 Control、PostgreSQL、Diagnosis Worker 健康。Runtime 返回 `actual_backend=postgres`、`degraded=false`，数据库存在真实 checkpoint。
- SiliconFlow DeepSeek-V3.2 为聊天模型；Qwen3-Embedding-4B（1024 维）和 Qwen3-Reranker-4B 连接独立 Chroma 服务。公共知识库索引 9 个块。诊断事件实际记录 `BM25_CHROMA_RRF_RERANK`，并非仅配置了 hybrid。
- 最终诊断 `insight_b23950c24fc44196817e12705ea835de` 完成，首轮来源 `MODEL`，第二、三轮来源 `MODEL_REPLAN`。py-spy、sys_metrics、perf 三次调用全部 COMPLETED，保存 13 条 Evidence 与 13 个 Artifact；故障已停止。
- 1446 个有效 Python 样本中，`source_hot_function` 占比 97.4%。报告门禁分别为两份 `INSUFFICIENT_EVIDENCE` 和一份 `PARTIAL_WITHOUT_COUNTER`，没有 VERIFIED 根因报告，也没有同负载代码修复复测。
- 真实 Chromium 页面读取最终诊断通过，0 个 JS 异常和 HTTP 错误；45 个 Web 静态文件与本机构建 SHA-256 全部一致。

最终原始结果：[real-diagnosis-20260919-r3.json](../local-sre/real-diagnosis-20260919-r3.json)，SHA-256 `bbc29221e8cb0c3f83c2e34a86eb4dd47be7bcdc5cc87ef44bc9cc6af2ea7ab4`。浏览器结果与截图在 `output/local-sre-20260919/browser-1789819631631/`。原始本机产物按仓库忽略规则保留在本地，本文是可版本管理的摘要。

## 发现并处理的问题

1. Docker 原运行路径启动失败，使用用户已有的修复启动脚本。此前异常构建留下部分零字节源码与依赖；无缓存重建 Python/演示/Web 镜像，校验源码大小与依赖导入。未重置 Docker，也未删除数据卷。尚不能证明引擎中断的唯一根因。
2. MinIO 原 mc 健康检查配置损坏，局部覆盖为直接检查服务 live 端点。
3. 首次诊断规划超过 30 秒，实际规则兜底；保留 r1 原始报告。按 [SiliconFlow 官方示例](https://www.siliconflow.com/zh/blog/deepseek-v3-2-now-on-siliconflow-reasoning-first-model-built-for-agents) 显式关闭思考模式，并把本地单请求超时设为 90 秒，仍禁用自动重试。
4. r2 首轮图执行触发 16 步限制，后续模型重规划成功；保留该报告。图步数计入 middleware，因此扩为 64 步；独立的每轮 6 次主模型调用、4 次参考查询限制不变。r3 首轮与重规划均成功，无此错误。
5. 未选中诊断时前端请求不存在的全局 V2 事件流，导致 404。现在等待有效诊断 ID 后连接，清空选择时断开。回归 10 项通过。
6. 前端构建一度内存分配失败，使用单个 Rayon 线程后构建与 bundle 检查通过。

新增模型选项测试 3 项通过，Agent 改造定向测试 23 项通过、2 项因宿主 Python 缺少 Chroma SDK 跳过；容器内 Chroma 的真实索引、在线查询已通过。没有重新执行整个历史测试集或 21 场景验收。

## 使用边界

服务运行在本机 Docker/WSL 的 Linux 环境，不是对 Windows 原生进程取证。此次仅启动 Python 故障演示；Go/Java/C++ 故障实验、Grafana 实际连接、长期稳定性和 ReAct/LATS 效果对比未验收。缓存原生镜像用于本机启动，不是全新机器的完整可复现构建。鉴权关闭且端口仅绑定回环地址，不能原样公开部署。

模型单请求超时并非整次诊断硬实时期限。知识和历史记忆仍不是当前故障证据。本次 `passed=true` 仅指真实链路、模型规划、混合检索与故障清理通过，不是准确率、VERIFIED 根因或自动修复成绩。密钥仅在 Git 忽略的 `.env.local-sre`；已检查代码 diff 和验证 JSON 无密钥值。
