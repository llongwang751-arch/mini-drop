# 真实后台服务接入

## 2026-09-24 云端样本文档清理

旧的 14 篇办公助手 `user_upload` 验收样本已清除。应用 `DELETE /api/documents/{id}` 会删除检索块并把文档标为 `deleted`，但本地 SQLite 仍保留旧版本正文，且不可用的 Elasticsearch 投影任务会使旧 Milvus 删除任务在队列中滞留。因此清理先通过原接口按 ID 删除，确认全部旧文档已删除且检索块为 0；再停止办公助手，按已确认的 14 个 ID 移除旧版本和文档行、清空仅由这些检索块产生的投影任务、重建空 `rag_chunks` 向量集合，对 SQLite 运行 `VACUUM` 与完整性检查，最后重启服务。数据库文件、向量库目录与平台卷均未删除。新建的小型健康样本经重启后仍有 1 个 1024 维向量、语义召回和正确问答。旧请求观测仅保存数字，可在 24 小时内作历史记录，但对应文档已不可再查询。当前容量及步骤见 [清理验收](../reports/business-acceptance/cloud-data-cleanup-20260924.md)。

## 2026-09-23 百万字文档与真实向量库

办公助手原网页 `/api/office/` 的上传仍由原 FastAPI 处理。原 SQLite 每个分块提交一次，百万字可形成 7,701 次提交；现在按最多 100 块一事务提交，Embedding 和向量写入按最多 32 块一批流式执行，避免构造整篇向量数组。已安装 `pymilvus==3.0.2` 与 `milvus-lite==3.2.1`，本机向量库位于 `/var/lib/agi-office/vector/milvus.db`，重启后显式 load collection。Embedding 使用硅基流动 OpenAI 兼容接口的 `BAAI/bge-m3`（1024 维）；API URL、模型、密钥和向量库路径由 `/etc/agi-office/backend.env` 注入，密钥绝不写进观察、Git 或网页。部署依赖见 `integrations/agi_saber/requirements-vector.txt`；对云端原始 AGI-saber 发布的补丁见 `integrations/agi_saber/patches/`，补丁需对记录的原始源码版本应用，不能盲目套用到其他版本。

公网办公助手路由 `/api/office/` 的 Nginx `proxy_read_timeout` 是独立配置。百万字向量上传首轮因旧 120 秒限额收到 HTTP 504；`deploy/nginx/control-tls.conf` 将该路由改为 600 秒，保留其他 API 的原超时。该调整只允许长请求返回，不代表消除了远程 Embedding 的耗时，页面仍需明确展示慢阶段。

`rag.ingest` 记录上传 request_id、文本长度、块数、Embedding 调用与失败数、**已成功向量入库数**、各阶段真实耗时、进程 CPU/RSS 峰值和 systemd cgroup CPU/内存峰值与限额。诊断选择该请求后，页面先给出主要慢阶段与资源数字，完整明细按需展开。新字段没有采到时显示缺失，不会填零。百万字实测 7,701 块、241 次远程 Embedding 全部成功、总 289.1 秒，其中向量化 230.8 秒。旧 512 MiB systemd 限额小于 Milvus Lite 加应用的服务组峰值；初升至 768 MiB 后网页复验仍触发 cgroup 回收，升至 1 GiB 后网页上传成功，但在多篇百万字文档重启加载时发生一次 OOM 自动重启。当前持久限额设为 1.5 GiB，启动加载实测峰值约 1.26 GiB。主机可用内存另行观察，不能把 cgroup 限额命中说成主机内存不足。

故障演示通过受限服务端接口给**当前问答请求**的检索阶段加入 2,500 ms 延迟；基线、故障、撤销后在三个请求窗口对比，超时自动撤销。撤销注入是恢复演示条件，不代表完成代码修复或真实故障根因 VERIFIED。原生 Agent 仍独立采集当前进程窗口，历史上传的请求级数字不冒充同时间窗原生 Evidence。端到端步骤与证据见 [全链路验收](FULL_CHAIN_ACCEPTANCE.md)。

