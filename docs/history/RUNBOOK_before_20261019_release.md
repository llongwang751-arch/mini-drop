# Drop 运维手册

> 本轮全量版本尚在隔离验收，当前公网版本仍为后端 20261016.1 / Web 20261016.3。新增配置与“已执行”记录分别维护；仅有配置文件不代表已部署或已完成故障演练。最新逐项证据见 DELIVERY_MATRIX.md。

## 全量升级的执行顺序

小规格生产机只接收已测试镜像；编译、隔离集成、备份恢复和性能基线必须串行。2026-10-16 并行验证曾造成严重 I/O 争用和 SSH/API 超时，后续不能仅依据空闲内存启动更多测试。启动前同时检查 `free -m`、`df -h`、`/proc/pressure/io` 和真实 API readiness；压力异常时停止提交测试负载，先确认并释放本轮私有测试资源。

1. 记录所有运行镜像 ID、发布标签、Compose 文件、数据卷、schema 版本；运行在线备份及隔离恢复。保留旧发布镜像和所有数据卷。
2. 配置使用同一冻结发布标签的六个应用镜像：API、Core Server、全工具 Agent、Analyzer、Web、maintenance；验证发布 manifest、SBOM 和签名。不得在正式发布时用可变的 full-ready 标签代替已记录的版本。
3. 保留现有 `secrets`，以 root 运行 bootstrap 补齐缺少的 Secret；分别执行 owner migration、permission-init。依次执行001～005向前兼容迁移：004新建有效任务排序部分索引，005补充采集priority范围约束及队列索引，须确认 `schema_migrations` 最大版本为5。索引构建有锁和语句超时，失败时不继续切换。
4. 使用选定的完整覆盖集合启动，等待依赖健康，再验收登录、共享权限、真实采集/分析、制品下载、Cron、复合报告、监控和备份。任何失败先保留错误证据，按兼容性判断回退镜像。
5. 在隔离验收通过后执行证书轮换、公开 IP HTTPS 与定时器安装；先从公网确认受信任HTTPS，再将PUBLIC_URL切换为HTTPS并重建API，随后实际登录验证Secure Cookie。

覆盖文件具有明确职责，必须记录最终集合到 `.env` 的 COMPOSE_FILE：

| 文件 | 用途与边界 |
|---|---|
| compose.yml | 基础服务、私有网络和持久卷 |
| compose.perf.yml | 已有受控 perf 模式，保留专用 Agent 卷与 PERFMON |
| compose.tools.yml | 独立全工具 Agent 镜像、工具 manifest、SYS_PTRACE；不挂宿主 Docker socket |
| compose.ebpf.yml | 显式授权的 BPF/PERFMON、有限 memlock、只读 tracing 与限定 seccomp；不授予 privileged/host PID |
| compose.observability.yml | maintenance、Prometheus、Grafana、Alertmanager、OTel 和 Tempo；管理端口仅监听回环 |
| compose.https.yml | 公网 Nginx 边缘入口和 ACME 路径；替代 Web 的直接端口映射 |
| compose.oidc.yml / compose.ai.yml | 仅在真实服务地址、参数及 Secret 配置后启用外部身份源或模型 |

在最终覆盖集合确定后，运维脚本内的 `docker compose` 从同目录读取 COMPOSE_FILE；执行前可用 `docker compose config --services` 核对服务列表。不要把展开后的完整 config 输出到日志，因为环境值可能包含敏感配置。

## 对象存储静态加密

新增配置使用 `MINIO_KMS_SECRET_KEY_FILE`、显式bucket AES256默认规则和自动加密；没有bucket规则时该MinIO版本会选择aws:kms，不能据AUTO_ENCRYPTION开关宣称SSE-S3已生效。主密钥位于 `deploy/secrets/s3_kms_master_key`，只挂载到storage-backend；Agent和浏览器不持有。密钥必须与数据备份分别做受保护的异机托管；丢失或替换会导致既有加密对象无法解密，bootstrap不能用作密钥轮换。本机第二目录不算异地。

