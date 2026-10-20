# Analyzer 开发过程

日期：2026-10-16。

1. 阅读《drop 系统复刻指南》第 3.6、7、11、12 节，以及共享 `docs/CONTRACT.md`、SQL migration 和 TaskKind 真源。将首期分析边界固定为 `folded_v1`；未实现的 pprof、HPROF 和其他采样器不注册。
2. 先加入固定 folded 黄金样本和九个测试。第一次运行因 `drop_analyzer` 不存在失败（Red）。实现有界 UTF-8 栈解析、递归权重、火焰树、self/inclusive TopN、gzip/SHA-256/size 校验与确定性产物后九项通过（Green）。
3. 先加入共享结果契约、procfs 声明和导入来源测试。运行因 `result` 模块不存在失败（Red）。实现来源、单位、限制说明及带证据的规则建议后十二项通过（Green）。
4. 分离 Config、Repository、ObjectStore、LeaseGuard、Worker、Health 模块；没有把数据库或对象存储调用混入 parser。Repository 采用 PostgreSQL `FOR UPDATE SKIP LOCKED`、租约续期、有限重试和最终崩溃 dead letter。
5. 对成功发布同时校验 owner、analysis attempt、lease expiry、Task 当前 collection attempt 及 `COLLECTED` 状态。对象 key 含 analysis attempt，避免迟到上传覆盖下一轮结果。Artifact、suggestion、job/task 终态和事件同事务提交。
6. 增加五个 worker 故障与生命周期测试，验证上传中断不发布成功、过期 owner 不提交、临时目录回收、续租丢失、health freshness。共十七项单元测试通过。
7. 增加九个真实 PostgreSQL 集成测试，覆盖锁跳过、不同/相同 worker ID 的 attempt fencing、取消与重试隔离、退避和重试上限、损坏输入立即失败、最终租约到期和元数据原子回滚。另用数据库触发器延迟结果写入，验证发布过程中租约到期也会回滚全部 Artifact。没有 DB 时明确 skip，不能视作已验证。
8. 本机 Python 为 3.14，Docker Desktop Linux daemon 不可用。部署目标为 Python 3.12，须在服务器容器中再次运行；远端使用隔离 `drop-analyzer-test-*` 资源，不连接正式数据库，使用独立临时 PostgreSQL 数据目录，无公开端口，测试后清理。
9. 增加实际 MinIO round-trip 集成用例：将真实 folded 输入上传，worker 解析并上传四个结果对象，事务提交数据库 success 后逐个回读，验证 SHA-256、gzip 火焰树和报告来源。远端 Python3.12.12 镜像已构建成功，容器用户为 10001。
10. 原 `postgres:17.6-alpine` 官方镜像拉取长时间没有进展；终止本模块自己的等待进程并清理隔离资源。主线程改用官方公共镜像获取当前 PostgreSQL17.11 后，将测试脚本固定为 `postgres:17.11-alpine`。
11. 在阿里云 Ubuntu 服务器、Python3.12.12 非 root 容器、PostgreSQL17.11 和 MinIO 下运行全部 27 项测试，3.266 秒全部通过，零 skip。包含九项真实 SQL 用例和一项实际对象存储流水线往返。测试容器、临时数据库、测试 bucket 和 network 均清理完成；保留 `drop-analyzer:build` 镜像供部署。
12. 明确导入来源的时长未知，IMPORT_COLLAPSED 的 summary.duration_seconds 固定为 null，不把创建请求默认值误写成实测时长。快照镜像更新为 `154375d094cf`，27项服务集成回归再次通过（2.939秒）。
13. 增加真实 worker 子进程崩溃测试：第一子进程领取真实 job 后在下载阶段等待，同时持续续租；测试实际 SIGKILL 该进程，确认数据库仍是 running。第二 worker 等待15秒 lease 自然到期后自动领取并完成，同一 job 的 attempt=2、恢复事件为true、生成4个结果制品。远端28项全部通过（19.335秒，零skip），测试资源均清理。
14. 编写 `docs/openapi.yaml`，与实际 Gin 路由逐条核对26个path/30个operation，检查无重复operationId和悬空引用；24个schema通过JSONSchema2020检查，并用三种真实AnalysisResult和采集/导入正反例验证。另编制 `docs/DELIVERY_MATRIX.md`，区分已执行证据、待公网E2E与后续扩展能力。
15. 独立验证主线程新加的存储隔离网关：通过Compose analyzer临时容器读取Secret，只操作随机fixture bucket。初次7/8通过，发现form POST `Action=AssumeRole` 到达后端返回400。反馈主线程后其新增根路径只允许GET/HEAD并reload；全部8/8通过（0.117秒），fixture清理完成。正常签名Put/Get/Head、预签名Put/Get及sha256 metadata兼容；unsigned trailer、query/form STS、管理、复制标记均403，复制加密header不能污染fixture对象。回归保存在 `deploy/tests/`。保留“旧二进制仅缓解、未打全量补丁”的明确边界。
16. 额外执行真实公网10k/49k节点浏览器验收，仅建立两个明确标记的合成导入任务9/10，原文分别202,760和991,160字节，Analyzer事件耗时130/602毫秒。首次49k SVG全量DOM搜索769毫秒，随后按主线程授权仅改Flamegraph组件与Worker：纵向可视行+3行overscan、固定25px矩形/28px行高、完整布局搜索、缓存颜色/规范名、memo帧与roving tabindex。新增4项回归先红后绿，全Web16项通过、TS/Vite构建通过。
17. 新版部署后复用原任务、不新增大任务进行真实浏览器复测：49k布局完整保留49,000节点，初始DOM矩形降至8,402，导航到可交互2.655→1.469秒，搜索769→145.4毫秒；10k DOM降至2,102，搜索190.1→58.8毫秒。两页零JS/HTTP错误；屏外匹配计数、定位/重置、滚动绘制、展开/折叠、方向键均通过。before/after JSON与截图、测试方法和非SLA边界保存在 `docs/evidence/flame-performance.md`。
18. 在最小权限 / 非root MinIO切换恢复后的最终环境，复用现有会话、读取当前Agent受控PID7，创建一次真实PERF_CPU任务19（5秒/99Hz）。采集与分析成功，source=perf，190样本、2条folded栈、10节点/9深度。RAW与4结果全部经公网授权下载核对size/SHA-256，RAW权重、gzip火焰树权重和result均为190，manifest输入hash匹配；13条任务事件齐全。
19. 使用同一任务执行真实SSE服务端游标回归：首连接收到sequence189后主动关闭；新连接携带Last-Event-ID=189，收到190/191，与持久化after=189的前两项完全一致、严格递增、无游标事件重放。两个连接分别配置20/25秒超时且由客户端主动关闭，没有mock。证据保存在 `docs/evidence/sse-resume.json`。客户端连接时间与数据库事件时间分别注明来源，不用跨机器时间差计算延迟。
20. 最终perf截图使用1440×1000浏览器，等待fonts.ready及6个有限CSS动画finished后捕获，active有限动画为0、图中10个实际rect、0 JS异常；JSON与PNG保存在 `docs/evidence/perf-live-final.*`。运行脚本 `web/e2e/perf-live-final.mjs` 保留幂等checkpoint，不再次登录，不执行服务部署或重启。

