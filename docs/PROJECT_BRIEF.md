# Drop 与 AI-CRM 项目技术说明

## 一、Mini-Drop：性能与可靠性诊断平台

### 1. 项目背景与后端架构

Drop 面向 Linux 服务的 CPU、内存、I/O 排查，将节点注册、心跳、进程发现、任务下发、现场采集和异步分析串成完整系统，输出热点函数、火焰图和报告。项目基于腾讯 Mini 校企合作课程题目复刻并扩展，我主要负责 Go 后端编排、测试工具、接口与数据库回归、采集分析验收及性能问题复现。

节点先注册身份和能力，再通过心跳报告在线情况；进程发现提供 PID、启动身份等信息供选择目标。React 通过 HTTP 提交与查询，Go 负责权限和编排，mTLS gRPC 连接 C++ Core/Agent；Agent 主动心跳领任务并上传 RAW，Python Analyzer 后台解析。PostgreSQL 保存任务、状态、事件和制品引用，MinIO 保存原始采样、附件与完整结果树，大文件不通过控制消息传输。

以搜索服务变慢为例：固定业务请求与负载→选择节点和目标进程→创建采集任务→Agent 采样并上传→Core 登记输入与分析 Job→Analyzer 校验、解析、发布→页面展示结果并复测。HTTP 受理、采集完成、分析成功分别有状态；浏览器断开不取消后台任务。

Task 是逻辑诊断，Attempt 是一次现场执行，Artifact 保存大小、SHA、格式与对象键。采集或导入成功登记输入后，为该 Attempt 创建唯一分析 Job，其执行计数标识 analysis run。采集重试新增 Attempt，分析重试复用 RAW；管理员重分析先归档旧终态，再重排同一个 Job。Task Event 记录状态变化，Audit 记录操作身份，Outbox 保存待派发意图。

### 2. 功能与接口回归

任务创建、状态流转和结果查询构成基本回归链。`POST /api/v1/tasks` 顶层传 `target_agent_id`，选项放在 `parameters`；先核写角色和幂等键，再归一化、校验类型及参数，核组、节点 operate 权限、能力、心跳与目标准入。创建响应从 `data.id` 取得任务 ID，再查详情的 `attempts`，按采集序号确定当前轮次，继续关联制品和结果。

幂等键按创建者隔离，对规范化意图算摘要；同键同参复用原任务，同键异参冲突。owner 事务锁内复查幂等和十个活跃采集额度，唯一约束兜底，Task、首个 Attempt、创建事件、审计和 DISPATCH Outbox 一起提交。COLLECTED 后释放采集槽，分析可继续。

派发器以 `FOR UPDATE SKIP LOCKED` 领取 Outbox，RPC 期间持 Outbox 行锁，避免预锁 Core 也要更新的 Task。Core 已提交 QUEUED 而 ACK 丢失时，下轮重读状态并退休旧消息；耗尽前锁 Task、复核当前 Attempt，防止迟到错误覆盖已接受或重试后的状态。取消先提交状态、清理租约与授权，再传播现场；旧执行收尾时仍受当前身份约束。

用例同时断 HTTP 状态、错误码、字段类型、数据库状态与副作用。正常创建检查任务和事件一起出现；拒绝操作比较领域表完整行，防止“返回403但数据已改”，正常限流计数另计。可见性覆盖列表、统计、详情、事件和下载，不能只隐藏按钮。PID 复用场景核 PID、启动 tick、UID 与 namespace，防止编号相同却采到新进程。

OpenAPI 和共享 TaskKind schema 约束请求、响应及参数；静态定义/路由比对与实际 Router 请求验证分开执行。空列表、无采样、解析失败不是同一种状态。并发回归在旧 RPC 返回前停住它，先取消或重试，再放行，断言新 Attempt、事件和结果归属未被污染；`go test -race` 只补充进程内共享内存检查。

### 3. 自动化测试工具与执行门禁

回归按功能风险分层组织：Go 单元验证归一化和领域规则；生产 Router/Service 配合真实临时 PostgreSQL 验证接口、事务、行锁和约束；Python 工具组织协议、安全、性能、报告和整链检查。CI按路径过滤触发预先配置的固定测试组，本地由人按变更影响选择明确命令，不把所有检查混成一套pytest或自动风险选测器。