独立验证脚本为 `deploy/tests/test_s3_encryption.sh`，只创建随机新卷/网络。当前已实测legacy明文→同key AES256迁移和新自动加密写入，2MiB SHA/size保持；错误密钥阶段首次因精确错误码未匹配而失败、恢复尚未运行，整个fixture未算通过。完整报告及整bucket停写迁移步骤见 `ops/ENCRYPTION_MIGRATION.md`。生产旧对象仍待备份后迁移逐件校验；GET导出备份为受保护目录内的明文且不含主密钥。

## 监控与后台维护

管理界面通过 SSH 隧道访问回环端口：Prometheus 9090、Grafana 3000、Alertmanager 9093、Tempo 3200。Grafana 管理口令保存在 Secret 文件，不写入文档。四模块输出指标和 OTLP；18 条告警分别覆盖存活、心跳、积压/失败、完整性、维护、证书、磁盘和备份。

maintenance 使用有界批次清理过期对象、孤立对象与未完成分片，记录缺失和哈希隔离状态；审计 webhook 通过文件 Secret 的 Bearer token 认证。它只写本系统审计记录，外部邮件或聊天通知需要另行配置目的地。

`deploy/systemd` 提供备份、监控状态、内部证书轮换和公网证书续期的 service/timer。先在隔离环境验证，再安装到 `/etc/systemd/system` 并执行 daemon-reload、enable --now；通过 `systemctl list-timers` 和实际运行结果核对，不能以文件存在作为已安装证据。`backup-offsite.sh` 仅在有效 rclone 目的地配置后上传，未配置时不能算异地备份完成。

前端于2026-10-16单独更新为 `20261016.3`；后端与采集镜像继续使用 `20261016.1`。服务器 `.env` 增加 `WEB_RELEASE_TAG=20261016.3`，Compose 的前端镜像优先读取该值，其余服务仍读取 `RELEASE_TAG`。仅发布界面时使用 `docker compose -f compose.yml -f compose.perf.yml up -d --no-deps --no-build --wait web`；回滚界面把 `WEB_RELEASE_TAG` 改回 `20261016.1` 后执行同一命令，保留原镜像。完整启动仍须保留两份Compose文件。

当前形态是单主机 Docker Compose：C++ 采集面、Go API、Python 分析 Worker、React/Nginx、PostgreSQL 17、MinIO。生产目录为 `/opt/drop`，Compose 基础文件为 `/opt/drop/deploy/compose.yml`，perf 覆盖文件为 `compose.perf.yml`。当前发布版本为 `20261016.1`，已核验运行镜像标签；服务器 `.env` 保存 `RELEASE_TAG=20261016.1`、`COMPOSE_FILE=compose.yml:compose.perf.yml`。所有命令由服务器 root 运维执行；所有完整 `up` 仍显式指定两个文件，避免错误覆盖环境变量时丢失 perf 配置。

## 启停与健康

```bash
cd /opt/drop/deploy
docker compose -f compose.yml -f compose.perf.yml ps
docker compose -f compose.yml -f compose.perf.yml logs --tail 100 api drop-server analyzer agent web
docker compose -f compose.yml -f compose.perf.yml exec -T api wget -qO- http://127.0.0.1:8080/health/ready
curl -fsS http://127.0.0.1/healthz
```

API 的 live 仅说明进程运行；ready 才检查依赖。Web `/healthz` 仅说明 Nginx 可服务。Agent 在线由最近心跳计算，正常周期 2 秒、15 秒后判离线；采集执行租约 45 秒。Analyzer 具有独立租约，Worker 重启后由租约回收接管。采集与分析状态分别查看，不能把 `COLLECTED` 当作结果成功。

服务重启使用部署目录中的 `docker compose restart <服务名>`，先确认COMPOSE_FILE保持当前完整覆盖集合。停机前核对执行中的任务；采集中断会保留失败尝试，已完成RAW的上传重启从journal恢复且不重新采样。上传永久4xx保留有界RAW供排查；恢复上传仍受当前attempt和deadline约束。不要使用 `docker compose down -v`：它会删除持久卷。

