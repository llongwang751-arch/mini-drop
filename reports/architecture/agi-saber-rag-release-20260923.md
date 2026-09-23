# AGI-saber 请求级 RAG 观测接入与云端验收（2026-09-23）

Mini-Drop 现可从真实 AGI-saber 知识库问答选择一条请求，显示改写、向量化、检索、重排和生成的实际阶段耗时，再绑定办公助手当前进程采集 CPU、RSS、线程、FD 和探针证据。原应用仍由 `agi-office-backend.service` 独立运行，Mini-Drop 原生 Agent 负责可信进程绑定。诊断工作台默认先显示“本次观测窗口未确认故障”或已验证故障，再显示根因报告；机器附加的请求观测 JSON 不再出现在标题、案例列表或对话首条。

最终平台指针 `/opt/mini-drop-current` 为 `20260923T105000Z`：Web 镜像使用该标签，Diagnosis Worker、Analyzer、Chroma 沿用 `20260923T101442Z` Python 镜像。办公助手 `/opt/agi-office/current` 为 `20260923T105939Z`。四项平台容器 healthy，公网 `/api/healthz` 三依赖 healthy。前一平台 `20260923T101442Z`、办公助手 `20260923T100428Z` 及 9 月 22 日发布均保留，数据库卷和历史 Evidence 未清理。最终源码包 SHA-256：`26a04be9e87fdedb9c81ae18ff68631c494ff2747d584c42ffcee4bedc492ad5`。

埋点在 `integrations/agi_saber/request_observations.py`，由 `backend_entry.py` 在 `build_deps()` 前安装。原业务 `Response.trace_id` 连接请求；快照最多 100 条，不包含问题、答案、文档正文或凭据。宿主 `/var/lib/agi-office` 保持 0700，仅 `/var/lib/agi-office/mini-drop-observations` 子目录只读绑定到非特权 Diagnosis Worker。读取端限制精确路径、schema、服务 ID、字段、PID 一致性、时间窗和大小；应用自报 PID 不授权原生采集。历史问答阶段与后来系统采样是不同时间窗，不能把后者冒充该问答调用栈。

首个 `20260923T100428Z` 草稿用了 0600 单文件绑定。真实页面测试发现 Worker 主进程 UID 1000 无法读取；单文件绑定还会固定旧 inode，看不到业务进程原子替换后的新快照。修订版改为专用目录绑定、快照 0644（宿主父目录仍 0700）。以 UID 1000 直接读取成功，连续两次真实问答生成两个不同请求 ID，Mini-Drop 服务列表状态 `AVAILABLE`，浏览器选择请求可见检索与生成耗时，未执行阶段明确标缺失。初版不可用状态没有被报告为通过。

终态办公助手 `20260923T105939Z` 补正状态口径：应用在响应体返回错误时，原 HTTP 接口仍可能返回 200；快照将 HTTP 状态和业务 `FAILED` 分开记录，避免页面把业务失败误写成 HTTP 500。重启后再次用原问题验收，HTTP 200、答案校验通过，新请求约 793.568 ms，Worker UID 1000 可读到新版本的新 inode。该口径修正没有改动原业务处理路径。

真实知识库问题“员工年假申请须提前多久提交？”重复两次均 HTTP 200 且答案包含预期内容。第一条请求业务执行 1046.651 ms（生成 1011.38 ms、检索 5.09 ms），第二条 792.715 ms（生成 758.874 ms、检索 4.091 ms）。当前办公助手使用本地词法检索，改写、向量化、远程重排本次未执行或未采到。两次耗时不能当作修复前后效果：本次没有定位并修改 AGI-saber 的性能缺陷，也没有排除并发、缓存和模型波动。

从第一条真实请求创建的诊断 `insight_31065996e81542658cd1800f2bdb00a4` 正确附着相同请求 ID、绑定办公助手 PID 3006820；执行 4 个工具调用，产生 12 份 Evidence，其中系统指标样本 15 个。系统窗口 CPU 0.286%、RSS 94.777 MiB、线程 5、FD 10。三份报告仍为 `INSUFFICIENT_EVIDENCE`，因为没有可验证的异常根因；页面显示有边界的“本次观测窗口未确认故障”，不提升为 `VERIFIED`。这证明正常状态展示和取证链路可运行，不证明根因准确率已提升。

本地 Python 定向 26 项通过；最终 Web 全量 43 个文件、195 项通过（jsdom 需将单项超时设为 15 秒），生产构建与 bundle 门禁通过。最终公网浏览器真实会话与“接入服务 → 选择知识库请求”均无 JS/HTTP 错误；截图在 `output/local-sre-20260919/browser-1790160779094/real-local.png` 和 `output/cloud-sre-20260923T101442Z/browser-services-1790160893114/real-local.png`，API 与进程数字保存在本地未提交的 `output/cloud-sre-20260923T101442Z/case-metrics.json`。

故障广场仍由白名单场景的固定服务端启停接口注入和撤销，不执行用户输入或模型生成的任意 Shell 命令。此次没有对真实 AGI-saber 注入故障。下一项质量工作是将同负载复测与版本/业务结果配对，接入跨服务 OTel/SQL span 和真实缺陷修复后的独立对照；现有阶段计时不能替代这些证据。

Web-only 回滚可用 `20260923T105000Z/private/rollback.compose.json` 恢复上一 Web 镜像，再把 `/opt/mini-drop-current` 原子指回 `20260923T101442Z`。Python 服务回滚使用 `20260923T101442Z/private/rollback.compose.json`；办公助手单独把 current 指回 `20260922T094352Z` 并重启 systemd。回滚不删除卷、历史发布或 Evidence。
