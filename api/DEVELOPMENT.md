# API 开发与验证记录

2026-10-16：根据 `docs/CONTRACT.md`、共享 SQL / Proto / TaskKind 真源实现 API 编排模块。目录按 HTTP、业务服务、仓储、对象存储、gRPC 适配器拆分。真实 protobuf Go 代码由 protoc 29.3 / protoc-gen-go 1.36.5 / protoc-gen-go-grpc 1.5.1 生成。

先编写参数边界、资源所有权、viewer 权限测试；首次执行因领域类型不存在而失败，补齐领域与服务后通过。随后增加匿名访问拒绝、登录限流窗口、密码边界、签名 URL 路径保留、旧取消事件不能影响新 attempt 等回归测试。

已实现：本地管理员 bcrypt 登录、随机会话 Cookie / CSRF、角色与 owner 权限、用户管理、任务幂等创建、事务 Task / Attempt / Event / Outbox、持久化调度恢复、取消与重试 attempt fencing、短期制品下载、2 MiB 折叠栈导入、真实数据库聚合概览、SSE 重连游标、分钟计划与唯一执行记录、审计与分离健康检查。Agent 仅获取单任务对象的 600 秒 PUT URL，浏览器下载授权为 300 秒。禁止任意脚本与 URL，真实采集 PID 限定在 Agent 上报的受控进程列表。

运行：`go test ./...`、`go vet ./...`。PostgreSQL 集成测试通过 `TEST_DATABASE_URL` 或 `TEST_DATABASE_URL_FILE` 启用，在随机独立 schema 内验证并发幂等 / owner 越权 / 取消重试 / 并发计划去重；不修改应用 schema。如从独立二进制运行，设置 `TEST_MIGRATION_FILE` 指向共享 SQL。

部署：仓库根目录为 Docker build context，使用 `api/Dockerfile`。启动前执行 `drop-api migrate`。Secret 优先读取 `<NAME>_FILE`，无文件时读取 `<NAME>`；TLS 文件配置值是证书文件路径。任务类型在运行时读取共享 JSON。镜像不包含示例用户密码、证书或长期访问密钥。

当前范围：单 API 副本的内存登录限流；任务权限为 owner / admin，Agent 为统一资源域；尚未配置真实 IdP，OIDC 与细粒度资源组隔离未宣称完成。导入与现场采集分别标识，不预置伪造任务或统计。性能与故障验收结果由主部署流程记录。

实际验证：本地 `go test ./...`、`go vet ./...`、`go test -trimpath -tags=nomsgpack ./...` 与 `go vet -tags=nomsgpack ./...` 均通过。远端使用 PostgreSQL 17.11 的隔离测试容器（`--network none`，无公开端口，内存 256 MiB）执行 `TestPostgresAtomicWorkflow`，最终 7 个真实数据库子场景全部通过：8 次并发幂等仅持久化一组 Task/Attempt/Event/Outbox；owner 隔离；取消后重试不会被旧 outbox 影响；并发计划仅触发一次；12 个不同键并发只接受 10 个任务；概览统计包含实际分析中任务并排除已取消/失败任务；中断导入到期后转为明确的 IMPORT_TIMEOUT 终态。

补偿与敏感数据：Create 与 Retry 共享 owner 级 PostgreSQL advisory lock，避免不同幂等键绕过并发配额。IMPORT_COLLAPSED 的 CREATED 任务在期限内允许原请求重放，期限到达后 sweeper 写 Task/Attempt FAILED 与事件；不把原始折叠栈长期写入 SQL。自定义 panic recovery 只记录 request_id 和错误码，避免框架默认请求转储将 Cookie 写入日志。