本机性能基线（Python3.14，开启 tracemalloc，包含解析和结果编码）：10,002 节点、250,000 输入字节耗时 0.400 秒，Python 堆峰值 10.4 MiB；49,002 节点、1,225,000 输入字节耗时 1.954 秒，Python 堆峰值 52.3 MiB。该数据来自开发机，不代表服务器承诺或最坏输入上界。

单测命令：从 `analyzer/` 执行 `python -m unittest discover -s tests -v`。

集成测试命令：从仓库根目录执行 `sh analyzer/scripts/run-isolated-integration.sh`。脚本使用 `drop-analyzer:build` 镜像，并建立独立 UUID schema 验证共享 migration。

首期边界记录（后续范围已扩展）：规则引擎只产生可解释相关信号；Analyzer不调用外部AI，外部AI编排由API独立负责。对象存储与数据库不使用分布式事务，上传后崩溃可能留下未引用对象，由独立维护服务与生命周期策略清理。首期50k节点上限在后续100k边界回归中提高，历史性能数据不作为新规模的验证证据。

## 全量分析扩展（2026-10-16，取代首期三种输入的范围）

21. 用户明确要求完整文档范围后，与 Core/API 冻结 contracts/V2.md：17 个采集 TaskKind 对应15个独立 pipeline；RAW format 与kind严格匹配，ELF采用独立BINARY制品，保留既有protobuf编号及数据库lease/attempt fencing。
22. 实现 pprof protobuf wire/string/function/location/inline/sample/label 表解析，支持CPU纳秒和alloc/inuse objects/space；label精确匹配和include/exclude子串过滤。选中负权重的差分profile明确拒绝。CPU规则的最低样本阈值使用原始samples计数，避免将纳秒权重误称为样本数。
23. 加入 gperftools CPU 原生32/64位大小端二进制、gperftools heap、jemalloc heap_v2采样校正、LLVM BOLT fdata branch/no_lbr/memory记录。可附ELF执行有界addr2line符号化及真实llvm-bolt优化，优化产物标记仍需业务回归，不执行上传的ELF。Java CPU支持async-profiler collapsed与OpenJDK jfr JSON解码；Python内存使用memray真实FileReader，而不是folded替代品。
24. 实现 HPROF header/class/instance/object-array/primitive-array/GC-root和强引用图。SQLite WAL按子记录事务checkpoint，可在崩溃后从offset/segment恢复；迭代DFS与immediate-dominator算法计算retained、类表及GC-root保留路径，排除Reference.referent弱边。大小基准明确为HPROF序列化字段/数组载荷，不虚构VM对象头、压缩引用和对齐的精确驻留字节。
25. 实现IO JSONL吞吐/IOPS、P50/95/99、设备/进程/文件热点、队列重叠区间和时序；memory JSONL分配/释放/页缺失/存活候选；RESOURCE_SNAPSHOT与固定模板SCRIPT JSON。缺失延迟不伪造零延迟；存活对象与retention candidate不直接宣称泄漏。
26. 每个任务在独立子进程执行，Linux限制AS/CPU/文件/FD/core dump并启用no_new_privs，父进程限制wall time/output并随lease失效杀死进程组。子进程环境不传数据库或S3凭据。high_memory/small队列可独立部署；摘要和manifest记录来源、格式、输入hash、限制、collector元数据及实际CPU/RSS/时长/临时磁盘开销。HPROF cache按24小时/512MiB清理。
27. 增加内部Prometheus指标和可选OTLP HTTP短span/trace link；规则从diagnostic_rules读取enabled/threshold/min_samples/version，建议保留input_digest和attempt_id。Artifact/建议/job/task/events仍在同一受fencing约束事务发布；任何工具、解析、上传失败都不发布SUCCESS。
28. 本机Ruff E9/F检查和Python格式化通过；54项发现中30项通过、24项明确skip（Linux资源、实际工具fixture及PG/S3条件未齐），不能计为54项已验证。独立真实Go1.26.5/JDK17.0.17 toy进程的原生pprof和46,431对象HPROF已解析，后续以隔离Linux无宿主敏感环境fixture替换并记录版本/SHA。完整Linux工具与SQL/S3流水线验收仍在执行，最终结果将在后续日志补充。
29. 采用Core共享的Ubuntu24 full-tools基础镜像生成真实fixture，包含Go1.22.2、OpenJDK17.0.20.1、memray1.20、gperftools/jemalloc、asprof4.1、py-spy0.4.1和LLVM16；隔离network-none，只运行toy进程。全部17种kind及额外JFR共18项实际隔离子进程解析通过。HPROF为48,698对象/1,415roots，JFR117个真实sample/35节点；每个输入SHA、格式、字节数、单位、输出与资源开销记录在native-format-results.json，未将用户态IO/memory fixture冒称内核Runner验证。
30. 完整Linux非root+真实SQL/S3测试63/63通过，零skip，83.658秒。包含全部17种kind输入上传→隔离分析→4个结果对象（BOLT5个）→原子DB发布→hash回读，实际HPROF索引期间SIGKILL恢复，worker租约到期接手、旧attempt拒绝、取消、输出失败、限额以及真实OTLP collector。此前一次61项中仅硬编码旧analyzer版本断言失败，已改为引用__version__后重新完整执行；没有把失败轮次当成功。最终日志与按63条测试生成的JSON报告保存在evidence/integration-full-final.*。
31. 冻结0.2.0镜像a75926c2f23f，逐一核对镜像内22个Python文件SHA与测试源码相等（release-source-fingerprint.json），并记录完整锁依赖的PyPI canonical SHA。独立读取Core生产RunnerRegistry对toy业务进程采的10项语言/基础采集和3项native profile，13/13原始size/SHA吻合并解码通过，证据runner-format-results.json。该导入验算保留import来源，没有重启生产。
32. 按指南继续补实现而不是只列缺口：0.2.1增加HPROF parse/index/graph/dominator/emit五阶段有界跨进程通道，父进程通过current attempt/owner/lease锁事务发布ANALYSIS_PROGRESS，快速退出child仍完整drain；三项测试先因模块缺失失败，补实现后通过。Windows实际子进程暴露默认stdout编码问题，同步固定PYTHONIOENCODING/PYTHONUTF8；生产Linux编码保持UTF8。另加真实SQL旧owner/取消/lease到期插入回滚回归，完整Linux新轮次尚在执行。
33. 与Core冻结RESOURCE_SNAPSHOT网络namespace累计量、上下文切换、collector getrusage CPU delta/自身RSS以及有界dmesg状态，解析端新增字段保留与200行/1024字符/64KiB限额，先红后绿。新增真实BOLT checksum toy验证脚本，在network-none隔离容器真实插桩生成fdata并优化，3组交替原/优化执行checksum与退出码全等；计时只作烟测，不宣称生产收益。该脚本只执行自身编译toy，不执行用户或业务ELF。

