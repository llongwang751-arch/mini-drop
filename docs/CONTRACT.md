# 实现契约 v2

2026-10-21增量：SQL009增加 `diagnosis_runs.knowledge_mode` 与个人 `diagnostic_cases`。新增诊断方法目录、案例保存/查询/撤销四个HTTP操作；诊断工具增加 `read_diagnostic_knowledge`，报告可含程序生成的knowledge。模式off/skills/experience参与幂等及来源摘要，旧行off兼容原摘要。知识不授予权限、不修改采集契约或Protobuf编号；每项假设必须引用本轮测量观测。详见 [知识层说明](DIAGNOSTIC_KNOWLEDGE.md) 与OpenAPI。

日期：2026-10-16。`contracts/drop.proto`、`contracts/task-kinds.json`、`db/migrations/*.sql` 为共享契约，修改先协调对应模块，保留既有 Protobuf 字段编号。本文描述当前实现接口，完整 HTTP Schema 见 [openapi.yaml](openapi.yaml)，不将预发布能力当作生产已验收结果。

## 模块和环境

- core/：C++20 gRPC drop_server/drop_agent，libpq，curl，OpenSSL，nlohmann-json。Server :50051，仅内网；mTLS，证书 CN=drop-api / drop-agent-本机agentid。Agent ID=aliyun-local。Server读 `DATABASE_URL`、`TLS_CA_FILE`、`TLS_CERT_FILE`、`TLS_KEY_FILE`；Agent另读 `DROP_SERVER_ADDRESS`、`AGENT_ID`、`AGENT_HOSTNAME`、`WORK_DIR`。
- api/：Go Gin/GORM/minio-go/gRPC，模块 `drop/api`；HTTP :8080。`DATABASE_URL`, `DROP_SERVER_ADDRESS=drop-server:50051`, `TLS_*_FILE`, `S3_ENDPOINT=minio:9000`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET=drop-artifacts`, `PUBLIC_URL`、`ADMIN_USERNAME`、`ADMIN_PASSWORD`。优先支持 `_FILE` secret读取。
- analyzer/：Python3.12 psycopg/minio；同DATABASE_URL/S3环境变量；`WORKER_ID`、lease 60s，续租10s，并发1。
- web/：React+TypeScript+Vite，浏览器同源 /api/v1；Nginx静态资源及反向代理API/SSE/objects路径。
- PostgreSQL17.11 + MinIO + nginx1.30.6。当前公网开放80重定向和443 HTTPS（SSH22保留）；postgres/minio/gRPC无宿主映射。监控管理端口仅loopback。MinIO backend在独立internal网络，控制面经保护网关访问。

`PYTHON_MEMORY` 现场附加要求目标Python环境内可导入兼容的memray模块；Agent安装工具不能替代目标运行环境准备。还需目标信号权限：helper 与目标 UID 不同时，GDB 停止/恢复线程需要 `CAP_KILL`。Runner 在开始附加前以 `kill(pid, 0)` 检查权限，失败返回 `PROFILER_PERMISSION_DENIED`；不向目标发送实际信号。受控 Compose 将该 capability 仅授予共享 Agent 容器 PID namespace 的 helper，Agent 保持 UID10001、全部 capability 移除；不开放 host PID 或长期存储凭据。可选 systemd/Kubernetes 宿主采集方案仍须按目标范围单独配置此权限，不能直接套用容器验证。memray 冷启动版本检查有界至15秒，实际附加、采集、收尾和目标身份栅栏独立保留。

## 状态与采集

状态值字符串，见SQL CHECK。kind字符串来自task-kinds.json。ID REST JSON统一字符串（保留浏览器精度）。所有时间ISO8601 UTC，界面Asia/Shanghai。

TaskAttempt是一次执行。手动重试同一Task新增attempt，旧制品事件保留。当前attempt为MAX(attempt)。任务取消后晚到NotifyResult不得覆盖；AnalysisJob只接受该任务当前attempt且collection=COLLECTED。所有变更写task_events。

API创建 task + attempt + event + outbox同事务。outbox dispatcher生成raw对象key与600秒PUT URL（每次dispatch刷新），写attempt.raw_upload_url，再gRPC CreateTask。Server幂等将CREATED→QUEUED，不创建Task。Server心跳从DB持久化任务里取QUEUED并标DELIVERED；任务描述从DB读取attempt、params、presigned URL，1台Agent最大1并发。

Server Progress接收RUNNING/UPLOADING（合法单向转换），NotifyResult成功事务写RAW Artifact、COLLECTED、attempt终态、pending AnalysisJob与event。RAW名称、内容类型、格式及分析pipeline来自TaskKind真源，不把所有输入当作folded；key为raw/tasks/{taskid}/attempts/{attempt}/{raw_name}。SHA256小写64位。BINARY附件的短期PUT目标保存在attempt.upload_targets；Agent仍不持有长期对象存储凭据。

支持采集manifest的Agent在注册labels声明`collection_manifest_version=1`。仅在该协商成立时Server下发并要求`MANIFEST/collection_manifest_v1`，固定名`collection-manifest.json`、Content-Type application/json、最大65536字节，key为`manifest/tasks/{taskid}/attempts/{attempt}/collection-manifest.json`；旧Agent不会因为不认识新增制品而失去兼容。manifest在数据制品PUT成功后生成一次，记录实际起止、版本、去敏参数、object key、size、SHA256及不作内容hash解释的opaque ETag；不包含URL、凭据、目标文件路径或原始stderr。恢复沿用已持久化的同一份字节，不重新采集或递归记录自身hash。采集MANIFEST与Analyzer结果manifest是独立制品，不能互相覆盖。

Agent perf record实际采样→perf script解析为folded；PROC_CPU每线程/proc CPU tick差→ `process;thread-name`计数，结果和建议须明确仅线程级。绝不把procfs数据宣称perf采样。内置CPU workload可作为pid供验证，进程列表在resources.processes里仅提供可采样的受控进程。不使用shell。

离线导入：17种非COMPOSITE类型均可使用通用import文件；IMPORT_COLLAPSED另保留collapsed_text（最多2MiB）。API上传后同事务写RAW/附件、COLLECTED、分析job和event，不dispatch Agent。通用文件导入先于旧文本分支校验，因此IMPORT_COLLAPSED也可直接导入文件。所有导入保存params.source=import，分析摘要source=import，现场时长/频率为null，不把文件内部时序当作平台现场采集承诺。明确标识的示例由用户主动导入，不预置假任务。

## REST契约

全部业务路径以`/api/v1`为前缀；以下省略前缀。响应`{request_id,data,error:null}`，错误`{request_id,data:null,error:{code,message,retryable,details:{}}}`。health/metrics、OIDC重定向、SSE除外。REST bigint ID及Event.sequence均为十进制字符串，时间为ISO8601 UTC。严格JSON绑定拒绝未知结构字段；options、规则config及结果sections按各自契约处理。普通HTTP体最多4MiB，POST /tasks最多64MiB。

列表data为`{items:[],total,page,page_size}`。Tasks/Agents/audit-logs实现page/page_size（默认1/20，上限100）；Task过滤search/status/kind/agent_id，status匹配collection或analysis状态。Agent先取最多500条授权资源再过滤/分页。其他List接口的total可能只是当前有界批次条数，不能当作完整历史总数；具体上限见OpenAPI。Groups/Schedules当前返回全部授权行、没有数据库分页限制，如实记录此边界。

身份与会话：

- POST `/auth/login {username,password}` →`{user:{id,username,display_name,role,created_at},csrf_token}`，设置12小时HttpOnly SameSite=Lax Cookie；HTTPS部署设置Secure。GET `/auth/me`同响应；POST `/auth/logout`撤销当前会话。
- 所有已认证写接口需`X-CSRF-Token`，包括logout、反馈与客户端遥测。登录检查Origin；登录10次/IP/15分钟、认证写60次/用户/分钟均使用PostgreSQL共享固定窗口，跨API副本共享，数据库不可用时拒绝放行。owner并发采集配额通过事务锁保证，最多10个活动采集任务。
- GET `/auth/providers`公开返回`{local:true,oidc:{enabled,name},ai:{enabled,name}}`，没有issuer/endpoint/DSN/密钥。GET `/auth/oidc/start`和`/auth/oidc/callback?state&code`为302浏览器跳转；真正实现discovery/JWKS签名、issuer/audience/azp/expiry、浏览器绑定单次state、nonce与PKCE。未配置IdP时503 OIDC_UNAVAILABLE，企业IdP接入另行验收。首次OIDC身份默认viewer，不按相同email自动合并用户。
- GET/POST `/users`及PATCH `/users/:id {role?,display_name?,password?}`仅平台管理员；密码12..72字节、bcrypt cost12，改密码撤销该用户会话。事务锁保护最后一个平台管理员。

统一授权：

| 主体或范围 | 读取 | 操作与管理 |
| --- | --- | --- |
| viewer | 仅自己/组可见/显式share所授权资源 | 不创建、取消、重试或管理；可对已授权任务提交建议反馈 |
| operator、group_admin | 仍按资源范围过滤；全局group_admin并不自动拥有所有组 | 创建需Agent operate和目标组operator/group_admin权限；仅相应组的成员group_admin可管理本组 |
| platform_admin、兼容admin | 全平台未软删除资源 | 平台用户/规则/Agent启停/管理员重分析；其余生命周期限制仍适用 |
| Task owner | 自己未软删除任务 | 有写角色时cancel/retry/share/delete，终态和配额等限制独立适用 |
| visibility=group | 同组成员可读 | 同组operator/group_admin加全局写角色可operate；组管理员可管理任务共享 |
| 显式Task share | read覆盖详情、result、suggestions、events/SSE、artifact | operate额外允许cancel/retry，不能仅凭share管理共享或软删 |
| Agent | group_agents授权对应组成员；平台管理员可读全部 | operate还要求组成员operator/group_admin和全局写角色 |

Task/Schedule/Composite均按owner、group+visibility、平台管理员过滤，Task另支持显式share；所有Task读取面共用同一授权。越权读取通常404，避免资源探测。SSE每轮重新检查当前会话、角色和Task权限。Task详情`permissions:{read,operate,share,delete,reanalyze}`由同一授权service计算，UI仍需遵守状态限制。

资源和任务：

- GET `/overview` →`{agents_online,agents_total,tasks_total,tasks_running,tasks_success,success_rate,trend:[{time,total,success}],recent_tasks,collection_counts,analysis_counts,storage_bytes}`；均为授权范围真实SQL。success_rate为0..100百分数。tasks_running包括采集中及COLLECTED后分析PENDING/RUNNING/RETRY，排除采集取消/失败。
- GET `/agents`、`/agents/:id`返回`{id,hostname,ip,version,platform,status,capabilities,capability_details,labels,resources,cpu_percent,memory_percent,load_average,last_seen_at,created_at}`。labels.registration是含protocol/build/os/arch/kernel/tools/cert到期时间的对象，不假定所有label值为字符串。GET `/agents/:id/metrics`返回`{items}`，最近一小时至多720条；GET `/agents/:id/events`为最新100条List。
- PATCH `/agents/:id {enabled:boolean}`平台管理员，启停与audit同事务；disabled不能由注册/心跳重新启用或领取任务。GET `/agents/:id/diagnostics?category=resources|capabilities|processes|events`走4秒有界mTLS FetchData。processes/events最多100条数组，resources/capabilities为持久化有界对象；HTTP当前未透传Core的generated_at/truncated，不宣称完整进程清单。
- GET `/task-kinds`返回共享JSON全部18项，包括专用COMPOSITE；17种非COMPOSITE支持离线导入，16种有Agent runner。`?agent_id`仅在授权后标注available，不隐藏离线导入类型。动态读取parameter_schema、supported_targets、input_formats、auxiliary_artifacts，不复制选项规则。COMPOSITE通过专用路径创建。
- POST `/tasks {name?,kind,target_agent_id?,group_id?,visibility?,parameters?,collapsed_text?,import?}`，必需`Idempotency-Key`（8..128字符，无CR/LF/NUL），201新建/200重放，同键不同规范化参数409。parameters含旧`pid/duration_seconds/frequency_hz/scope`及`target/budget/options`；时长/频率0或缺省按TaskKind默认。source/traceparent由服务器注入，不接受用户非空值。
- target为`{type:'process'|'host'|'container',pid?,start_time_ticks?,container_id?,runtime?:'docker'|'containerd',container_pid?,expected_uid?,check_uid?}`，budget含`max_file_bytes/max_address_space_bytes/max_cpu_seconds/max_processes/max_concurrency`；非零值与options须满足所选TaskKind Schema，不能提交任意argv/脚本/URL。`max_processes`约束采集工具所属进程组内的并发task（含线程），以50ms轮询、每轮最多32768个PID核算；超限或核算失败终止该组并返回`RUNNER_PROCESS_LIMIT`。不以跨容器共享UID的`RLIMIT_NPROC`代替每任务预算，目标和其他同UID进程不计入；部署的Agent/helper `pids_limit`分别64/96提供容器级硬上限，轮询不是瞬时每任务硬隔离。Agent策略canonical键为`host_processes/containers/host_resources/allowed_uids/allowed_container_ids`。容器须精确命中白名单；未在有界snapshot列出的PID，需固定允许UID并由Agent真实OS身份复核，不能把枚举截断当越权。
- 通用import为`{filename,content_type?,data_base64,format?,auxiliary?:[{filename,content_type?,data_base64,format?}]}`，严格base64且主文件+附件合计1字节..32MiB，basename不可含路径或控制符。format缺省TaskKind.input_format，只允许input_formats；BOLT必须附target.elf，GPERF/JEPROF可选。对象content_type由服务器推导，客户端声明不会覆盖。import不得混用非空target_agent_id/collapsed_text，服务端清除现场PID/目标/时长/频率。`IMPORT_COLLAPSED`仍可用旧collapsed_text，1字节..2MiB。
- GET `/tasks`返回Task List。Task核心字段`{id,name,kind,target_agent_id,params,collection_status,analysis_status,creator_id,group_id,visibility,request_id,version,collection_deadline?,error_code?,error_message?,created_at,updated_at}`。GET `/tasks/:id`加permissions、attempts/artifacts/events/suggestions及成功时result。
- POST `/tasks/:id/cancel`取消当前执行；POST `/tasks/:id/retry`需新Idempotency-Key，为终态任务创建新采集attempt，旧取消不能影响新attempt；导入任务须重新导入。DELETE `/tasks/:id`仅终态任务管理者软删除，历史/制品及原保留期不变。POST `/tasks/:id/reanalyze`仅平台管理员，COLLECTED且分析success/failed、RAW可读未过期；归档旧run/result/suggestions，清当前result再入队，run编号递增，拒绝覆盖pending/running/retry租约。
- GET `/tasks/:id/events?after=sequence`取至多500条持久事件；GET `/tasks/:id/events/stream`使用`id:sequence,event:task_status,data:Event`，Last-Event-ID续读、每秒轮询、15秒keepalive。终态后仍可接收AI事件。Event.sequence JSON是字符串；事件JSON的type字段与SSE transport event名不同。
- GET `/tasks/:id/result`为当前attempt成功job的真实JSON，未就绪409。GET `/tasks/:id/suggestions`仅当前attempt且未superseded建议。GET `/tasks/:id/artifacts`为元数据List；GET `/tasks/:id/artifacts/:artifact_id/download`逐次授权/审计并返回`{url,expires_at}`，短URL最多300秒或剩余保留期，过期410。Artifact额外含format/metadata/expires_at/retention_class/download_allowed；RAW14天、INTERMEDIATE3天、LOG14天、RESULT/MANIFEST/BINARY90天。真实删除/孤儿/缺失/hash/multipart对账由maintenance负责。

部分分析结果：

- 下列16KiB限制针对紧凑UTF-8 `data`正文，标准`request_id/data/error` envelope另留1024字节；partial接口以JSON直接编码`< > &`，浏览器仍仅作React转义文本渲染，不把符号当HTML。

- GET `/tasks/:id/partial-result`沿用Task read授权，仅Task为`collection_status=COLLECTED`、`analysis_status=FAILED`，且当前failed job、最新采集attempt、当前analysis run、最新ready RAW id/SHA与检查点全部吻合时返回；没有可用当前检查点返回409 `PARTIAL_RESULT_NOT_READY`。`/result`仍只返回成功分析，partial不进入AI或Compare成功路径。
- 正文遵循[`contracts/partial-result.schema.json`](../contracts/partial-result.schema.json)：`schema_version:1,completeness:'partial',task_id,attempt_id,analysis_job_id,analysis_run,input_artifact_id,input_digest,kind,completed_stages,missing_stages,summary,top_functions`，不允许额外键。ID为正int64十进制字符串；run为1..2147483647；总UTF-8 JSON≤16KiB、深度≤6；数值finite、非负且JS安全，unique_stacks/node_count≤100000、max_depth≤128、百分比≤100。Top K≤20，name最多160个UTF-8字节且无Unicode类别C字符，仅任务授权用户可见并作为转义文本；symbol_sha256对应原完整符号。source使用契约枚举，unit为`^[A-Za-z0-9_.-]{1,32}$`，producer将未知来源/不合单位语法映射unknown。示例为明确synthetic导入fixture：[`partial-result-v1.json`](../contracts/fixtures/partial-result-v1.json)。
- `completed_stages`固定`['input_verified','parsed']`，`missing_stages`固定`['diagnostic_rules','result_artifacts','final_commit']`；后者指这份快照未包含的成果，不断言后续阶段从未执行。只保留校验完成的摘要、Top K和计数，不复制完整flamegraph、raw/collector metadata、原tool report、内部工作路径、对象key/URL或建议正文。
- Task详情可选`analysis_identity:{analysis_job_id,analysis_run,input_artifact_id,input_digest}`锚定当前job/run与最新采集输入，浏览器同时绑定Task.version并校验全部标识；缺identity不读取partial。检查点沿用`analysis_jobs.result` JSONB，由当前owner/run/未过期lease及输入绑定原子写入；新claim清空，失败可保留本run，成功完整结果覆盖。手动reanalyze原有run归档和清空继续生效，绝不回查历史run作当前partial。inline检查点不是INTERMEDIATE对象，不假称已有三天对象删除策略；无新增SQL migration。

组和共享：

- GET/POST `/groups`；创建`{name,description?}`仅平台管理员，响应`{id,name,description,created_at}`。GET `/groups/:id`加`members:[{user_id,username,display_name,role}]`和`agents:[{agent_id,permission}]`。
- PUT `/groups/:id/members/:user_id {role:'viewer'|'operator'|'group_admin'}`及DELETE由本组管理员/平台管理员；PUT `/groups/:id/agents/:agent_id {permission:'read'|'operate'}`及DELETE同理，组管理员只能委派自己已拥有的Agent权限，不能read升operate。
- GET/POST `/tasks/:id/shares`及DELETE `/tasks/:id/shares/:share_id`仅任务管理者；创建`{user_id?:string,group_id?:string,permission:'read'|'operate'}`且主体二选一。每task/principal唯一，重复创建不是upsert。

组合任务和计划：

- GET/POST `/composites`、GET `/composites/:id`、POST `/composites/:id/cancel`。创建需Idempotency-Key，body为`{name,group_id?,visibility?,deadline_seconds:1..3600,allow_partial_success?,nodes:[{key,required?,depends_on?,task:CreateTask}]}`。1..16节点、只允许现场采集、拒绝环/重复/缺失依赖和嵌套；required缺省false，组范围覆盖子模板。
- 创建/列表返回run：`{id,name,creator_id,group_id,visibility,status,allow_partial_success,deadline_at,summary,created_at,updated_at}`。创建summary初始可能为`{}`，详情增nodes/report。节点`{id,run_id,key,required,depends_on,status,task_id?,error_code?,task?,result?,created_at,updated_at}`；result仅有界摘要。report含`results:[{node_key,task_id,summary}],suggestions:[{node_key,task_id,suggestion}],limitations:[]`，不同采样器权重不求和。
- DAG依赖失败令后续SKIPPED；必选失败则整体FAILED。允许partial且仅可选失败、至少一项成功时PARTIAL_SUCCESS。整体deadline独立于慢RPC派发，超时取消未完成子attempt并TIMED_OUT；状态另含PENDING/RUNNING/SUCCESS/CANCELED。全部定义/节点/状态持久化，重建service可恢复。
- GET/POST `/schedules`；创建`{name?,cron?,interval_minutes?,timezone?,group_id?,visibility?,template:CreateTask}`。标准五段Cron和1..10080分钟间隔择一；Cron模式interval缺省/0，间隔模式cron缺省/空。默认Asia/Shanghai、支持完整IANA时区；DST缺失时刻跳过，重复小时按实际两个时刻触发。模板不可变，不允许导入。
- PATCH `/schedules/:id {enabled}`、DELETE同路径、GET `/schedules/:id/records`（最新100）。响应Schedule含cron/group/visibility，Cron行兼容interval_minutes=1不代表实际每分钟执行。记录含template_snapshot，行锁+唯一(schedule_id,scheduled_at)+子任务幂等防重复；错过时点只推进到未来，不集中补发。

规则、模型与反馈：

- GET `/rules`登录可读List，当前key为allocation/lock/gc。PATCH `/rules/:key {enabled?,severity?,min_samples?,threshold?,config?}`仅平台管理员；severity为info/warning/critical，min_samples≤1e12、threshold为0..100百分比、config编码≤8192字节；更新版本和audit同事务。
- POST `/tasks/:id/suggestions/ai {consent:true}`需Task operate和已成功结果，返回202持久AIRequest`{id,task_id,attempt_id,requested_by,input_digest,status,error_code?,created_at,updated_at}`，按task/attempt/digest去重。GET `/tasks/:id/suggestions/requests`为最新50条List，支持刷新后恢复。未配置返回503 AI_UNAVAILABLE。
- 只外发数值和Top20哈希符号摘要，无raw、路径、PID或身份；stream:true按SSE增量解析并净化纯文本，上游30秒/256KiB/256块/最终8KiB，保存每块前检查当前Task/attempt/授权/AI lease。持久事件`SUGGESTION_DELTA {text,ai_request_id,streamed:true}`及SUGGESTION_CREATED/FAILED；非流回退明确streamed=false、不伪造增量。完成更新Task.updated_at。Suggestion保留engine/version/input_digest/evidence，AI未校准confidence=0并注明限制，不能冒充实测结论。
- POST `/tasks/:id/suggestions/:suggestion_id/feedback {rating:'useful'|'not_useful',comment?}`按用户/建议upsert，comment最多1000字节；GET同路径返回最新100条反馈。Task read授权即可，viewer可反馈；不允许unsafe HTML渲染建议。

运维：GET `/audit-logs`仅平台管理员且分页，runtime审计权限为INSERT/SELECT；POST `/client-errors {code:'FLAME_LAYOUT_FAILED'|'WEB_RENDER_FAILED'}`登录+CSRF+共享限流，只接受固定code，不接收message/path/raw/stack。GET `/health/live`、`/health/ready`、`/metrics`均在业务前缀外，metrics由部署限制内网访问。HTTP/gRPC直方图、实际SQL队列/Agent/租约/保留期指标、OTLP HTTP exporter和TraceContext传播已实现，不将未配置collector视为已上报。

主要HTTP错误：400输入/角色/DAG无效；401未登录/凭据错；403权限/CSRF/目标策略拒绝；404不存在或不可见；409状态/幂等冲突/结果未就绪；410制品过期；429共享限流或配额；503Agent/能力/对象存储/身份源/模型不可用；500内部异常。日志不输出Cookie、密码、密钥、签名URL或原始profile。

配置：除数据库/S3/TLS外，OIDC_ISSUER/OIDC_CLIENT_ID/OIDC_CLIENT_SECRET[_FILE]/OIDC_REDIRECT_URL，AI_ENDPOINT/AI_API_KEY[_FILE]/AI_MODEL，OTEL_EXPORTER_OTLP_ENDPOINT（HTTP根路径自动补/v1/traces）。OIDC_ALLOW_HTTP/AI_ALLOW_HTTP只在明确受控测试启用、默认false。migration命令顺序执行全部SQL，当前API要求schema1至8完整存在；缺失任何一项拒绝启动。可选CODE_EXECUTOR_SOCKET为绝对Unix socket路径。实际外部能力及部署验收以证据文档为准。

契约检查：`python api/tools/sync_openapi.py`生成参考；`python api/tools/check_http_contract.py`核对全部61个Gin路由、75个Schema、CSRF/幂等、公共DTO字段、18项共享参数Schema及边界fixture；安装openapi-spec-validator后加`--spec-validator`执行OpenAPI3.1结构校验。静态校验不替代真实HTTP/PG/对象存储集成。

## 分析结果

analysis_jobs.result 保存有界结果JSON（完整树另存对象存储）：

```
{ "schema_version":1,"kind":"PERF_CPU","total_samples":123,"unique_stacks":4,"node_count":12,"max_depth":5,
  "flamegraph":{"name":"all","value":123,"self":0,"children":[...]},
  "top_functions":[{"name":"foo","self":20,"total":100,"self_percent":16.2,"total_percent":81.3}],
  "summary":{"duration_seconds":10,"frequency_hz":99,"unit":"samples","source":"perf","limitations":[]},
  "suggestions":[{"title":"...","content":"...","severity":"warning","confidence":0.9,"evidence":{...},"engine":"rules","version":"1.0.0"}]
}
```

flamegraph.value为inclusive，self为自身，children之和+self=value。TopN递归同名函数inclusive每栈只记一次。JSON最多100k节点/128深，原始最多32MiB，浏览器Worker布局。worker所有结果上传成功后带lease_owner条件事务更新job/Task、result Artifact、suggestions和event。对象预签名不写result公共JSON。

## Java堆采集附加方式（2026-10-19）

`JAVA_HEAP`使用清单固定并校验SHA的asprof原生jattach诊断命令执行`GC.heap_dump`，`live=false`才增加`-all`，产物仍为真实HPROF，类型/字段/分析流水线不变。该方式支持同一PID namespace、不同mount namespace的受控同UID JVM；不需要SYS_ADMIN或加入目标mount namespace。工具要求与runner名称同步在TaskKind真源更新，API读取同一只读catalog；不保留指向旧jcmd要求的API目录。诊断命令的输出路径仅接受绝对路径中的ASCII字母、数字、`/._-`且最多4096字符；拒绝空白、引号、换行或反斜杠，调用方不能拼入其他JVM命令。目标仍须显式授权、同UID，并通过PID/starttime/namespace身份复核。


## 诊断 Agent 增量契约（2026-10-20，SQL006）

- POST /tasks/:id/diagnoses：完整成功结果及 operate 权限；严格输入 goal（trim后1–600字符）、mode（evidence/model）、consent。模型模式必须 consent=true。Idempotency-Key 必填，按请求用户去重，不同输入冲突409；最多3个活跃诊断。异步202。
- GET /tasks/:id/diagnoses：read 权限，最新20条；所有 bigint 标识均为十进制字符串。记录绑定 task/attempt、analysis_job_id/analysis_run 和完整结果哈希，包含状态、工具 trace、结构化 report 或固定 error_code，不含租约凭据、上游原文、隐藏思维链。
- POST /tasks/:id/diagnoses/:run_id/cancel：operate 权限；pending/running 原子转 failed/DIAGNOSIS_CANCELED，写事件与审计。已终态409，过期 worker 不能提交。
- POST /tasks/:id/diagnoses/:run_id/collect：operate 权限、confirm=true、Idempotency-Key；仅成功且当前的诊断草案。锁定来源任务，在同一事务复核证据、复用原目标及正常采集校验、创建新任务与 DIAGNOSIS_VERIFICATION_CREATED/审计。首次201，幂等重放200。导入结果不自动采集。
- 每次执行最多4轮工具交互、8次工具；最后报告未通过校验时可额外1次仅修正报告的模型请求，不提供工具、仍须通过原校验，总预算150秒，HTTP30秒/64KiB。180秒租约，崩溃最多3次执行。任务/分析版本、输入摘要或授权变化禁止后续工具及最终提交。失败不改原任务的采集/分析状态。故障恢复可能重复只读模型调用。
- DIAGNOSIS_QUEUED、DIAGNOSIS_TOOL_COMPLETED、DIAGNOSIS_COMPLETED、DIAGNOSIS_FAILED、DIAGNOSIS_VERIFICATION_CREATED 都使用既有 task_events/SSE，不修改 protobuf 字段号或 TaskKind 清单。
- 模型只接收问题文本与数值/哈希符号/本地枚举线索。工具不能指定 task、PID、路径、URL、SQL或命令。复测时长/频率仍受共享 schema 限制；模型输出的证据引用必须指向实际工具观测。具体数据边界见 [DIAGNOSIS_AGENT](DIAGNOSIS_AGENT.md)。

## 调查与源码验证增量契约（SQL007、008）

- `POST /investigations`：goal、target_agent_id、parameters、consent=true、Idempotency-Key。权限与实时资源快照绑定PID/启动时间/UID，模型不能改变目标。每用户最多3个未终态调查。计划器最多6轮、10次工具、150秒，从实际TaskKind目录选择参数；错误作为观测回填。生成计划不采集。
- `GET /investigations`、`GET /investigations/:id`：本人加目标operate权限，管理员可见。关联源码的历史调查仍要求管理员，角色降级后不能通过历史报告读取源码。列表最多30条。
- `POST /investigations/:id/confirm {confirm:true}`：确认原采集和最多一次受控复测/自动模型诊断；各次采集最多60秒。`cancel`取消调查及关联未完成任务，并限制迟到写入。流程最多20分钟。
- `POST /tasks/:id/diagnoses/:run_id/collect` 同事务新建 verification_runs；`GET /tasks/:id/verifications`最多20条，并校验两侧任务operate权限。复测采集/解析成功后自动请求新诊断，必须调用无自定义任务ID的 `compare_profiles`。自动验证最多15分钟，来源重分析/权限撤销会使闭环失败。
- 比较核对TaskKind、Agent、PID/完整目标身份、单位和来源。仅比较Top表self占比，符号化名；采样权重不能解释成请求延迟。重复采样的root_cause_status为not_tested。1000样本只是保守的描述展示门槛，不是显著性检验。
- `GET /code-projects`、`POST /code-repairs`、`GET /code-repairs[/:id]`、`POST /code-repairs/:id/cancel`均管理员限定。创建须project_id、goal、consent=true与幂等键，可提供source_revision固定版本；每用户最多一个活跃修复，列表20条。
- 自然语言调查可选code_project_id和code_consent=true。root登记的容器镜像、源码SHA及运行进程身份必须匹配目标；复测完成后自动创建固定源码版本的修复任务。不会根据模型的文字猜测源码仓库。
- 源码工具仅`profile_business_flow`、`read_source`、`test_patch`；最多8轮模型、12次工具和2次实际沙箱测试，每次工作420秒、480秒租约，崩溃最多3次执行。只读源码最多160行；补丁最多6段、24KiB，原SHA和唯一上下文匹配，同文件多段不得重叠。参数拒绝作为工具观测，不消耗实际沙箱测试次数。
- API只挂载Unix控制socket，不挂Docker socket/宿主源码。root broker登记项目/镜像与允许路径，模型不能选命令、镜像或挂载；生成代码在无网络、UID10001、只读根目录、512MiB、1CPU、64进程、240秒硬限的独立容器执行，无凭据和Agent命名空间。取消限制后续步骤/写入并断开工具连接；broker观察到调用方断链后停止自己已核实的沙箱。硬终止/重启等未及时观察的情形仍受容器硬限约束。
- 源码执行器内部契约3：服务端以code_repair ID固定execution_id；不同修复的profile/补丁测试分别实测，同一修复内重复或恢复可复用其自己的durable receipt。观测明确cache_hit。模型工具参数不能设置execution_id，普通HTTP输入不暴露这个内部控制字段。只读源码片段依旧按SHA读取。
- 首个源码场景是Python CRM品牌配色函数和原回归测试。必须验证原测试、384组合成输出一致性、6组交替A/B；CPU时间中位加速比至少1.10且至少5组超过1.05才采纳。保存补丁后profile，报告位置必须来自实际原profile行号。该实用门槛仅适用于登记基准，不证明生产P95/P99收益或全CRM根因。
- 源码工具需单独同意发送登记片段，区别于普通诊断的化名统计。源码输出只保存在管理员报告，进程运行身份不发送模型。原CRM配置、数据、鉴权/限流、测试和测量harness不能被模型修改。
## AgentScope internal runtime contract (2026-10-20)

`contracts/agent-runtime-v1.json` defines the private Go ↔ Python control
protocol for the optional `AI_AGENT_BACKEND=agentscope` backend. AgentScope
2.0.10 runs planning, diagnosis and source-repair ReAct loops; Go owns task
state, authorization, tool schemas, evidence and final acceptance. Existing
HTTP routes, SQL migrations and protobuf field numbers are unchanged.

Two separate Unix sockets authenticate the real process UIDs with
`SO_PEERCRED`. Their producer directories use owner UID 10001/10002, group
10003 and mode 2750; each consumer mounts the other socket directory read-only.
Each run receives an ephemeral private capability, distinct from its persisted
public UUID. Capabilities are revoked on cancellation or completion and never
become model input or report data. Callback IDs are idempotent only for an
identical canonical request; different content reusing an ID terminates the run.

Go reserves every actual model call before provider I/O, checks all tool calls
against phase schemas and domain fences, and accepts only its own validated
JSON result. Diagnosis keeps the original evidence budget: one initial evidence
plus at most seven callbacks; its fifth model call is available only for a
fourth-round invalid report correction and cannot call tools. The runner
disables provider retries and autonomous context compression. No automatic
fallback to `legacy` occurs after a runtime error.

Successful diagnosis and repair reports include server-derived `agent_runtime`
metadata (framework/version/class, public run UUID, model/tool counts, elapsed
milliseconds and optional token usage). Planning appends equivalent metadata as
an `agent_runtime` trace step, outside the business tool budget. Model-authored
runtime metadata is rejected. Runtime metadata proves execution and counts;
it does not certify the semantic truth of a model explanation.

The Python runner preserves the fully validated raw JSON value accepted by Go,
including decimal/exponent notation, negative zero and precision. It must not
decode and re-encode that value through binary64 before returning it. Go's
strict canonical result equality remains authoritative. A non-200 runtime
response may include only a fixed allowlisted error code and optional bounded
private stage/kind/counters defined in the manifest. Unknown fields or SDK
message text are rejected; result mismatch and metadata mismatch have distinct
fixed error codes. These diagnostics are not model input or report content.

Cancellation and lease/authorization revocation close the model/callback
connection. The source-test broker also observes caller disconnection and
stops only the verified job container by immutable Docker ID. Recovery begins
a fresh bounded persisted phase; token-level framework continuation is not a
contract.