## 配置和凭据

`deploy/secrets` 与 `deploy/secrets/tls` 的宿主目录权限为 0700，挂载的具体文件为 0444，以兼容 standalone Compose 和非 root 用户。每个服务只挂载自己的证书/私钥及 CA 证书。签名私钥独立保存于 `deploy/secrets/signing/ca.key`，目录 0700、私钥 0600，禁止挂载进任何运行服务。备份、复制或打印日志时不得输出密码、DSN、签名 URL 或私钥。

Server/API 的 TLS 环境变量必须指向 CA、各自证书和私钥；Agent 证书 CN 必须是 `drop-agent-<AGENT_ID>`，API CN 必须是 `drop-api`。当前默认 Agent ID 为 `aliyun-local`。Server 会拒绝 Agent 身份调用 API Control，或 API 身份伪装 Agent。

数据库初始化/迁移 owner 是 `drop`，仅 migration 挂载 `migration_database_url`；API、Server、Analyzer 挂载 `database_url`，连接非超管运行期角色 `drop_app`。bootstrap 分别生成管理员密码和 `app_database_password`，`permission-init` 在 migration 之后由管理员执行 `db-permissions.sql`，服务端从受保护 Secret 读取应用密码，创建或收敛角色权限，再允许运行期服务启动。

`drop_app` 无 SUPERUSER、CREATEDB、CREATEROLE、REPLICATION、BYPASSRLS；清除其其他角色成员关系、schema CREATE 和数据库 TEMP 权限，仅授予项目表所需 DML 与序列 USAGE/SELECT。全量权限脚本特别撤销 public.audit_logs 的 UPDATE/DELETE/TRUNCATE，运行身份只能追加审计；独立 PG 的 20 项实际检查通过，生产应用该脚本的结果另记。它不拥有数据库和表。三个运行模块仍共享一个应用 DML 角色，不提供数据库层多租户 RLS；用户任务范围由 API 业务授权校验。

MinIO API/Worker 当前仍使用部署管理凭据。Agent 只取得单任务短期 PUT URL，不持有长期对象凭据。接入多租户或业务生产环境前应继续拆分对象存储与数据库服务身份并验证授权策略。生产 bootstrap 应由 root 执行；它会重写已有 0444 的 DSN 文件，普通非 root 用户直接重复执行会因文件权限失败。

Compose 的 `minio:9000` 是 S3 请求网关，`storage-backend` 才是持久化 MinIO，后者只加入隔离的 `storage` 网络。业务客户端和浏览器通过网关。显式 `backup-client` 和加密迁移一次性管理服务只在隔离的存储网络直连 backend，以保留完整对象元数据；不扩大业务网关允许的请求头，也不开放 backend 公网端口。排障同时检查这两个服务日志。

`deploy/tests/test_minio_nonroot.sh` 可验证当前 MinIO 镜像在 UID/GID 10001、全部 capability 移除、只读根目录、`/tmp` tmpfs、`HOME=/tmp` 下的真实兼容性。它只创建随机命名并带专属标签的全新 fixture 卷、独立 internal 网络和临时容器，不发现/挂载生产卷，不停止生产服务；结束时仅清理自己带匹配标签的 fixture。已实测 `CapEff=0`，45 字节对象与 7 MiB multipart 对象的 S3 写入、下载、hash、stat、list、delete 均通过。

部署配置中的 MinIO backend 使用 UID/GID 10001、全部 capability 移除、只读根目录、`/tmp` tmpfs 与 `HOME=/tmp`。既有 backend 切换非 root 前须安排受控维护，先备份并停止该 backend 的写入，再对**明确确认的 object_data 卷**转换 ownership，最后启动新配置。一次性 `storage-permission-init` 无网络、只挂载该数据卷，使用 CHOWN / DAC_OVERRIDE / FOWNER 完成既有受保护目录的递归 owner 迁移与根目录 0700；这些 capability **不授予运行期 backend**。测试脚本不会替运维人员执行生产 ownership 迁移。官方 entrypoint 的 `MINIO_USERNAME` / `MINIO_GROUPNAME` 用户切换机制已废弃，会尝试 chroot 或修改 passwd；使用 Docker 的 `user`，不要启用该旧机制。