34. 0.2.1完整真实Linux后续回归70/70通过、零skip、115.438秒；证据integration-v21-final.*。首次70项唯一失败为并发时worker启动等待5秒过短，改为15秒有界等待+子进程早退检测后完整重跑通过，不掩盖失败轮次。
35. 对标指南12.8提高统一火焰树上限为100,000节点（含虚拟根），回归先因默认50k失败，再验证精确100k接受和100,001永久失败；本机10项folded回归全过。新增真实隔离子进程512MiB边界与10k/50k/100k完整产物编码基线，远端最终验证进行中。
36. ARM64缺少Bookworm bolt-16的问题采用官方LLVM19预编译ARM64软件包解决；两项固定版本/大小/SHA-256与官方HTTPS Packages索引实际匹配，记录arm64-bolt-pins.json。构建脚本使用文件版本去掉Debian epoch，apt安装仍检查deb内部完整版本依赖。尚未以此核对冒称ARM执行已通过。
37. 服务器并发legacy镜像提交造成I/O压力并影响SSH，按主线程要求停止自身构建及新72项测试会话，保留精确资源名等待主线程清理；不动生产服务。100k基线首轮子进程因工作目录仍/app加载旧解析器而按50k拒绝，重跑命令已固定/workspace/analyzer，但连接在banner阶段超时，不能计成功。接下来按Core→Analyzer→性能串行执行。100k源码冻结为23文件SHA tree 3323ddeb6d5e34db914abf7c73aa414290b5263f9c5b9cb02a679c48814dd49f。