进程内近 100 条请求快照升级为 `mini-drop.office-observations.v2`，启动时从旧快照继承通过字段白名单、时间窗和大小校验的历史数字，不继承问题、答案、文档正文或未知字段。Mini-Drop 只读解析旧 v1 和新 v2；v2 的 `producer_started_at_unix` 用于区分旧 PID 在重启前已完成的请求与当前 PID 的新请求。历史 PID 不授权当前进程采集，也不影响 Agent 的新鲜绑定。云端从两份保留的真实快照合并 12 条后上线，经新问答与再次重启有 14 条、4 个历史 PID，百万字上传仍可在页面选择、`invalid_records=0`。这仍是有界近 24 小时快照，不是永久审计库。
请求下拉框支持按操作名称、字数等可见标签搜索；历史上传即使排在虚拟列表后部，也能输入 `999,999` 找到。关联旧请求后的体检仍绑定当前服务进程，页面将两种时间窗分别标注；原请求观测里的内存限额保持历史值，不自动改写为当前限额。

## 2026-09-23 AGI-saber 知识库问答阶段

原办公助手保持 `/api/office/` 入口。`integrations/agi_saber/request_observations.py` 在部署入口 `backend_entry.py` 构造依赖前安装有界埋点：`LLMRewriter.rewrite`、`HybridStore.search_multi`、实际使用的 `_embed_fn`、启用时的 `HybridStore._finalize` 重排、`UnifiedAgent._llm_generate`，以及 `process_with_options`/`process_stream` 的总执行时间。多查询检索并发线程继承请求上下文；阶段只记录耗时，搜索阶段扣除向量化与重排，避免在页面上重复加总。生成的 request_id 采用原 `Response.trace_id` UUID；没有可用 UUID 时生成独立 ID。快照只保留最近 100 条、24 小时内读入、最大 256 KiB，不导出问答文本、文档或凭据。宿主业务根目录仍为 0700；Worker 只读挂载专用观测子目录以跟踪原子更新，0644 的快照文件供非特权 UID 读取，其他业务库和数据不挂载。

Mini-Drop 从只读文件选择真实 `rag.question` 记录，诊断仍由登记服务和 Native Agent 重新发现、校验目标进程。请求记录中的 PID 只是应用自报，不授予采样权限；后续 CPU、RSS、栈与探针属于复现窗口，不是历史请求的执行轨迹。此处原发布曾使用本地词法检索；2026-09-23 接入 Milvus Lite 后，当前发布可执行语义检索，历史记录保持原样。这个接入是专用请求级业务遥测，不是完整 OTel/跨服务 Trace，也没有 SQL span、首 token 计时或自动修复效果证明。旧发布验收见 [发布记录](../reports/architecture/agi-saber-rag-release-20260923.md)。

快照将 HTTP 状态与业务结果分开记录：原接口即使在响应体返回错误时仍可能返回 HTTP 200，届时记录 `status=200`、`result=FAILED`，不会把业务失败冒充 HTTP 500。终态办公助手发布为 `20260923T105939Z`。

## 2026-09-22 办公助手业务指标

办公助手入口已加入依赖无关的 ASGI 聚合中间件，并发布到 `/opt/agi-office/releases/20260922T094352Z`。它只输出与当前进程 PID 绑定的请求数、5xx 数、处理中请求、累计耗时和最近 256 次请求的平均/P95，不保存 URL、正文、响应内容或凭据。systemd 的 `PrivateTmp=true` 保持不变；Native Agent 通过 `/proc/<pid>/root/tmp/mini-drop-app-metrics.json` 读取该进程自己的快照。Analyzer 执行 PID 身份核对与字段白名单，再把窗口前后差值写入 Evidence。该路径属于受限业务指标接入，不等于完整 OTel Trace；函数阶段、SQL span 与跨服务拓扑仍未接入。

## 2026-09-14 已运行的四个轻量业务

四个业务的原网页、账号、数据库和文件独立保留。Mini-Drop 新增服务请求关联和采集保护，使用现有两台 Worker，不增加服务器。