关键执行保存命令、超时、退出码、用例事件、覆盖收据和失败日志。状态机门禁读取 `go test -json` 的实际 pass/skip/fail，逐个核必测名称；缺失标为 not_run，必测 skip、空事件流或关键函数无覆盖都不通过。安全矩阵也要求指定用例确实执行。退出码0只证明进程正常结束，不能代替执行记录和有效报告。

测试环境显式提供临时数据库、迁移和类型目录，隔离 schema 与数据身份；输入参数、预置对象、请求和预期结果独立组织，避免硬编码共享历史 ID。异步链按本轮身份和截止时间轮询，失败终态立即留证，不用固定 sleep 掩盖问题。报告核数量、状态、来源摘要与字段结构，日志有界且脱敏，不能把原始 profile 或限时授权写入公开报告。

按改动影响选择回归范围：参数规则关联单元与创建接口；生命周期改动关联真实 PG、取消/重试和整链；解析器改动关联格式、边界与结果约束。关键链路不因缺依赖而被默认为成功。Linux/Docker 提供可控环境，Git 固定源码版本，文件摘要绑定报告与实际构建。

### 4. 采集分析链路验收

独立环境验收使用当前构建的 API、Core、Agent、Analyzer 和 Web，准备临时 PostgreSQL/MinIO、受控目标和测试身份，确认节点实际就绪，再执行浏览器或客户端链路。过程包含构建、初始化、登录、发现进程、创建任务、等待采集、分析及回收；环境和对象带本次所有权，异常清理不影响其他环境。

验收先核采集：目标身份、当前 Attempt、文件类型与归属是否正确，实际下载的大小/SHA 是否与登记一致。再核分析：当前 Job/run 是否成功、结果是否引用本轮 RAW、事件是否重复、页面是否加载本轮输出。采集取消后重试新增Attempt、保留旧CANCELED；分析重试则复用RAW并增run。已有普通未插桩CI记录五个成功PROC_CPU任务、二十份制品核验及三类恢复；不把它扩展为全部采集器验收。

Core 校验当前轮次和制品元数据，不下载 RAW 复算 SHA；Analyzer 下载后核实际大小与 SHA，再按 kind/pipeline/format 分派受限解析子进程。租约领取、续租和最终提交绑定当前采集、分析 owner、run 与实时有效 lease；旧 worker 上传成功也不能发布。PG 与对象存储没有共同事务，未发布对象通过维护对账处理。

folded 按完整路径建树，节点满足 `value=self+Σchildren.value`；函数 Top 每条栈先去重同名函数，避免递归重复计入 inclusive。pprof 按选定 sample 列解释单位，HPROF 按强引用支配关系计算独占保活的 retained，大小取序列化 payload 口径，不等于 RSS。公开 PROC_CPU 摘要标识 cpu_ticks，不能把所有输入都解释为函数耗时。

PG 保存有界摘要与对象引用，MinIO 保存完整树；当前失败 run 可回退最多16KiB的部分结果，须核最新 Attempt/RAW 身份，不进入成功比较路径。页面 Profile Worker 核响应、gzip限额与树结构，Layout Worker 算布局；旧请求不能串页。SSE 通知刷新并支持续读，但序号分配不等于事务提交，低序号晚提交仍有限制。

### 5. 性能问题复现与分析

目录十八类包括十六个现场 runner、IMPORT_COLLAPSED 和 COMPOSITE。CPU 包含 PROC_CPU、PERF_CPU、JAVA_CPU、GO_CPU、PYTHON_CPU、GPERF_CPU；内存包含 GO_HEAP、JAVA_HEAP、PYTHON_MEMORY、GPERF_HEAP、JEPROF_HEAP；另有 IO_TRACE、MEMORY_TRACE、RESOURCE_SNAPSHOT、BOLT_PROFILE 与固定模板 SCRIPT。十七类非 COMPOSITE 可离线导入，来源明确标 import，不派发现场 Agent。