## 2026-10-17：总磁盘预算与原生perf补齐（0.2.2，本地完成，Linux待验收）

38. 对照指南7.11/7.12，确认原实现只有单文件限额，补RAW+aux总下载预检、逐块写前余量检查、job全目录/当前digest索引增量总量watchdog；Linux目录fd+O_NOFOLLOW不跟随symlink，拒绝特殊文件/硬链接/目录替换；child cwd、PYTHONPATH与TMPDIR绑定受信任源码和私有job目录。先因模块缺失红，后验证真实child超总量被终止、第二chunk写前拒绝、失败清理且不发布。HPROF既有checkpoint不重复计下载。
39. 与Core/Root冻结PERF_CPU perf_script/perf_data/folded兼容格式，新增PID/TID线程根节点、DSO区分、原始IP回退、纳秒字符串、inline首帧去重；没有loss证明不填0，native头/数据区扫描区分LOST records与硬件lost samples，压缩/AUXTRACE/截断不宣称完整。perf_data只经可信linux-perf有界argv转换，空symfs且禁止联网符号fetch；Docker两架构补linux-perf，尚未实际构建。
40. 0.2.2本机精选53项真实回归：48通过、5个Linux/POSIX条件skip、0失败、5.014秒。Ruff/格式/compileall通过。perf_script为明确合成导入fixture，执行真实child；perf_data converter是mock argv回归，未冒称原生工具验证。全量发现91项Linux回归，须排Core后执行；新源码25文件tree SHA a05d3d81f9797acc08f64adebbd78bed02d32084db2429ebc4dd34e2790e2983，之前23文件冻结和70/72测试快照已失效。
41. 独立审查Root指定SSE测试两文件，先复现网络Timeout被误判为解密拒绝；修为结构化S3错误码+明确解密原因、前后readiness+签名bucket检查，并以当前密钥新写/读canary排除KMS整体故障；实际AES256 header、2MiB完整payload与SHA元数据验证、原密钥恢复、专属资源label清理。16项离线模拟及Ruff/py_compile/bash -n通过；没有访问远端或Docker，实际MinIO三阶段验证仍待运行，未知泛化InternalError保守失败。