| 业务 | HTTPS 入口 | 负责 Agent | 稳定服务选择器 |
| --- | --- | --- | --- |
| Memos v0.30.0 | [打开](https://120.24.187.205:18441/) | tencent-cvm-worker-1 | mini-drop-business-memos.service |
| File Browser v2.63.23 | [打开](https://120.24.187.205:18442/) | tencent-cvm-worker-1 | mini-drop-business-files.service |
| linkding v1.46.2 | [打开](https://120.24.187.205:18443/) | tencent-lighthouse-worker-2 | mini-drop-business-linkding.slice |
| ntfy v2.28.0 | [打开](https://120.24.187.205:18444/) | tencent-lighthouse-worker-2 | mini-drop-business-ntfy.service |

用户先登录 Mini-Drop，再在“AI 诊断 → 接入服务”打开业务页面、使用独立业务账号操作。返回平台后选择请求、填写现象、开始诊断；技术进程详情可按需展开。账号文件在本机 `C:/Users/洛伦兹力不做功/.codex/private/mini-drop-business-accounts.json`，不要提交到仓库。

File Browser 上游已归档，仅保留隔离演示；文件 API 需要平台认证和原应用认证。未授予平台凭据的文件 JWT 无法访问文件 API。业务只监听 Worker 环回地址，受限 SSH 通道从控制机 Docker 网桥转发，入口使用独立 IP HTTPS 端口。此前 sslip.io 域名在本次网络下握手失败，保留历史解析记录但不作当前入口。

### 请求关联与运行时保护

网关只记录固定操作分类、服务版本、request_id、时间、状态、发送字节数，不记录原 URL、正文、文件名、查询词和凭据。平台按服务从共享目录只读解析完整记录，每次最多 2MiB、24 小时内；拒绝冲突 ID、跨服务、越界路径和过期选择。原观察存入 `diagnosis.created` 事件，不伪造原生 Evidence。日志每五分钟检查 5MiB 轮转阈值，保留 3 份归档；轮转后过期选择需要刷新。

Cookie 按主机共享而非按端口隔离。业务反代只转发各上游自己的认证 Cookie，并移除平台 API Key。文件 API 的平台认证通过内部子请求验证。ntfy 支持 WebSocket Upgrade，同时保留 JSON/SSE 长连接；连接持续时长不能当作单条消息延迟。

本批没有为三个 Go 业务登记与进程绑定的 pprof 端点，不能复用演示 go-hotspot 的端点；当前使用系统指标、perf、内存等通用采集。

每次采样仍使用当前新鲜可信快照；请求的历史时间与当前复现窗口明确区分。linkding 原 HTTP router/master/后台任务曾造成歧义，现使用 uWSGI http-socket、1 worker/2 threads，只绑定该 HTTP 工作进程；多符合项仍要求澄清，不猜 PID。原生 Agent 为显式业务 slice 提供稳定服务提示，不把系统通用 slice 当业务。

实机发现旧回退会把未知 Go 程序交给 JVM attach，触发 SIGQUIT。已修复：服务端未知运行时禁用专用采集器，绑定存在时不使用用户文字/服务名覆盖未知身份；Native 在 JVM attach 前读取 `/proc/<pid>/maps`，必须存在可执行 libjvm.so 映射，否则返回 RUNTIME_MISMATCH，完全不调用 asprof。三个 Agent 已更新。嵌入式/其他 JVM 未被这项 HotSpot 检查确认时应拒绝，不能试探性发送信号。

### 源文件与部署

- `integrations/lightweight/catalog.json`：业务版本、目标主机、端口、资源限制。
- `artifact-lock.json`：核对过的二进制校验和和容器 digest。
- `fetch_upstreams.py --work-dir <目录>`：从固定上游下载，并核对上游与锁文件摘要。
- `install_apps.py --work-dir <目录> [memos files linkding ntfy]`：通过已有 SSH 安装到独立目录，账号私有保存，systemd 开机启动；数据目录不重建。二进制更新使用校验后原子替换。
- `generate_gateway.py`：生成 `deploy/nginx/business-apps.conf` 及 Python 服务目录；不能直接改生成文件。代码部署流程仍需构建发布镜像。
- `reload_gateway.py`：对已存在的控制机配置做语法检查与平滑 reload，出错恢复旧内容；不创建 SSH 通道或替代首次平台部署。
- `install_log_rotation.py`：在控制机以 root 安装周期日志轮转。
- `website_fixture.py`：私有、限长延迟的受控网页依赖，只用于验收，不是业务替身。

实际首次安装、Memos 初始化、SSH 通道、Agent 构建与滚动发布脚本/回执在 `output/acceptance/lightweight-business-20260913/`。应用二进制 `/opt/mini-drop-business/<id>/<version>`，状态 `/var/lib/mini-drop-business/<id>`，凭据 `/etc/mini-drop-business/<id>-account.json`。ntfy 初次启动创建 auth DB 后再创建用户；Memos 初始化管理员后关闭公开注册。linkding 使用镜像固定 digest、专用 slice 和持久卷。

最终发布、截图、16 个采集任务与业务指标见 [本批验收报告](../reports/business-acceptance/轻量业务接入与验收-20260914.md) 及其 `final-state.json`。八项基础操作通过，最新四个会话均为证据不足。函数 span、数据库执行计划、跨服务拓扑、真实缺陷修复闭环和容量未完成；既有 21 场景成绩不变。

## 以下为 2026-09-13 原办公助手接入记录

> 当时范围（2026-09-13，后续请求关联见上文）：本文记录服务部署、发现和采集接通的事实，尚不代表业务请求已自动关联到诊断或业务故障已修复。请求/阶段遥测、异常入口、具体修复案例的优化方向见 [业务接入设计](BUSINESS_ONBOARDING_DESIGN.md)，其中新增能力尚未实现。

## 目标与上一轮的区别

业务项目仍独立运行；Mini-Drop 通过同机 Agent 发现它，在出现性能问题时绑定当前实例并采集。上一轮 `integrations/agi_saber/service.py` 只是原 RAG 引擎的实验适配器，结果在“验证与 A/B”保存。本次运行原办公助手完整 FastAPI 应用，不使用该适配器替代业务后台。

实际链路：办公助手登录 → 上传文档 → SQLite 持久化、分块和检索 → 真实生成模型回答；Mini-Drop 服务目录 → 原生 Agent 的 systemd 服务身份 → 新鲜进程绑定 → AI 规划 → 白名单采集 → 报告。

## 页面操作

1. 登录 Mini-Drop，进入“AI 诊断 → 接入服务”。
2. “AGI 办公助手后台”显示 Agent、systemd 服务名、当前 PID 和最近快照时间。“已发现后台进程”只表示进程存在。
3. 点击“打开办公助手”。该入口为 `/api/office/`，外层要求 Mini-Drop 浏览器会话，内部仍使用办公助手自己的账号和 JWT。首次使用在办公助手注册账号，再登录；两套账号不同。
4. 在办公助手上传一份制度文本，打开知识库问答并提问。业务库与本机原助手的数据、Mini-Drop PostgreSQL/MinIO 完全分开。
5. 回到“接入服务”，写清楚慢的是哪个接口、什么操作之后变慢、是否持续，然后点击“诊断这个后台”。业务请求与采样应处在相同时间窗；空闲时不能保证采到 CPU 热点。
6. 查看该会话的工具、调用栈、证据和报告。诊断完成、根因确认、修复复测分别判断，不能从页面绿色标签推出故障已修复。

## 服务如何绑定

登记源是 `server/app/drop_insight/managed_services.json`。登记 `id/name/environment/service_hint/agent_id`，不保存 PID、密码或执行命令。首个服务的精确选择器是 `agi-office-backend.service`，Agent 为 `control-interview-demo-agent`。

`GET /api/v2/services` 将登记与该 Agent 最新的完整、可信、新鲜快照连接；过期、缺失和截断不会伪装成在线。`POST /api/v2/services/{service_id}/diagnoses` 接受症状与模式，服务端限定 Agent、精确核对服务名，再调用原发现和 clarify 流程。身份包含 PID、启动 ticks、boot ID、namespace、executable 和 snapshot；进程重启后重新绑定，不能复用旧 PID。多进程歧义保留为待确认，不猜一个实例执行。

目录是运维登记，不是任意服务自动注册接口；还没有 Kubernetes Service/Pod 发现、OpenTelemetry 全链路拓扑和跨服务自动根因关联。API 延用 Go 的角色权限；受限资源账号仍因 V2 未完成范围隔离而返回 403。

## Linux 部署

原仓库：`D:/洛伦兹力不做功/Desktop/AGI-Core项目/AGI-saber-python/final`。打包采用 Python 源文件、Alembic 配置与新构建的 Vue 静态文件白名单，不打包原 `.env`、运行库或个人文档。

- `integrations/agi_saber/backend_entry.py` 只调用原 `main.build_deps()`，让 Uvicorn 绑定私有 Docker 网桥地址。
- `agi-office-backend.service` 使用独立 Linux 用户运行，限制 0.75 CPU、512MiB、128 tasks；开机启动，失败自动重启。
- `/opt/agi-office/releases/<版本>` 与 `/opt/agi-office/current` 保存代码；`/var/lib/agi-office` 保存业务数据。
- `/etc/agi-office/backend.env` 在服务器生成 JWT 密钥并单独配置原助手的生成模型凭据，权限 0600；不是复制 Mini-Drop 的模型账号。模型值不进入报告或源码包。
- 原助手设置 `AGI_LLM_ALLOW_MOCK=0`；模型调用失败不能回退成“模拟 LLM 回复”冒充成功。
- 2026-09-13 当时使用本地 SQLite 词法检索与真实生成模型，未启用 Milvus/远程 embedding/ES/Kafka/Neo4j；2026-09-23 已接入 Milvus Lite 和远程 Embedding，其余仍不列为已验收。命令沙箱关闭。
- Web 使用 `VITE_API_BASE=/api/office` 和 `vite build --base=/api/office/`，保留原 Vue 界面。Nginx 对这个前缀先用 Mini-Drop HttpOnly 会话验证访问资格，再反代到 `172.17.0.1:18090`，不将 Mini-Drop Cookie/API Key 转交业务后台。
- Diagnosis Worker 通过同路径只读 `/opt/agi-office` 挂载实际发布源码，`MINI_DROP_OFFICE_SOURCE_PATH=/opt/agi-office`；保留绝对符号链接的解析路径，源码映射只是定位参考，运行证据门禁不变。

本批执行脚本在 `output/acceptance/service-integration-20260913/`。第一次启动发现原后台评测目录默认写在代码目录，与只读部署冲突；现用 `AGI_EVAL_TENANT_DIR=/var/lib/agi-office/evaluation-tenants` 配置到独立数据目录。第一次未配置模型的 HTTP 响应保留为失败的真实性检查，不能拿来证明真实问答。

## 接入其他项目

在目标 Linux 主机启动真实服务并运行 Mini-Drop Agent；systemd 部署应保持稳定的 `.service` 名。确认 Agent 快照能看见进程后，在目录登记该服务及 Agent，发布配置。无需把项目代码改写成 Mini-Drop 测试适配器，也不需要让业务服务接受采集命令。需要源码定位时，仅挂载审核过的源代码目录。若服务有多个 worker，先完成明确的实例选择；当前目录不会自动汇总所有 worker 的火焰图。

## 本批验收

Mini-Drop 最终发布 `/opt/mini-drop-releases/20260913T093541Z`（Web、Diagnosis Worker；Analyzer 保持 `20260913T092555Z`，API/Agent/schema 不变）。办公助手代码为 `/opt/agi-office/releases/20260913T092221Z`，systemd 单元持续运行。

- 原后台登录、真实上传、持久化检索及模型问答已调用成功，未认证业务接口返回 401；公网业务入口缺少 Mini-Drop 会话也返回 401。业务回答正确引用人工验收文本中的“三个工作日”。
- 浏览器使用原 Vue 登录表单与知识库开关、聊天输入框完成真实问答；桌面和 390px 页面无横向溢出、无浏览器异常。
- 服务重启前 PID 3260865，重启后 3272177；Agent 新快照自动更新，原上传文档仍可检索。目录中不存放这两个历史 PID。
- 通过“诊断这个后台”创建 [实际会话](https://120.24.187.205/ai-diagnosis?case=insight_929235765e3b482cb0b6ed32bd20426b)。四个任务均绑定 PID 3272177，采集与分析成功：系统指标、perf CPU、连续 perf、py-spy。共 20 条证据记录、4 份报告；记录数量不代表有效支持证据数量。
- py-spy 有 141 个样本；窗口内真实登录和问答交错，栈中有 `AuthService.login`、`ssl_wrap_socket`、`do_handshake`、`do_commit` 等路径。采样比例不能直接当作问答端到端耗时比例。4 份报告均为 `INSUFFICIENT_EVIDENCE`，没有认定 GIL、CPU 热点或依赖为根因，也没有宣称修复故障。
- 有界 HTTP 负载执行 180 次真实知识库问答，180 次响应符合人工测试文本；负载单元已结束，业务后台继续运行。不是容量压测、准确率评估或修复 A/B。
- 首次源码挂载使用不同容器路径，使绝对 current 链接失效；最终发布改为同路径挂载，Worker 已能读到实际 `auth.py` 并映射 `login/search_local`。这项后续修复没有重写上一次报告中的 source_context。
- Mini-Drop Python 全量 545 passed / 5 skipped；Web 全量 39 文件、171 tests，通过后采集器中文名调整的 3 项定向测试通过。原助手禁用 Mock 与流式调用回归 6 项通过。OpenAPI 83 路由与生产构建、bundle 检查通过。跳过的 PostgreSQL 专用测试没有在本批重跑。

完整记录见 [真实后台服务接入验收](../reports/business-acceptance/真实后台服务接入-20260913.md)。

![实际服务入口](assets/learning-guide/20260913-service-integration/connected-service-desktop.png)

![原办公助手真实问答](assets/learning-guide/20260913-service-integration/office-real-chat.png)

![原后台 Python 调用栈](assets/learning-guide/20260913-service-integration/office-backend-python-profile.png)