方案随目标和问题选择：perf/py-spy/async-profiler 提供相应采样，Go pprof 依赖目标端点，Memray/HPROF/分配器 profile 描述不同内存事实。PROC_CPU RAW 是线程 tick 差值，只到线程级；调用栈采样、分配字节、对象数与 I/O 事件不能混用。采集侧按目标身份、工具配置、argv、deadline、文件大小与并发限制执行，以进程组处理取消和子进程清理。

本机合成HTTP/SQLite/CPU测试按正常→CPU故障→正常恢复执行，用time.process_time独立比较同一测试进程CPU时间，验证注入后升高、撤销后恢复；这条测试不含采样和模型诊断。现场分析另以进程指标确认绝对变化，再用热点函数和火焰图定位；图中占比下降可能只是其他函数变忙，不能单独证明优化。

控制面、采集扰动与业务收益分别测。控制面已有10/25/50个合成mTLS身份、850次心跳测量，50身份组客户端RPC往返P95约71.26ms，未叠加RAW和分析。独立合成HTTP目标的无采样/49/99Hz交错实验读取目标及profiler的procfs CPU tick/RSS，记录请求分位数、吞吐与错误，固定负载、时窗和采样参数并保留收据；它说明采样扰动，不替代CRM业务收益或平台容量。

### 6. 缺陷定位、错误处理与用例改进

分析命令失败时记录退出状态、错误阶段和有界诊断信息，区分工具缺失/执行失败、格式错误、样本无效、资源超限与取消；原始stderr位于临时工作文件，公开日志脱敏。解析同时限制输入、行长、深度、节点数、解压和输出量，不能因命令退出0就登记有效结果。严格 folded 拒绝空帧；可信现场 py-spy 的显式空栈保留为 unknown 权重并标注，不能猜造函数或当成已定位热点。

定位从任务/Attempt、进程身份、RAW SHA、命令输出追到解析和发布记录，保存最早失败输入，再检查修复后状态与错误码。回归既验证损坏、截断、超大、空内容等异常，也保留合法输入对照，防止“统一失败”造成假修复。采集Agent中断、Analyzer领取前暂停、PG短时故障是已记录的恢复范围，不等于全部持租约崩溃场景。

AI 辅助枚举等价类、边界和异常候选，人依据契约独立确认预期与断言，保留复现输入和修复结果。CRM质量扩展将生成限制为有界 Case DSL，由固定执行器调用真实接口，独立 oracle 核响应与数据库效果；sham检查误报、受控变异检查检错能力，这与CRM内部运行评分分开。

其他编排复用普通任务：COMPOSITE 持久化最多十六节点DAG，依赖失败则SKIPPED；允许部分成功且至少一项成功、仅可选项失败时才为PARTIAL_SUCCESS。计划任务用Cron/间隔、IANA时区、行锁与触发点唯一约束防重；恢复处理一次到期触发，再推进未来，不批量补发遗漏周期。

源码：[任务与事务](../api/internal/repository/postgres.go)、[统一CI](../.github/workflows/quality-assurance.yml)、[执行门禁](../scripts/quality/protocol/check_risks.py)、[系统验收](../scripts/quality/system/README.md)、[性能实验](../scripts/quality/performance/README.md)。

## 二、AI-CRM：客户业务与 Agent 执行平台

### 1. 项目背景与 Agent 开发

CRM 面向销售线索管理和客户服务：联系人记录沟通对象，商机表示销售机会，阶段表示推进位置，会话/消息保存沟通，预约与跟进承接后续行动。Agent 查询客户事实、核组织政策、提出建议，并按模式和授权执行业务动作。我主要负责 Agent 模块，包括编排、模型与工具接入、知识、审批恢复及运行评测。

例如核对商机与退费条件：从业务记录取得当前状态，从组织知识取得有效政策，模型整理引用和建议，需要审批的修改或发送先形成提案，获得确认再执行。读到资料、提出建议、排队发送、渠道确认是不同结果。组织隔离覆盖业务数据、上下文、工具、知识和恢复身份。

原TS版使用Next.js/TypeScript与Pi Agent Core；Python迁移版采用React、FastAPI/Pydantic和LangGraph，以下机制以Python为主。配置及发布版本固定模型、工具与知识范围；Run表示一次任务，Job承载后台执行，事件记录过程，回执保存效果，提案保存待审动作。PostgreSQL持久化这些身份与状态，保留操作审计。