依赖扫描：根据 [Go 官方安全修订](https://go.dev/doc/devel/release#go1.26.9) 将工具链与构建镜像升级为 Go 1.26.9，并将 Gin / GORM / minio-go / gRPC / pgx / x/net / x/text 等升级至扫描时已修复版本；包含 [pgx 占位符处理修复](https://pkg.go.dev/vuln/GO-2026-5004)。显式固定 quic-go 0.59.1。`govulncheck` 最终报告应用可达漏洞为 0，已导入包漏洞为 0；模块图中 x/crypto 的 GO-2026-5932 尚无上游修复，但涉及的功能包没有被本应用导入或调用，不宣称整个依赖图无漏洞。

概览回归：`tasks_running` 同时统计真实采集中任务与 COLLECTED 后分析状态为 PENDING/RUNNING/RETRY 的任务；CANCELED/PENDING、FAILED/PENDING 不计入。加入第 7 个 PostgreSQL 场景并通过（7 场景 1.11 秒）。

部署构建：标准 Dockerfile 在镜像内生成 Proto、运行测试并编译；统一 `-trimpath`，以 `nomsgpack` 排除未开放的 MessagePack 绑定，避免对无用大包重复编译。对 2 vCPU 主机另提供 `Dockerfile.runtime`，允许在开发机以 Go 1.26.9 / CGO_ENABLED=0 / GOOS=linux / GOARCH=amd64 交叉编译同一源码，再构建仅含运行环境与二进制的非 root 镜像；构建产物 SHA-256 在发布步骤核验，源码构建路径保留用于复现。

线上最小读基线（2026-10-16 20:01:05，Asia/Shanghai）：生产主机经本地 Nginx，对真实数据库分别发起 50 次 GET /overview 和 50 次 GET /tasks，10 并发，仅登录/退出一次。100 次测量请求错误率 0%，总耗时 0.540 秒；P50 31.773 ms、P95 181.412 ms、P99 221.376 ms，短突发吞吐约 185.197 req/s。测试前后真实任务数均为 3；同期主流程继续 E2E 验收，此结果只作为低负载短突发基线，不代表持续容量或公网网络时延。原始数据见 `benchmarks/http-baseline-2026-10-16.json`；可复用脚本见 `tools/http_benchmark.py`，凭据仅从服务器 Secret 文件读取，日志不输出 Cookie、CSRF 或密码。

源码发布核对：最新标准 Dockerfile 全量生成 Proto、测试并编译成功，产出独立 `drop-api:source-verified`（镜像 SHA256 `cf7e19fd00f9ade494522ddb4c185da4ac2eeb0db414dc184195354e42886023`）；20 个业务/测试 Go 文件与 go.mod/go.sum 已逐项 SHA-256 校验本地/远端相同。两套产物使用相同 Go 1.26.9、依赖版本/校验值、编译参数，生成的协议代码仅 protoc 版本注释不同。开发机产物还含 Git 工作区元数据，故最初交叉构建与源码构建的二进制并非字节相同；`benchmarks/build-proof-2026-10-16.json` 记录受控最终发布前的核对结果，不将其旧 runtime 哈希当作后续发布哈希。生产 `drop-api:20261016.1` 已使用该完整源码镜像且运行健康，实际 image ID 与上述 SHA256 一致，见 `../docs/evidence/release-20261016.1.json`。

数据库最小权限：新增 `deploy/db-permissions.sql`，以 migration/object-owner 管理员执行，密码只在 PostgreSQL 内通过受保护 Secret 文件读取并以 SCRAM 存储。drop_app 去除超级用户、建库、建角色、复制、绕过 RLS 与旧角色会员；显式授当前库 CONNECT、public USAGE、现有/该 migration owner 未来表 DML 与序列 USAGE/SELECT；不授 sequence UPDATE。额外撤销 PUBLIC TEMP 与 public CREATE，以保证无法建临时表或业务表。权限与密码轮换同事务，敏感角色语句关闭本会话 SQL 日志并用不含密码的通用异常退出。

真实 PostgreSQL 17.11 隔离测试最终通过 16 项检查，含 DML/序列、未来表默认权限、拒绝 CREATE ROLE/TABLE/TEMP、pg_read_file、setval、TRUNCATE、剥离旧特权、重复执行幂等、带行尾的 Secret、密码轮换旧密拒绝及日志不含测试密码。测试容器无网络、无公开端口，已清理；该组测试不连接生产数据库。脚本 `tools/test_db_permissions.py`，报告 `benchmarks/db-permissions-2026-10-16.json`。初始化探测需等待最终 TCP listener，避免官方 PostgreSQL entrypoint 临时 Unix 初始化服务造成启动竞态。

生产切换检出的有效回归：最初权限 SQL 使用单参数 `btrim`，仅裁剪空格，遗漏 bootstrap Secret 文件的行尾 LF，造成运行时 SCRAM 密码与 DSN 不一致。主流程已将 SQL 改为 `btrim(..., E' \t\r\n')` 并重跑权限初始化，API/Analyzer/Server 恢复健康。隔离 fixture 现分别以末尾 LF 创建密码、末尾 CRLF 轮换密码，16 项真实回归重新全部通过。原无行尾 fixture 未覆盖该场景，已补齐；没有输出密码、Cookie 或 Secret 正文。生产现以 `drop_app` 连接数据库，发布快照中 API/Server/Analyzer 共 4 个运行连接；角色无 superuser/createdb/createrole/replication/bypassrls，public CREATE 与数据库 TEMP 均为 false。MinIO backend 实际 UID 10001、有效 capability 为 0；perf Agent 为 UID 0，仅保留 PERFMON（`0000004000000000`），未开启 privileged 或 host PID。8 个长期服务均在运行，6 个配置了健康检查的服务均为 healthy，证据见发布快照。

已核实的全链路验收：发布 `20261016.1` 并完成最小权限切换后，最新公网六项 Playwright E2E 全通过（23.3 秒），发布归档 `../docs/evidence/playwright-last-run.json` 为 passed。覆盖导入响应丢失后幂等重放、真实结果与签名下载、PROC_CPU、取消立即重试、CSRF 与 owner/viewer 隔离，以及 375/768/1024/1440 响应式、键盘和移动菜单焦点 trap/escape/inert。login/overview/result 三页本轮 axe JSON 均为 `[]`；仅代表已覆盖页面与规则，不宣称全站人工可访问性认证。早先真实 perf 任务 #8 为 5 秒 99 Hz、189 samples、22 nodes；发布后任务 #19 为 5 秒 99 Hz、190 samples、10 nodes，RAW 与 4 个输出实际下载的 SHA/size 均通过。分钟计划 #1 唯一触发任务 #12 并分析成功，用时 66.757 秒，计划已暂停。

最终独立复验：任务 #19 的真实 SSE 首连读取事件 189 后主动断开，携 Last-Event-ID=189 续读 190/191，严格递增且不重放游标，与持久化 after 查询一致；这验证单次客户端断流重连，不宣称覆盖长期代理与全部网络故障。最终在线备份快照为 17 tasks/21 attempts/199 events/85 artifacts，85 个对象共 1,487,885B 的 hash/size 校验、隔离 PostgreSQL 恢复与 S3 回读全部通过；未重启服务或修改生产资源，早先 45 对象批次保留为历史证据。同服务器备份不等于异地灾备。对应记录为 `../docs/evidence/perf-live-final.json`、`../docs/evidence/sse-resume.json`、`../docs/evidence/backup-restore-final.json`；客户端与服务端时钟不同，不使用跨机器时间差计算延迟。

2026-10-16 第二轮全能力扩展：按共享V2 Proto/18项TaskKind目录新增JSON Schema 2020-12 typed参数校验、17种非COMPOSITE原始导入/ELF附件、目标启动时间/UID固定与Agent显式目标策略。导入保留实际format/metadata/source=import，并清除采集端时长/频率，不将离线样本标记为现场采集。新增002迁移，不修改001；根流程独占003维护游标迁移。真实S3解析能力由Analyzer独立集成验证，本模块服务边界测试使用明确的内存对象存储fixture，不把它当作S3验收。

编排和权限：持久DAG实现依赖、必选/可选、部分成功、总期限、取消与有界结果/建议汇总；任务仍沿普通Task/Attempt/Event/Outbox。Cron使用robfig五段标准表达式与IANA时区，春季不存在时点跳过，秋季重复小时分别计算；数据库锁、唯一键、普通任务幂等和模板快照共同防重复。DAG/Cron/导入补偿与慢RPC派发分开运行。Viewer/Operator/GroupAdmin/PlatformAdmin与旧admin兼容，统一覆盖Agent/Task/Artifact/Schedule/Group/共享，SSE每轮重新检查会话和资源权限。组管理员只能委派已有Agent权限；任务read share不能operate，operate share不能管理共享或删除。详情permissions由同一授权函数计算。

身份和建议：可配置OIDC实际实现discovery/JWKS签名、issuer/audience/azp/expiry、state单次消费和浏览器绑定、nonce、PKCE、默认viewer独立身份映射，保留本地bcrypt登录。OIDC集成fixture运行真实HTTP与RSA/JWKS签名，尚不代表已经接入某个企业IdP。可配置外部AI仅发送数值和不可逆哈希符号摘要，需显式consent；流式HTTP增量逐块检查当前attempt/授权/lease，持久SSE事件，非流回退明确streamed=false。实际模型服务未配置时返回AI_UNAVAILABLE；测试供应商是协议fixture，不当作真实模型诊断。规则更新与用户反馈持久化，重分析归档旧run并拒绝覆盖活跃租约。

可观测性与保留：新增Prometheus HTTP直方图、gRPC客户端指标和实际SQL队列/Agent/租约/保留期gauge；OTLP HTTP exporter与TraceContext传播、task link已实现，真实本地collector收到protobuf导出。客户端错误接口只收固定code，不接收堆栈/路径/原始输入。Artifact插入时设置分类型保留期，签名URL不超过剩余保留期；对象删除/孤儿/缺失/hash/multipart对账由根流程maintenance模块独立负责。共享PostgreSQL限流替代进程内计数，短期会话/state/限流行定期有界清理。32MiB导入采用单进程创建入口并发门与Go内存预算，仍需真实容量基线，不宣称持续吞吐SLA。

本轮实际验证：本机`go test -p=1 -trimpath -tags=nomsgpack ./...`和`go vet -p=1 -tags=nomsgpack ./...`通过；Cron/DST、DAG环、typed schema、路径/任意URL/argv拒绝、摘要脱敏、OTLP真实导出均有回归。隔离PG17.11（network none/512MiB/0.75CPU）repository/service/httpapi三包通过，覆盖原7场景、组/共享/40并发共享限流、二进制导入原样保存、DAG重建/可选失败/期限取消、OIDC成功及nonce/audience/签名/state边界、AI流式lease撤销、管理员重分析/旧run和Agent审计、HTTP各资源面越权拒绝。报告`benchmarks/full-integration-2026-10-16.json`；测试只操作随机schema且容器已清理。govulncheck本轮应用可达与已导入包漏洞均0，模块图仍1项不达漏洞，不写成全依赖零漏洞。默认并行vet和首次govulncheck曾触发开发机分页文件不足；改为-p=1、GOMAXPROCS=2及扫描GOMEMLIMIT=512MiB后通过，未修改系统配置。源码Docker已在隔离构建目录完整编译通过，最终镜像/线上全链路以主流程后续发布记录为准，本轮未重启生产。

目标策略联调回归：Core实际上报的canonical键为host_processes/containers/host_resources/allowed_container_ids/allowed_uids，API统一按共享`contracts/fixtures/target-policy.json`验证，并对容器ID执行精确白名单。早先allow_*口头命名与实际JSON不同，已修复；canonical拒绝值优先于兼容alias。进程列表有界截断不再等同于授权边界：未列PID仅在host_processes明确允许、expected_uid已固定且属于allowed_uids时可创建，单UID策略自动固定，多UID要求显式选择；最终由Agent实际OS UID/starttime复核，不能仅信任客户端UID。该路径不代表任意宿主PID开放。

API运行源码冻结与镜像（2026-10-16 22:50，Asia/Shanghai）：标准Dockerfile完整生成Proto、测试并编译，最终`drop-api:full-ready`镜像ID为`sha256:6b5fe5d900c10cdd86de6c64f6cd4357caf757c0f962f87eb46020e5372b7ecc`，二进制SHA256为`cd14af879c8cf8556dffce2b773d64a7a8a1a886593c5747469c41fc9319ba6d`。60个runtime Go/module/proto/catalog/fixture/migration文件本地与远端构建目录逐项hash一致；完整proof见`benchmarks/full-build-proof-2026-10-16.json`。最新PG三包回归报告已归档（HTTP末次日志UTC14:23:27），不是旧报告。后续仅补文档/静态工具，没有改变运行二进制；全栈staging及生产验收由根流程继续执行。

全HTTP契约收尾：`docs/openapi.yaml`升级v2并覆盖60个实际Gin操作、73个Schema；18个TaskKind规范化参数Schema从共享JSON直接复制。`tools/check_http_contract.py`核对全部路由双向覆盖、path参数、公共DTO字段、CSRF/幂等、字符串事件游标及导入/共享/Cron边界fixture；真实执行通过，并通过openapi-spec-validator0.9.0的OpenAPI3.1结构校验。命令为`python api/tools/sync_openapi.py`与`python api/tools/check_http_contract.py --spec-validator`（依赖PyYAML/jsonschema/openapi-spec-validator，仅开发工具）。原共享`python scripts/check_contracts.py`也通过。IMPORT_COLLAPSED通用import在Validate中先于collapsed_text分支，已纳入契约fixture。查询边界按真实代码记录：diagnostics固定processes/events100条且尚未透传truncated/generated_at；Groups/Schedules当前无数据库分页限制，不宣称无限资源域容量。