## 采样失败与恢复

现有线上 Agent 只允许自身受控 workload。全量 Agent 支持明确配置的宿主与容器目标；普通业务 Agent 的 RUN_CONTROLLED_WORKLOAD 默认 false，验收 Compose 显式 true。目标必须复验 UID、starttime、PID namespace 和策略白名单，容器目标还须解析容器内 PID 与宿主 PID。Agent 重建后 PID 可能变化，长期计划不能假设 PID 永久稳定；不要通过给整个容器 privileged 绕过权限。

`PERF_UNAVAILABLE` 表示真实探测未通过，通常与内核 `perf_event_paranoid`、容器 seccomp、CAP_PERFMON 或工具兼容性有关。当前无特权 Compose 可使用 `PROC_CPU`；它统计线程 CPU tick，不包含函数调用栈，不能解释为 perf 结果。参数频率控制 1–10 Hz 的 procfs 读取。

当前服务器经授权已启用 `deploy/compose.perf.yml`。后续完整启动/重建与回滚必须保留覆盖文件：

```bash
cd /opt/drop/deploy
docker compose -f compose.yml -f compose.perf.yml up -d --no-build
# 只重建 Agent，须先等待正在执行的采集进入终态：
docker compose -f compose.yml -f compose.perf.yml up -d --no-deps agent
```

该模式保留 `cap_drop: ALL`，仅增加 `PERFMON`，只在原 Docker 默认 seccomp 策略上允许 `perf_event_open`。策略文件为 `deploy/seccomp/agent-perfmon.json`，SHA256 为 `b63dbbfbc7d82cfbaf554c4ea39ac98dfe11b699af874e98d4f33d79b66000d6`，由 Docker 29.1.3 上原无 capability Agent 的实际 OCI 策略导出。Docker/架构升级后应重新比较默认策略并执行受控探测。

当前 Docker 对 UID 10001 的有效 capability 为空，实测无法 perf；因此该可选模式使用容器 UID 0，但 `CapEff` 只有 `0000004000000000`（CAP_PERFMON），没有 SYS_ADMIN、SYS_PTRACE、DAC_OVERRIDE 等权限。它仍使用私有 PID namespace、只读根目录、no-new-privileges、资源限制及受控 PID 白名单，未使用 privileged、host PID 或宿主 sysctl 修改。**非 root helper、ambient capability 与独立提权边界尚未实现**，不能声称已完成这些硬化。

新工作目录使用专用 `agent_perf_data` 卷且 `nocopy: true`，避免继承旧 UID 10001 的目录 ownership；预先验证为 root-owned 0700 可写。旧 `agent_data` 卷保留不动。只使用基础 Compose 文件重新创建 Agent 会还原无 perf 模式并改回旧卷，因此切换前须核对两份卷中的 journal 和正在运行的 attempt，不可删卷“修复”权限。

`AGENT_DISCONNECTED` / `TASK_TIMEOUT`：检查 Agent、网络和任务时间线；租约到期后任务明确失败，用户重试创建新 attempt。`AGENT_RESTARTED`：采集中途重启，检查保留的尝试记录后重新发起。`ARTIFACT_UPLOAD_FAILED`：检查 MinIO 健康、磁盘和短期 URL 期限；上传每次限 20 秒、最多 3 次。已上传但结果上报失败由本地 journal 重放，不重复采样。不要在排查时手动清空 `agent_data` 卷。

分析任务失败先查看 `error_code`、租约和原始制品 hash。Worker 使用有限重试；修正基础设施或输入后，再通过业务入口重试。导入任务失败须重新导入原始文本，不支持远程 Runner 重采集。

## 在线备份与真实恢复演练