### 2. LangGraph / ReAct 任务编排

入站消息验签、去重、持久化后，消费者在事务中核源消息并创建inbound_turn Job，再确认消费。同一联系人作业协调，worker领取绑定locked_by+locked_at，避免旧执行冒充当前领取者。图按 `customer_memory→knowledge→security→control→model` 构造上下文、知识与安全条件，再决定继续、执行工具或交付。

ReAct循环是模型提出结构化工具请求→执行器校验并执行→工具结果进入上下文→模型依据新观察继续。LangGraph节点、状态和条件边负责组织这一过程，工具后按phase回control/model或转delivery。每轮核人工接管、暂停和客户状态；接管后停止后续自动处理，已完成的外部动作不会因此撤回。

步数限制循环次数，token限制文本用量，cost限制累计调用消耗。调用返回后累计并检查预算，超限阻止后续，不能回滚已经发生的费用。同一工具名加排序后参数形成失败指纹，累计五次失败报agent_repeated_tool_failure，避免相同错误无限循环。工具错误作为观察回填，不把调用正常返回等同于业务成功。

客户记忆以本地确认事实为源，可选Mem0排序结果还要回核本地；当前业务状态经工具读取，组织知识提供政策，三者不互相替代。客户外发由显式发送工具创建，普通assistant文本不直接发送。automatic按入站策略运行，assisted形成待审草稿；工作台mode为inspect/act，评测禁写是另设的执行上下文。

### 3. 知识检索与 Skill 复用

支持文字型PDF、UTF-8/BOM Markdown、CSV和文本。PDF提取页面文字，拒绝加密、损坏或无文字材料；扫描件不含OCR。CSV经csv.reader读取并转文本，不等同Excel或复杂表格版面还原。正文归一化后默认2000字符分块、重叠240，优先段落边界，避免条件和例外被完全割裂。

每块保存organization/source/kb_version_id、位置和content_hash，生成1536维embedding并写PostgreSQL。pgvector按问题向量检索相近片段；当前SQL定义IVFFlat余弦索引、lists=100，实际索引命中与延迟看执行计划。相关度不是事实真值，Top-k召回需独立标注集或精确检索基线。

索引building时旧ready版本继续服务，新版本完成后激活。worker传pool时指针与active标记事务切换，HTTP-only路径仍顺序更新。商品更新复制旧活动版本中其他商品chunks/向量，只重建本次商品。检索按当前配置，或授权选定的同组织已发布Agent版本选择知识源，核ready/active，再返回sourceId、revision、定位和excerpt。可选WeKnora按组织绑定。

Skill保存业务处理规程，先给索引，matcher按场景加载正文，再读取当前激活manifest允许的references，核路径、大小和SHA。RAG提供事实材料，Skill提供处理方法；文档内容是不可信输入，不能改变角色、组织或工具权限。未检索、检索失败、空证据和版本失效分别留状态，材料不足时不得伪造依据。

### 4. MCP 工具接入与权限

工具目录定义名称、描述、Pydantic Schema、角色、operation及read/write scope。运行按已发布版本、业务范围和模式筛工具，模型生成arguments，执行层重新检查类型、必填与范围，再核组织归属、角色、业务状态和副作用策略。客户查询、商机调整、预约与消息排队复用原业务处理器及校验，避免新增绕过规则的写入通道。

联系人查询可输入 `{"query":"测试客户","limit":2}`，返回contacts、cursor、has_more；后续操作使用实际取得的对象ID，再核归属。RBAC约束角色能做什么，RLS约束普通数据库访问能接触哪些行；组织来自可信JWT/服务端上下文，后台特权查询仍显式过滤organization_id，不相信模型自报组织。

外部MCP在HTTP上处理JSON-RPC initialize、tools/list、tools/call，内部Agent直接复用execute_tool。协议错误与工具业务错误分开，HTTP200不代表完成，工具失败需识别isError/业务错误结构。TS内部包装存在审计失败但返回普通{error}的路径，不能只看函数正常返回计为成功。

inspect及evaluation上下文禁止业务写入，评测不向真实客户发消息；运行仍记录工具请求、拒绝原因和审计。发送工具排队只是登记发送意图，不是实际送达证明。权限在执行时重验，不能仅依靠开始时筛过一次目录。

