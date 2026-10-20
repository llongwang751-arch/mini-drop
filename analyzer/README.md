# Analyzer

Python 3.12 常驻 worker，独立的解析核心、PostgreSQL 租约仓库和 MinIO 对象存储适配器。

## 已实现的输入

`PERF_CPU`、`PROC_CPU`、`IMPORT_COLLAPSED` 保持 `folded_v1` pipeline 兼容原有 UTF-8 折叠调用栈（`root;leaf 123`）。折叠格式支持显式 gzip 内容类型，压缩后的原文和展开数据都受 32 MiB 上限约束。

0.2.2 的 `PERF_CPU` 新增 `perf_script` 和 `perf_data`。前者读取真实 `comm,pid,tid,time,event,ip,sym,dso`，按 PID/TID 分组，模块名参与函数标识，未解析地址原样保留；后者先验证二进制 magic 和 data 区边界，再使用镜像内可信 `linux-perf` 独立进程转换，不执行用户 ELF、不联网补符号，随后走同一解析器。旧 folded 不推断线程、时戳或丢样。工具真实执行与新 Runner 的交叉证据仍需后续 Linux 验收。

`PROC_CPU` 报告明确标记 `source=procfs`、`unit=cpu_ticks`，只包含进程和线程层级，不运行函数热点规则。`IMPORT_COLLAPSED` 标记导入来源。

全量格式由 `registry.py` 独立注册：Java async-profiler folded / OpenJDK JFR、Go CPU/heap 原始 pprof protobuf、py-spy folded、memray FileReader、Java HPROF、gperftools CPU 二进制和 heap 文本、jemalloc heap_v2、LLVM BOLT fdata、IO/memory JSONL、固定模板 SCRIPT JSON 和 RESOURCE_SNAPSHOT JSON。BOLT 的原始 perf.data 由 Agent 的 perf2bolt 转换为 fdata；`PERF_CPU` 的 perf.data 则进入独立的原生 perf 分支，不冒充 folded。各 Runner 是否可用由 Agent 的真实工具和权限探测决定，导入格式支持不代表在目标机器已运行 Runner。

结果保留 schema_version=1 的 flamegraph/top_functions，新增 `sections.perf/heap/io/memory/resources/binary/script`。字段示例见 `section-examples.json`（示例值不是实测数据）。HPROF 提供 GC-root 路径、强引用 dominator 和 retained 权重；字节基准为 HPROF 字段/数组载荷，不包含 VM 对象头、压缩引用与对齐，不能宣称精确 JVM 驻留大小。pprof/native heap 没有对象图，不虚构 dominator。

## 运行与测试