脚本要求 PostgreSQL、MinIO 已运行，当前 `analyzer` 镜像已经构建；需要宿主机 Bash、Docker Compose、OpenSSL、flock、sha256sum。脚本不会停止服务、删除生产数据库或删除 Docker 卷。显式运行的 `backup-client` 使用同一镜像，但只连接 storage 网络、只挂载S3 Secret，没有数据库/TLS凭据；因此恢复完整对象元数据无需扩大用户网关的header白名单。

```bash
chmod +x /opt/drop/deploy/backup.sh /opt/drop/deploy/restore-check.sh
/opt/drop/deploy/backup.sh /opt/drop-backups
/opt/drop/deploy/restore-check.sh /opt/drop-backups/drop-<时间戳>
```

每次备份先通过 `pg_dump --format=custom` 取得 PostgreSQL 一致性快照，再通过 S3 API 下载当前 MinIO 对象。对象按 key 的 SHA256 命名，manifest 保存原 key、大小、内容 SHA256、ETag 和版本信息，避免对象 key 进入本地路径。这是应用逻辑对象备份，包含当前对象正文、Content-Type、有界自定义元数据和四项内容头（编码/语言/下载名/缓存）；恢复 PUT 不复制鉴权、响应或加密密钥相关头，加密遵循目标 bucket 策略。兼容旧版无元数据备份。它不包含 MinIO IAM、bucket 策略、历史版本和服务配置。备份包含数据库用户/会话等业务敏感记录，目录为 0700、文件为 0600；不包含部署 Secret 和 CA 签名私钥，后两者须另做离线加密托管。

备份脚本通过 PostgreSQL 容器的管理员 Secret 执行 dump/隔离恢复，不使用运行期 `drop_app` 创建数据库。`pg_dump --no-owner --no-acl` **不包含 PostgreSQL 全局角色，也不恢复 drop_app 的 GRANT**；隔离数据恢复验收通过不等于新主机应用身份已经就绪。新主机须按下述流程重新建立运行期角色与权限。

系统使用不可变对象 key，先上传对象再提交 Artifact。在线备份期间可以继续创建任务；数据库快照之后出现的额外对象不影响恢复。脚本会检测下载时对象变化，并把数据库恢复到新建的 `drop_restore_<时间>_<随机值>` 隔离数据库，逐一核验已恢复 Artifact 的 SHA256 和大小是否都在对象备份中。任何引用缺失/不一致都让备份失败，不发布为成功备份；保留 `.partial` 目录供排查后重新执行。

恢复检查还会把对象备份实际上传至新建的 `drop-restore-check-<时间>-<随机值>` 隔离 bucket，再下载校验 hash，并通过 HEAD 核对 Content-Type 和元数据；每个验证对象随即删除，避免额外复制整桶占用磁盘。备份前要求至少 3 倍数据库体积加 1 GiB 余量，下载对象和恢复数据库前继续检查，避免演练挤占生产磁盘。成功或失败后仅清理该次脚本新建的验证数据库/bucket。最终成功目录含 `postgres.dump`、checksum、对象 manifest/正文、`backup.json` 和 `restore-check.json`。发生进程被强杀等极端情况，检查日志中验证资源名称，确认前缀和创建来源后仅清理该隔离资源。

备份与生产数据位于同一服务器时不能抵御整机/磁盘丢失。应把验证成功的完整备份目录加密复制到独立故障域，并定期从该副本运行恢复检查；保留周期由容量和业务 RPO 决定，脚本不会擅自删除历史备份。配置定时任务前先确认备份耗时和磁盘余量，是否已安装定时器以实际 systemd 状态和本轮发布证据为准。

## 故障迁移与回滚

生产恢复应先在隔离主机按上述脚本验证，再安排维护窗口和明确的写入切换点。使用相同 PostgreSQL 主版本，在**隔离的新主机/新部署**创建空数据库，以 `pg_restore --exit-on-error --single-transaction --no-owner --no-acl` 恢复；将对象按 manifest 中的原 key 上传到**新 bucket**，再次核验所有 Artifact hash。不要直接把备份覆盖到仍在写入的生产数据库或 MinIO 数据目录。