## 2026-10-17 最终串行验收准备（尚未执行）

- 本机重新发现91个唯一测试、12模块；25文件运行时冻结仍为`a05d3d81f9797acc08f64adebbd78bed02d32084db2429ebc4dd34e2790e2983`，没有修改运行时。
- 新增`FINAL_VALIDATION.md`，明确Core交接后顺序：新镜像及源码身份→91项真实Linux PG/S3零skip→两份独立真实perf RAW交叉→整容器512MiB无swap下10k/50k/100k和100001拒绝→最终镜像记录→新版SSE完整fixture。
- 新增`verify_perf_runner.py`：按各自manifest/native计数校验独立record的实际PID/TID/comm、root_name和ns字符串、DSO/地址及四产物；导入JSON不作零丢样凭据，perf.data必须实际调用可信系统perf，容器仅lo接口。已通过本机CLI help、Ruff和编译，真实RAW尚待Linux队列，未冒充成功。
- 基线脚本额外记录cgroup`memory.swap.max`和`memory.events`；清单显式`--memory=512m --memory-swap=512m`，以整容器无swap预算验收，不只看子进程RLIMIT_AS。运行器固定`/workspace/analyzer`，隔离child绑定当前审查源码路径。
- 新SSE probe成功输出为两个历史对象逐项保留安全code/status/reason-match布尔，11项离线回归通过；上轮真实fixture仅legacy→AES256+新写通过，wrong-key失败、恢复未跑，完整复验仍pending。

## 2026-10-18：0.2.3 原生配置、统一日志与实际冻结验收

42. 补 `--config`/`--set` 显式 allowlist，CLI > env > JSON > 有界默认值；保留 Secret `_FILE`，拒绝敏感原值进入 CLI/JSON、未知或重复键、控制字符和超大文件。主入口、health、metrics 和 OTLP 使用同一有效配置。启动输出脱敏配置及稳定 hash，secret 原值和来源路径均不进入 hash；统一有界日志字段和第三方异常脱敏。9 项新回归先红后绿，保留实际启动摘要遗漏 S3 键的失败记录，不以 wrapper 存在代替有效配置验证。
43. Windows 最终101项实际执行76通过/25明确环境skip，24.299秒；测试前后26个运行文件 SHA 一致。随后独占远端队列，保留已验证 perf6.12.95 依赖层创建0.2.3薄层镜像，101/101真实私有PG/S3零skip，59.168秒。原始Core toy perf-script91/native perf.data93采样均再次交叉通过，整验证容器512MiB/无额外swap/网络none/退出0/OOM=false。未重跑或改称旧0.2.2的10k/50k/100k性能测量。
44. 最终26文件指纹 `086561892d7fb844617c055e990ab0a1c2a5f5cace747a0a93f15e41239f583e`，镜像 `sha256:55ae36b4e68dec8700ea722d30fc94e8eda665a756c038587f59279597e228ed`，保留v23标签并首次创建 `20261018.1-rc2`；`evidence/release-v23-freeze.json` 绑定源码、构建配方、依赖及实际日志。测试容器清理为0，仅原8生产容器，未动数据卷；远端重型队列已交回根任务用于其余服务及stage。Ops独立配置/日志8项和runner安全4项本机通过，新模块已加入CI和真实PG/S3 runner，Ops最终Linux验收由根任务串行完成。