从 `analyzer/` 目录执行：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m drop_analyzer
```

数据库集成测试须设置 `TEST_DATABASE_URL`。它们会创建并清理一个独立 UUID schema，不修改应用 schema。还支持 `TEST_S3_ENDPOINT`、`TEST_S3_ACCESS_KEY`、`TEST_S3_SECRET_KEY` 执行实际 MinIO 原始输入→四个结果对象→元数据 hash 回读测试。未提供服务时集成测试明确显示 `skipped`。

容器使用仓库根目录作构建上下文：

```text
docker build -f analyzer/Dockerfile -t drop-analyzer .
```

环境变量遵循 `docs/CONTRACT.md`。`DATABASE_URL`、`S3_ACCESS_KEY`、`S3_SECRET_KEY` 支持 `_FILE` Secret。默认单并发、60 秒租约、10 秒续租。健康检查读取 `/tmp/drop-analyzer-health.json`，要求最近 45 秒心跳且 DB 和 bucket 就绪。

`ANALYZER_QUEUE=all|small|high_memory` 分离大内存队列。`ANALYSIS_MEMORY_BYTES` 默认512MiB地址空间、`ANALYSIS_CPU_SECONDS=90`、`ANALYSIS_WALL_SECONDS=120`；Linux子进程还限制文件大小、打开文件与 core dump。任务 budget 只能收紧 worker 的文件、地址空间、CPU 和进程数上限。取消或 lease 丢失会杀死整个分析进程组。容器 memory limit 应大于子进程和父进程峰值，单机建议768MiB并按实测调整。HPROF 索引在 WORK_DIR/indexes 按 SHA-256 命名，SQLite WAL 事务按子记录边界 checkpoint，崩溃后可恢复；损坏或旧 schema 的索引重建，缓存24小时/512MiB受限。

磁盘预算独立于内存与单文件限制：`ANALYSIS_DOWNLOAD_BYTES` 默认32MiB，预检本次RAW与辅助ELF的总大小；`ANALYSIS_TEMP_BYTES` 默认192MiB（六个32MiB文件预算），父进程50毫秒检查整个私有job目录及当前HPROF索引的新增空间；`ANALYSIS_MIN_FREE_BYTES` 默认保留16MiB，每个下载chunk写入前和分析前/中/后检查。Linux使用不跟随symlink的目录fd扫描，拒绝特殊文件、硬链接和被替换目录；child及工具的工作目录/TMPDIR固定job目录，JFR关闭全局perfdata临时写入。超限会终止子进程组，失败不发布结果；已有HPROF checkpoint只作磁盘基线，不计本次下载。该检查是主动watchdog，容器tmpfs/volume配额仍承担内核硬上限；缓存空间不足会明确失败，不能通过放宽job预算绕过文件系统容量。

HPROF 通过固定 `parse → index → graph → dominator → emit` 五个阶段产生 `ANALYSIS_PROGRESS`。子进程的本地通道最多4096字节，父进程只接受有序的注册阶段，每阶段数据库事务检查当前 task/collection attempt、analysis attempt、owner 和有效 lease；取消与旧 owner 不发布进度。阶段序号不是完成百分比或剩余时间预测。

资源报告保留CPU/RSS/虚拟内存/文件描述符、I/O、上下文切换、网络累计计数和采集器自身CPU/RSS。`network_scope` 明确网络 namespace 范围，不能解释为目标进程独占流量；`collector_overhead.scope` 说明 getrusage delta 的统计范围。dmesg 摘要最多200行、每行1024字符、总64KiB，`status=ok|unavailable|permission_denied`；未授权或内核拒绝不会伪造空的成功日志。

Prometheus `/metrics` 默认内部端口9102，可设 `ANALYZER_METRICS_PORT=0` 禁用，不应发布公网端口。提供领取延迟、队列状态、分析时长、CPU、RSS、临时磁盘、失败和过期租约恢复。设置 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 启用 OTLP HTTP 短 span，与 params.traceparent 链接。不会记录原始 profile、凭据或任务签名 URL。

资源指标注明统计方式：CPU 包含已等待的外部工具；`max_rss_bytes` 是分析器与其子工具中单个进程的最大RSS，`observed_peak_tree_rss_bytes` 是50毫秒轮询观察到的进程组RSS峰值，两者都不代表无间隙测得的瞬时容器总峰值。

`requirements.lock` 固定Linux运行时完整依赖，并包含从PyPI官方release JSON获得的发行文件SHA-256；公开镜像使用官方PyPI的wheel、`pip --require-hashes` 和 `pip check`，仓库构建不依赖未追踪的wheel目录。当前配方固定Python3.12.15/Trixie、urllib3 2.8.0、纯Python psycopg3.3.6及系统libpq5，工具为OpenJDK21、BOLT19和perf6.12。发布归档中的离线wheel锁与公开多架构锁保持相同版本和对应制品hash，但两份配方不宣称生成相同镜像ID。[构建输入与验收边界](BUILD_INPUTS.md)列出精确版本。`scripts/lock_requirements.py` 用于显式更新锁文件，不在worker运行时联网解析依赖。

## 一致性与边界

`FOR UPDATE SKIP LOCKED` 领取任务；领取计数限制最多三次，瞬时故障指数退避，损坏/超限输入永久失败。最终尝试崩溃后，过期租约进入 dead letter。续租与发布同时检查租约 owner、分析 attempt、到期时间、当前采集 attempt 和 `COLLECTED` 状态，拒绝取消或重试后迟到的结果。

结果对象 key 包含分析 attempt，过期 worker 的写入不会覆盖新 worker 的对象。所有对象上传完成后，Artifact、规则建议、job/task 状态和事件在同一数据库事务提交。对象存储与 PostgreSQL 之间没有分布式事务，上传后崩溃可能留下未引用对象，应由对象生命周期策略清理；数据库不会引用未上传的结果。

解析上限：32 MiB 原文、100k 火焰树节点（包含虚拟根节点）、128 层、500k 栈行、每行 64 KiB、每帧 4 KiB、总权重不超过 JavaScript 安全整数。100,001 节点明确拒绝，不静默截断。空输入、非法计数、控制字符、损坏 UTF-8、hash/size 不匹配均拒绝。每个 job 单独临时目录，成功和失败均清理；启动时只清理工作根内超过 24 小时的特定 job 目录。

生成 `flamegraph.json.gz`、`top.json`、`report.json`、`manifest.json`，同时按共享契约写有界 `analysis_jobs.result`。火焰图保留递归帧；函数 inclusive TopN 在每条栈中同名函数只计一次。规则附版本、实际函数与样本占比、阈值、最低样本数和置信度，不宣称 AI 或推断没有提供的丢样、时间范围。

## 实际验证证据

`evidence/integration-v22-offline.json` 记录0.2.2本机Windows/Python3.14回归：53项中48通过、5个Linux/POSIX条件明确skip、零失败，5.014秒。perf_script测试是明确标记的合成导入样本，包含真实child进程边界；perf_data工具委派使用mock，不能视为真实perf工具通过。当前91项全量Linux PostgreSQL/S3回归、Linux POSIX路径验证、真实新perf RAW交叉、100k整容器512MiB基线及新镜像安装/源码SHA核验仍待串行执行。

`evidence/integration-full-final.json` 记录一次真实Python3.12.12非root、PostgreSQL17.11和MinIO隔离测试：63/63通过、零skip，83.658秒；对应日志和源码指纹一并保留。之后新增的阶段事件与资源扩展以独立后续报告记录，不能套用此旧报告。

`evidence/integration-v21-final.json` 记录0.2.1真实Linux后续回归：70/70通过、零skip，115.438秒，包含HPROF阶段事件的数据库fencing、资源扩展及native截断拒绝。首次70项中worker启动等待5秒在并发环境超时；调整为有界15秒并检测进程提前退出后，完整重跑通过。100k边界使用其后的独立回归与基线报告。

历史ARM64输入使用固定版本的官方LLVM19 `bolt-19` 和 `libbolt-19-dev`，安装前校验精确大小与SHA-256。`evidence/arm64-bolt-pins.json`只证明当时供应链元数据匹配，ARM镜像构建和执行结果须另行记录；历史amd64证据使用Bookworm/LLVM16。当前Trixie公开配方在两架构均通过签名APT安装BOLT19，不再调用旧ARM安装脚本。外部工具版本保留在具体结果元数据中。

`evidence/native-format-results.json` 包含17个TaskKind及额外JFR共18项真实格式解析：Go pprof、JDK17 JFR/HPROF、memray1.20、gperftools、jemalloc、LLVM16插桩fdata均由隔离toy程序生成；每项记录输入SHA、大小、权重、节点与真实子进程开销。IO/memory的该组fixture来自toy的用户态文件操作/分配测时，不是内核BPF采集证据。

`evidence/runner-format-results.json` 独立验证Core实际RunnerRegistry采集的13项toy业务进程产物，逐项核对原始size/SHA后在Analyzer中解码。fixture作为导入验证保留 `source=import`，同时记录原始collector和 `profile_source`；没有把该验证冒称公网生产任务。

`evidence/bolt-toy-equivalence.json` 记录真实BOLT插桩、优化及三组交替执行；只执行验证脚本刚编译的确定性checksum toy，原/优化程序输出和退出码一致。共享CPU下的短计时只作烟测，不能推断业务性能收益。Worker只转换上传的ELF，不执行它。


## Native configuration and logging (0.2.3)

`python -m drop_analyzer [--config /path/config.json] [--set KEY=VALUE]`
resolves CLI > environment > JSON > validated defaults. `Config.from_env()`
keeps the existing environment-only API. `ANALYZER_CONFIG_FILE` supplies a
shared default JSON path for the worker and `python -m drop_analyzer.health`;
an explicit `--config` wins. The health entry accepts the same `--config` and
`--set` flags and remains silent. When overriding `HEALTH_FILE` only on the
worker CLI, provide that same override to its health command, or put it in the
shared JSON/environment configuration.

The explicit non-secret allowlist is `S3_ENDPOINT`, `S3_BUCKET`, `S3_SECURE`,
`WORKER_ID`, `LEASE_SECONDS`, `RENEW_SECONDS`, `POLL_SECONDS`, `WORK_DIR`,
`HEALTH_FILE`, `ANALYZER_QUEUE`, `ANALYSIS_MEMORY_BYTES`, `ANALYSIS_CPU_SECONDS`,
`ANALYSIS_WALL_SECONDS`, `ANALYSIS_DOWNLOAD_BYTES`, `ANALYSIS_TEMP_BYTES`,
`ANALYSIS_MIN_FREE_BYTES`, `ANALYZER_METRICS_HOST`, `ANALYZER_METRICS_PORT`, and
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`. Metrics and exporter startup consume this
resolved object; they do not reopen environment settings behind the snapshot.
Other ambient `OTEL_*` SDK overrides are refused. OTLP uses the configured
HTTP(S) endpoint, normal certificate validation, fixed bounded batching and no
implicit proxy/netrc or credential-provider configuration.