全新部署采用本工程默认数据库名 `drop` 时，恢复顺序如下：

1. 复制已知通过的发布源码/镜像，配置 `.env` 的 release、完整 `COMPOSE_FILE` 覆盖集合和新入口。恢复受保护 Secret 或执行 `bash bootstrap.sh` 生成新管理员/应用 Secret；保留 Secret 离线托管。已有SSE-S3数据必须恢复其原KMS主密钥，不能生成新密钥代替。
2. 启动新 PostgreSQL 和新对象存储，确认没有旧生产流量连接；把归档恢复到该**新建空 drop 数据库**，按 manifest 恢复对象。此阶段不启动应用写入。
3. 运行 owner migration，再运行 `permission-init`，用新 `app_database_password` 在 PostgreSQL 重建/收敛全局 `drop_app` 角色，并补齐恢复表/序列的权限和未来迁移的默认权限。新 PostgreSQL 容器必须挂载该应用密码 Secret，SQL 是在服务端读取文件。
4. 运行 `storage-init` 检查私有 bucket，然后使用记录的完整覆盖集合启动，验收 ready、登录、任务查询、制品下载及真实采集，最后切换入口。

第 3～4 步在新部署目录执行：

```bash
docker compose run --rm --no-deps --pull never migration
docker compose run --rm --no-deps --pull never permission-init
docker compose run --rm --no-deps --pull never storage-init
docker compose up -d --no-build --pull never
```

若改用其他数据库名，需要同步 DSN 与 `permission-init` 目标数据库，不能照搬硬编码的 `-d drop`。恢复的业务用户密码 hash 保持原值；bootstrap 新生成的管理员口令不会自动替换已有业务用户密码，登录应使用恢复前的有效凭据或执行明确的管理员重置流程。

发布前记录各组件精确镜像ID、release标签和完整Compose集合，先完成验证备份。回滚前暂停新任务入口并排空采集/分析，再按已经实测的兼容范围回退单个应用镜像；保留当前完整覆盖集合、数据卷、schema和KMS。只对目标服务运行带 `--no-build --pull never --no-deps` 的重建命令，不能用旧的base+perf两文件命令覆盖新版helper、加密或HTTPS配置。Agent重建后须随后重建helper，复验新心跳与真实PERF采集。

已加密对象不会随应用镜像回滚而变回明文；旧存储配置若没有同一KMS主密钥，将无法读取它们。数据库迁移只向前执行，回滚镜像不等于回滚数据库；不在仍写入的生产库上恢复旧dump，以免覆盖升级后的新数据。需要完整数据恢复时，按前述新主机/新bucket流程操作并明确切换点。所有旧命名镜像、独立密钥托管和验证备份继续保留。

## 证书轮换与公网入口

bootstrap初始叶证书365天、CA10年；`rotate-internal-certs.sh`在30天窗口签发90天新叶，也支持stage `--force`。只重建Server/API/Agent，随后重建共享命名空间的helper；所有up禁止构建/拉取。API readiness失败会恢复受保护的旧叶。完整轮换验收还须Agent新心跳和一次经helper的PERF_CPU，PROC_CPU不能替代；当前脚本不单独保证这一层。CA轮换仍需独立双CA信任迁移，不属于叶轮换脚本。

当前 IP 入口若使用 HTTP，登录和数据传输不具备公网 TLS 保护。正式业务访问应通过配置了可信证书的 HTTPS 反向代理或受控网络入口，随后把 `PUBLIC_URL` 改为 HTTPS 并重建 API，使 session Cookie 启用 Secure。仅开放 Web 的入口，PostgreSQL、MinIO、gRPC 保持 Docker 内网；云安全组和主机防火墙另行核验。不要把 `/metrics`、MinIO Console 或数据库端口映射到公网。