### 5. 人工确认、Checkpoint 与执行回执

工具效果分read/reversible_write/external/irreversible，审批要求随运行模式及动作类型确定。工作台act的外部或不可逆动作形成固定工具、参数与目标的提案；批准时锁Run/提案，核当前状态和版本、记录批准人，取得执行权后执行、存回执并创建续跑Job。不是所有商机修改都必须人工批准，也不是批准一次便放行所有后续动作。

review节点通过interrupt挂起，重入读取持久化决定；`Command(resume)`只唤醒，不提供批准凭据。可逆商机修改限制字段，保存旧值和写后版本，用expected_updated_at作条件更新；撤销时若人工已继续修改，不能覆盖新值。已外发消息无法通过数据库回滚收回。

Checkpoint保存图进度与上下文，效果回执分别记模型/工具的started/completed。completed可复用，started无明确完成表示外部动作可能已发生，进入核查而非自动重复付费、修改或发送。恢复适配器绑定组织、Run、发布版本与已领取Job，拒绝旧worker、取消或匿名化后的上下文复活。

put_writes可能先于checkpoint主体到达，先持久化pending_writes，主体到达后合并去重；后台写入排空后再压缩。出站消息保存排队、sending、确认和失败；按created_at巡检超过五分钟仍sending的记录，分批标failed/send_timeout、写事件和告警，不自动重发。迟到ACK与巡检以行锁协调，保护已确认状态。

### 6. 智能体评测与 pytest 回归

评测结合任务目标、实际观察、知识引用、字段断言、工具回执、审批事件和数据库效果。要核客户/商机身份、金额状态、政策版本与引用；模型自述完成不能代替业务记录。确定性检查发现越权、缺审批、字段不符、开始无结束等问题；合法工具是否符合任务语义仍需语义复核。

pytest通过TestClient/HTTPX检查FastAPI路由或服务，参数化组织有效、无效和边界输入；Fixture准备合成身份、客户与MemoryRepository状态，Mock固定模型返回、工具故障及checkpoint写失败。已有用例覆盖越权、禁写评测、重复执行、旧worker、撤权、超时、知识缺失、预算、审批恢复和未知付费结果，核响应、调用次数、事件、审计及仓储变化。这组测试不证明真实PG/RLS；数据库集成另由独立脚本调用原生verify函数检查。

代表性回归断言由多个分层用例承担：审批恢复只执行原提案；checkpoint写入失败但模型回执已提交时，重跑模型调用次数仍为1；未知付费响应再次进入核查而不二次调用；补偿遇人工更新不能覆盖新值。知识用例另核引用与版本，不能把这些用例合称为一次真实客户渠道整链验收。UI另保留联系人、看板并发更新和队列旅程。

当前profile revision 8相对权重为任务完成25、答案质量20、知识证据25、工具可靠性20、策略合规20、效率10、协作10，总和130。pass/needs_review/fail计100/50/0，not_run不入分母；未终态为not_run，终态required项fail则整体fail。语义裁判按材料与rubric补充复核，只能降低分数或加重结论，不能覆盖安全失败；缓存绑定材料指纹、模型和规则版本。

失败沿输入→路由→上下文→检索→工具→审批→输出→评测找最早偏离点，固定Run与模型/工具/知识版本，保存复现材料和回归结果。AI生成的是候选用例与排查思路，预期由需求、授权及独立业务事实确认，不能让生成者同时自报通过。

源码：[执行图](../../crm/AiNativeCrm-python/crm/agent/turn_graph.py)、[知识处理](../../crm/AiNativeCrm-python/crm/agent/knowledge.py)、[工具](../../crm/AiNativeCrm-python/crm/agent/tools.py)、[审批恢复](../../crm/AiNativeCrm-python/crm/agent/workbench_graph.py)、[评测](../../crm/AiNativeCrm-python/crm/services/run_evaluation_checks.py)、[恢复回归](../../crm/AiNativeCrm-python/tests/test_runtime_langgraph.py)、[数据库集成](../../crm/AiNativeCrm-python/scripts/rest_database_checks.py)。