`DATABASE_URL`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` remain environment/secret-file
inputs; raw secret values are forbidden in JSON and on `--set`. Every known
setting retains its environment `_FILE` variant. JSON may contain known `_FILE`
references, including these secrets. Within a layer, `_FILE` precedes the raw
environment value; environment precedes JSON. Secret-source paths and secret
contents are absent from the configuration hash. JSON objects are at most64KiB;
unknown/duplicate keys, control characters, nonfinite numbers and out-of-range
budgets fail with fixed public errors. No rejected original value is logged.

Startup emits `configuration_loaded` with the redacted effective configuration
and a stable SHA256 of canonical effective settings. The hash includes validated
non-secret endpoints while their displayed values are redacted; it never hashes
secret values or their source paths. Operational records share `time`, `level`,
`service`, `version`, `request_id`, `task_id`, `attempt_id`, `agent_id`, `stage`,
`duration_ms`, `error_code`, and `message`. Inapplicable IDs are empty. Bounded
allowlisted fields replace arbitrary exception text, URLs and raw profiles;
third-party standard logging keeps only module/exception class identifiers.

The 26-file 0.2.3 runtime passed all101 actual isolated Linux PostgreSQL/S3
tests with zero skips in59.168 seconds, followed by the unchanged imported Core
perf-script91/native perf.data93-sample cross-check. The installed source
matches SHA256 `086561892d7fb844617c055e990ab0a1c2a5f5cace747a0a93f15e41239f583e`.
`evidence/release-v23-freeze.json` binds source, image, tools and actual reports.
The earlier 0.2.2 whole-container512MiB performance baseline is retained as
historical evidence; this configuration-only release did not repeat that
benchmark. Stage/production rollout remains a separate action.