公网签发/续期共用锁与事务：生成受控bundle、原子替换`public-tls/current`成对切换证书/私钥，edge配置原子替换；nginx检查/重载/HTTPS探测失败恢复旧状态。监控读取current而非旧平面文件；六小时未变pair不重复保存bundle。两个edge模板关闭access日志并将error日志限为crit，避免上游错误记录签名URL。目录UID/权限、实际回滚及受信任签发仍需现场验收。具体输入、stage隔离、只重建目标服务和timer顺序见[部署TLS操作说明](../deploy/tls/README.md)。

## 已执行的运维验收

2026-10-16（Asia/Shanghai）已完成以下真实验证，未用固定样本冒充现场采集：

- 独立受控容器 3 秒、49 Hz 的 perf record/script 产生 43 个 sample，含 transform / aggregate / benchmark / main 函数栈；宿主 `perf_event_paranoid=4` 保持不变。
- API 任务 **8**（“验收 · PERF_CPU 真实受控采样”）：5 秒、99 Hz，`COLLECTED / SUCCESS`，189 samples、22 nodes；原始 folded 制品 704 字节，SHA256 `200f6f0a193de435c344e90ee8fd78ef3dd338c102d978cd7aa1bbabc1b5f469`，完整树包含上述真实函数。任务及制品保留供界面验收。
- 最小权限 Agent 重建后再次执行真实 mTLS RPC probe：API/Agent 身份隔离、旧取消/进度/结果拒绝、旧心跳不续新 attempt 租约、当前 attempt 可取消均通过；探针 fixture 自动清理。
- 20:06:03 完成在线备份及隔离恢复：数据库快照含 9 tasks / 11 attempts / 101 events / 45 artifacts；45 个对象共 1,458,001 字节全部 hash 核验通过，并完成隔离 S3 上传/下载 roundtrip。验证数据库已清理，生产服务持续运行且健康。
- 独立 MinIO 非 root fixture 严格验证通过：UID 10001、`CapEff=0000000000000000`、只读根目录，7 MiB multipart 内容 SHA256 `ebeb5f5d8b80b2a9180c96ce8b54d23e3c8e7af5a5a10d4162162e9e7861fb33`；未挂载生产卷，自己的 fixture 卷和网络均已清理。
- 最终权限切换后，20:26:46 在 active tasks / jobs 均为 0、最新真实 perf 任务 19 成功的空闲窗口再次完成在线备份和隔离恢复：17 tasks / 21 attempts / 199 events / 85 artifacts；85 个对象共 1,487,885 字节全部校验并完成 S3 roundtrip，未重启任何现有容器。生产 MinIO 已核验 UID 10001，数据目录 10001:10001:0700。

上述2026-10-16历史备份目录为 `/opt/drop-backups/drop-20261016T122640Z-c5ae53`，证据保留在 `docs/evidence/backup-restore-final.json`，不代表后续生产数据。

2026-10-18升级前的新备份为 `/opt/drop-backups/drop-20261018T053357Z-f1db03`：schema1、23任务、29次尝试、115个引用制品及115个对象（1510006字节），真实隔离PG/S3恢复5.76秒通过，内容、Content-Type和有界metadata全部验证；验证数据库/桶已清理，现有生产服务未重启、schema未修改。另一次真实随机桶测试覆盖gzip存储原字节和四项系统内容头。证据见 `docs/evidence/full/preupgrade-backup-2026-10-18.json` 和 `preupgrade-restore-2026-10-18.json`。

该份完整备份已通过严格主机认证的SSH流式复制至开发机E盘，并仅保留AES-256-GCM密文；密钥和密文目录的Windows ACL已实际核验只有当前用户及SYSTEM。认证解密后的整份tar hash与传输时相同。恢复所需的本机密钥和IV/tag说明在被忽略的 `.local/offsite-20261018` 目录，禁止提交或公开；证据仅记录摘要。它是一次人工异地主机副本，尚未配置定时外部复制，也未用它在Windows启动新的PG实例；不能称作托管灾备服务。生产迁移后的新数据仍需再备份。见 `docs/evidence/full/preupgrade-offsite-2026-10-18.json`。
