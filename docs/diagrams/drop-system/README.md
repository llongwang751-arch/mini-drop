# Drop 系统图解

打开 [图册总入口](index.html)。双击 `index.html` 可以离线看图；解压整个文件夹后仍可使用，无需登录或启动数据库。每张图可独立交互查看，源码引用打开带行号和 SHA 的本地快照。

从左侧 01 顺着读，或者直接选“具体采集了什么”“模块通信与字段”“实际怎么点”。图中的消息可以点击查看说明，右下角支持缩放、搜索、路径和地图。

| 图 | 回答什么问题 |
| --- | --- |
| [01 系统总览](01-system-architecture.html) | React、Go、C++、Python、PostgreSQL、MinIO 各管什么 |
| [02 指令与文件](02-control-data-flow.html) | 小消息和大文件分别怎么走 |
| [03 采集时序](03-collection-sequence.html) | 点击后谁接单，谁下发，谁采集 |
| [04 文件与分析](04-artifact-sequence.html) | RAW、附件、校验、分析制品怎样接起来 |
| [05 浏览器结果](05-result-sequence.html) | REST、SSE、下载 Worker 与布局 Worker 怎样配合 |
| [06 采集状态](06-collection-lifecycle.html) | 创建、排队、采集、上传、完成、失败和取消 |
| [07 分析与租约](07-analysis-lifecycle.html) | 领取、续租、成功、重试、过期接管和取消 |
| [08 AI 工具调用](08-ai-tool-sequence.html) | AgentScope 分支怎样调 Go 工具、核对证据 |
| [09 智能调查](09-investigation-sequence.html) | 规划、确认、原采集、复测、额外补证 |
| [10 源码修复](10-repair-workflow.html) | 登记项目、源码片段、隔离测试和补丁交付 |
| [11 回执丢失](11-recovery-sequence.html) | Outbox 如何重读事实、避免盲目重复执行 |
| [12 定时与复合](12-schedule-composite.html) | 定时触发、DAG 依赖、子任务和结果汇总 |
| [13 业务信号](13-business-signals.html) | 已授权服务与同一采集窗口的业务指标、日志类别、Trace 摘要 |
| [14 平台监控](14-platform-observability.html) | 可选监控叠加配置如何观察 Drop 自身 |

[采集器对照表](collectors.json) 覆盖契约目录全部 18 项：16 种现场采集、`IMPORT_COLLAPSED` 和 `COMPOSITE`。每项说明实际工具、具体数据/单位、RAW、解析结果、前提和不能推断的结论。导入格式能力另列，不将导入样本算成现场采样。

图中总览的箭头表示逻辑流；精确通信跳点看时序图及协议表。例如 Agent 主动发心跳/拉取 RPC，Server 在响应中下发任务；逻辑上的 Python → 页面，还要经过结果发布、API 授权和对象下载。

普通采集、规则解析与 AI 是不同阶段。08 展开的是 `AI_AGENT_BACKEND=agentscope` 分支；源码默认另有 Go 内 `legacy` 循环。本轮不推断当前生产配置。13、14 标注可选接入，不表示每个环境都已启用这些服务。

## 保存静态图

优先用图册中的“阅读版 PNG”或“全部静态图 / 下载”。[read-png](read-png/) 中是浏览器对默认 READ 主图的原生截图，保留图和图例，不含工具栏。结论卡、完整字段和源码说明仍在交互页面中。

Viewer 自带的规范 SVG/PNG 与 Share Card 导出会展开 fine 细注。实际检查发现，部分长注释时序图有重叠和右缘裁切；菜单没有保持 READ 的简洁导出。导出文件能生成且不含 Viewer 工具栏，不代表细注排版通过。此限制记录在 [collection-visual-review.json](collection-visual-review.json)，因此另外提供清楚的阅读版 PNG。

## 核对来源与验证

- [validation.json](validation.json)：逐张绑定 specification/HTML SHA、确定性 9/9 校验、自动浏览器检查与独立视觉审阅。
- [source-manifest.json](source-manifest.json)：本地引用文件的真实 SHA、字节数、行号和可阅读快照。基础 Git revision 是来源信息；工作区未提交内容按文件 SHA 记录。
- `*.visual-check.json`：四桌面尺寸测量和两端尺寸的深浅色截图；其中 `visualReview: pending` 保持工具原值，独立审阅在另外的 `*-visual-review.json` 中。
- [portal-browser-check.json](portal-browser-check.json)：离线入口、目录、筛选、移动布局和零外部请求检查。
- `candidate-history/` 与失败收据保留排版修正过程，不在最终阅读图册与分发 ZIP 中充当合格图。

本轮是代码说明与图表交付，没有执行现场采集、模型调用、生产故障注入、部署或服务重启；未修改业务执行逻辑。

## 维护

修改图的 JSON 后，用 Archify 对对应类型重新 `validate`、`deliver`、`visual-check`，再实际查看图片。不能直接改已交付 HTML 后沿用旧收据。候选布局未通过时，旧 HTML 可能仍然存在，不能据此声称新候选通过。

在仓库根目录运行：

```powershell
python docs/diagrams/drop-system/build-atlas.py
node web/node_modules/prettier/bin/prettier.cjs --write docs/diagrams/drop-system/index.html
```

构建器核对图的 JSON/HTML 与交付收据 SHA 后，生成离线总入口和源码快照。重新生成图册后，也要更新浏览器检查、阅读版截图和汇总收据。
