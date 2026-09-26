# 性能 SRE Agent 云端发布（2026-09-19）

已发布到 <https://120.24.187.205/ai-diagnosis>。当前目录 `/opt/mini-drop-releases/20260919T123800Z`，`/opt/mini-drop-current` 已切换到该目录；前一版本 `/opt/mini-drop-releases/20260914T073540Z` 保留。

## 发布内容

更新 Diagnosis Worker、Analyzer、Web，新增仅内网可访问的 Chroma 1.5.9。沿用现有 Go API、C++ Control/Agent、PostgreSQL、MinIO、业务服务、认证与 mTLS、SSH 隧道；没有数据库迁移，也没有删除历史报告、对象或卷。

聊天使用 SiliconFlow DeepSeek-V3.2；混合检索使用 Qwen3-Embedding-4B（1024 维）、BM25/RRF、Qwen3-Reranker-4B。云端公共知识索引 9 块，实际查询后端为 `BM25_CHROMA_RRF_RERANK`。模型单请求超时 90 秒、非思考模式、无自动重试；图步数上限 64，主模型每次 invoke 最多 6 次，参考查询最多 4 次。

新版本包含按需检索工具、同用户/服务/环境历史报告召回、ReAct 选项、完整证据覆盖门禁及有类型的处置建议。默认策略仍为 LATS。Grafana 适配代码已发布，但未配置实际 Grafana 数据源；历史记忆的召回质量、策略效果优劣仍待专项评估。

## 实际验证

| 检查 | 结果 |
|---|---|
| 公网健康接口 | Control、PostgreSQL、Diagnosis Worker 均 healthy |
| Agent Runtime | `diagnosis-agent-v5-retrieval`；PostgreSQL Checkpoint HEALTHY，无降级 |
| 采集节点 | Control 演示 Agent、两台腾讯 Worker 全部 ONLINE |
| 静态发布 | 45 个 Web 文件与本地构建 SHA-256 全部相同 |
| 源码 | 222 个运行源码/知识/示例/静态文件与最终发布清单一致 |
| 浏览器 | 实际 Chromium 打开已完成报告，0 个 JS 异常和 HTTP 错误 |
| 回归 | Python 定向 39 passed、2 skipped；前端 29 passed；生产构建和 bundle 检查通过 |

宿主 Python 的两项 Chroma SDK 测试因未安装 SDK 跳过；云端 Chroma 实际建索引、检索均通过。最初测试临时目录父路径缺失造成的 fixture 错误已修正后重跑，初始日志保留。

两次真实诊断均通过模型规划、混合检索、采集、证据导入和故障撤销检查：

| 策略 | Diagnosis | 工具 | Evidence / Artifact | 报告门禁 |
|---|---|---|---|---|
| ReAct | `insight_99a23992f06846259038da135895a5e0` | py-spy、sys_metrics、perf，3/3 完成 | 13 / 13 | 1 份 INSUFFICIENT_EVIDENCE，2 份 PARTIAL_WITHOUT_COUNTER |
| LATS | `insight_5c9622b4b8d1408ca7f864f5f540fa45` | py-spy、sys_metrics、perf，3/3 完成 | 13 / 13 | 1 份 INSUFFICIENT_EVIDENCE，2 份 PARTIAL_WITHOUT_COUNTER |

两条会话首轮为 `MODEL`，后续为 `MODEL_REPLAN`，并保留系统未知原因兜底假设。不是由纯规则兜底完成的冒烟检查。原始结果在服务器当前目录的 `verification-react.json`、`verification-lats.json`，本机副本在 `output/cloud-sre-20260919/`。

**这不是根因准确率或修复验收。** 两次会话均未得到 VERIFIED 根因报告，也没有同负载代码修复对照。ReAct 报告的阶段性 Top 帧为示例进程内的 `_sample`，不能据此声称已正确定位注入的 `source_hot_function`。这里只确认部署和两条执行链路工作；仍需改进采样归因、独立反证与真正修复复测。没有重新执行 21 场景，不更改历史失败成绩。

## 发布证据

- 最终源码包：`source-final.tgz`，SHA-256 `67d5d8298134b842f27185f802518a45df93313e643fc9a69b4f6b9357bd2b17`。
- ReAct 原始 JSON SHA-256：`8af8d0573efdbcbf052e495821f76e9b7ac83cb8d6037dabaf9d8a62478a41a8`。
- LATS 原始 JSON SHA-256：`8c44cd283838e85310771d26d9593ac56047af683697873c991951a6d986029e`。
- Python/Chroma 镜像：`mini-drop-python-worker:20260919T123800Z`，ID `086e384c10dc08cbebc75660894163b145398994714a934bccb18f274489d3f6`。
- Web 镜像：`mini-drop-web:20260919T123800Z`，ID `ddb835bc9c66bdbdc4161f1bf52ca256d03358db63e97fbee1e8ce1fd25ad31f`。
- 浏览器结果：`output/local-sre-20260919/browser-1789822690756/`（脚本历史输出目录名，不表示本地页面；JSON 记录实际 HTTPS URL）。
- 服务器当前目录保留 `final-health.json`、`security-boundary-check.json`、`source-check-final.json`、`python-packages.json`、构建与激活日志。

直接云端 pip 下载过慢，初始两次构建取消，线上服务未受影响。最终使用 80 个 Linux wheel 离线安装，并检查逐文件 SHA-256；Windows 跨平台下载会漏掉环境标记限定的 uvloop，已显式补齐。失败构建日志保留，没有清理服务器镜像缓存或历史数据。完成时磁盘剩余约 4.1 GB，后续大规模发布需另行规划存储。

## 运维与回滚

线上运行配置为当前目录 `private/runtime.compose.json`，来自原运行环境快照；目录权限 700、文件 600，包含凭据，不得提交或复制到报告。认证、存储凭据、原 Web 端口经前后比较保持一致；Chroma 无 host port。

在控制机只更新这些服务：

```bash
docker compose -p mini-drop-control -f /opt/mini-drop-current/private/runtime.compose.json up -d --no-deps chroma diagnosis-worker analyzer web
```

该配置只声明本批服务，其他同项目容器的 orphan 提示是预期的；**不要添加 `--remove-orphans`、`down -v` 或执行 prune**。

回滚命令（本次未实际触发回滚）：

```bash
python3 /opt/mini-drop-releases/20260919T123800Z/scripts/release_sre_cloud.py rollback
```

回滚使用保留的 `mini-drop-sre-rollback:<service>-20260919T123800Z` 镜像与 `private/rollback.compose.json`，逐个检查健康后恢复旧 current 指针；不删除 Chroma 卷或本次诊断证据。后续构建使用 `scripts/package_sre_release.py` 生成完整源码包，`release_sre_cloud.py` 管理阶段操作，`sre-release-python.Dockerfile` 要求预先准备带 `sha256.json` 的 `release-wheels/`。
