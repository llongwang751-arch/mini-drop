# 全量交付与验收矩阵

**2026-10-19 继续开发（进行中）**：用户选择以tx-drop继续完整平台，吸收drop-drop本地开发与视觉设计，要求最终部署，并允许清理旧应用/临时构建。历史未完成范围继续有效，见 [本轮跟踪](CONTINUATION_20261019.md)。新本地启动隔离7项、Web133单测/新增3浏览器实际通过；当前Core源码Linux严格构建已完成，高级采集/完整浏览器/stage/发布仍在验证。此段不表示生产已升级，正式运行版本仍以下方已有发布证据为准。

更新日期：2026-10-18。依据根目录《Drop 性能诊断系统架构与复刻指南》，保持 C++20 / Go / Python / React / PostgreSQL / S3 技术栈。上一轮以核心闭环作为交付范围不符合用户的全量要求，本轮已纠正。腾讯内部源码和私有运行环境未提供；这里实现的是指南描述的能力。

当前状态（2026-10-18 17:35 CST）：按用户“先上线部署吧，后面再说”完成 **`20261018.1-limited` 有限发布**。公网 [http://120.24.187.205](http://120.24.187.205) 已切换 API rc4 / Analyzer rc4 / Web rc7 / 内部 S3 网关 rc2，Core/Agent 继续 `20261016.1`；SQL schema 5。**全量指南尚未全部完成验收**，未完成项继续保留。旧版和隔离 fixture 证据各自按原版本解读。

| 本次上线范围 | 实际生产证据 | 边界 |
|---|---|---|
| Web rc7 `f42925a0…fc41` | UID101 真实 HTTP 校验14份资源及SHA；公网入口脚本SHA一致；375/1440浏览器10组页面、20张脱敏截图，gzip Worker火焰图与旧任务结果实际可读 | rc6业务bundle字节不变，仅修目录权限；partial真实故障链、全路由全功能stage仍待验 |
| API rc4 / Analyzer rc4 | SQL1→5迁移、23旧成功结果结构兼容；真实PROC_CPU任务26和明确标记导入任务27均COLLECTED/SUCCESS，RAW大小/SHA实际核验 | 当前旧Agent仅声明PROC_CPU/PERF_CPU；本轮未重跑现场perf，不套用历史perf到新镜像 |
| 发布与数据保护 | 新鲜在线备份115对象/1,510,006B及隔离恢复；实际回滚后重发；四个数据/采集容器身份不变；最终8容器运行，25tasks/125artifacts，默认R2 overlay持久化 | 同机备份；不包括生产SSE迁移、定时异地副本或新Ops |
| 安全检查 | API/Web rc7/网关各0Critical/0High；原始扫描报告保留 | Analyzer仍57Critical/329High匹配，整体安全门禁未过；入口仍HTTP |

精确镜像、部署证明和回滚步骤见 [有限发布说明](releases/20261018.1-limited.md)；实际结果见 [release-proof.json](evidence/releases/20261018.1-limited/release-proof.json)、[运行快照](evidence/releases/20261018.1-limited/runtime-snapshot.json)、[HTTP闭环](evidence/releases/20261018.1-limited/controlled-smoke.json)、[生产浏览器](evidence/releases/20261018.1-limited/production-browser-smoke.json)。本次仅先发布上述范围，下方完整要求与剩余工作继续有效。

**上线前全量补齐快照（2026-10-18，保留历史范围）**：§8.6失败后部分分析结果已补入Analyzer/API/Web：解析成功后保存独立16KiB有界快照，当前attempt/job/run/RAW id+digest绑定，lease前后栅栏，重试清空、成功覆盖，单独`/partial-result`与未完成UI；共享Schema/fixture冻结，无SQL/Proto变动。下表为有限发布之前的候选验证和当时待办，实际线上版本以上方新记录为准。

| 当前候选 | 已实际通过 | 仍待完成 |
|---|---|---|
| API rc4 `94ad66ca…bc47` | 76单测、三套真实PG零skip、vet/gofmt/双架构编译；govulncheck called/imported0；902组件SBOM，Grype0Critical/0High、exit0（3Medium/1Unknown） | 真实整站stage、升级与生产发布；单测环境另有19项无DB skip，不能计入PG实证 |
| Web rc6 `8f3ead04…8053` | 131单测、28原生浏览器49.25秒/零skip零flaky、7下载辅助、375/1440目检；1282组件SBOM，Grype0Critical/0High、exit0（3Medium）；UID101/zlib1.3.2-r1 | 真实授权下载、partial故障链、完整页面/系统stage及公网升级 |
| Analyzer rc4 `b9e6b659…f136` | 本机23相关回归/22真实PG；121/121真实Linux/PG/S3，64.033秒、零skip；实际perf script91/data93、18格式、整cgroup512MiB/noSwap的10k/50k/100k及100001拒绝（峰158076928B）通过，fresh ELF编译成功；6368组件新SBOM/实际扫描完成 | BOLT的instrument子工具exit1，尚无fdata/优化/等价结论；原harness未保存子工具stderr，需独立有界诊断。Grype **57Critical/329High**、exit2，需逐项核验处置，不能因linux-perf源码包映射而整批忽略 |
| Ops rc4 `e8d95a64…c7d7` | 42项PG/S3与五阶段SSE、2835组件SBOM | Grype0Critical/**61High**，exit2，安全门禁未过；生产加密迁移/密钥备份/升级后恢复仍待执行 |
| 平台Nginx rc2 `f76c617f…6cee` | UID101/zlib1.3.2-r1；1282组件SBOM，Grype0Critical/0High、exit0（3Medium） | 真实stage网关、受信任IP HTTPS和证书发布/续期回滚 |
| Core/helper r18 | Release/Werror构建、16/16 CTest及6项Linux仪表化回归 | Python内存及alloc正常结束/恢复未过；其他语言在最终helper下的实采、恢复/RPC/N-N1与Core/Agent双镜像冻结 |

实际扫描使用同一校验过的Grype0.120.1和2026-10-17漏洞库；API/Web/平台Nginx三个镜像High/Critical门禁通过，Analyzer/Ops两个未通过，Core/Agent最终镜像尚未冻结。匹配数不等于独立可利用漏洞数；初步来源核对见`docs/evidence/supplychain/analyzer-rc4-initial-triage.md`，不作清零或整套通过结论。此处记录为上线前快照；后续有限迁移与切换见本页开头。

**最新核对（2026-10-18 15:55 CST）**：Web rc5 已冻结98份源码与14份bundle，实际110项单测、23项原生Chromium检查（39.187秒，0skip/0flaky）、7项下载辅助回归通过，生产依赖审计100项/0漏洞；根代理已目检最终375/1440失败页与Markdown页。新增七阶段进度、当前attempt失败详情/可下载制品、DOMPurify最终HTML清洗。其部署包SHA `69fb3748…5126`，新镜像及真实全站stage尚待执行，不能套用rc4镜像指纹。Core新helper的r14 Release/Werror实际16/16 CTest和独立6类上传恢复已有证据；高级采集此前7/9通过，Python内存与alloc/free仍在修复重验，最终Core/Agent尚未冻结。Ops/Analyzer安全修补候选已准备Python3.12.15、urllib3 2.8.0、纯psycopg3.3.6与系统libpq，新构建/真实回归/安全扫描均未完成。工具基镜像的诊断SBOM扫描实际遭384MiB cgroup OOM，保留失败记录和私有工作目录，未当作扫描成功；后续需验证采集器崩溃恢复。07:47 UTC公网healthz 200和内部ready通过是一次探测，非SLA。下方rc2/rc4段落保留各自历史适用范围。

**恢复更新（2026-10-18，按现有证据核对）**：Core pre-helper快照通过11项CTest、双Server RPC/队列32正负控、6项上传恢复、双向N/N-1及7项目标验证；独立helper和跨UID接收目录仍在接入/安全回归，不能套用pre-helper证据。Analyzer当前0.2.3已冻结26文件SHA `08656189…583e`，候选镜像 `55ae36b4…28ed`：配置/日志增补后Linux真实PG/S3 **101/101、0skip、59.168秒**，perf6.12.95重新交叉解码实际Core RAW，script91/data93个样本分别匹配记录。整容器512MiB、禁swap的10k/50k/100k与100001拒绝是此前0.2.2快照的实测，容器峰值159076352字节；0.2.3未改解析/流水线/资源预算，但未重跑该规模基线，不冒作新版本容量测量。私有MinIO SSE v3五阶段已全部通过，尚未迁移生产。

Web当前rc2针对实截图中的浅图空白做了明确优化：最小SVG高度220→104，提示细帧仍保留并可搜索聚焦；**96项单测、21项本机原生Chromium检查、0skip/0flaky**，375/1440截图目检和axe零违规。部署包SHA `0741948c…19976`，根代理已实际构建rc2镜像 `3b7e61ce…4892`、运行用户101；最终rc2的SBOM/发布验收另计。随后仅E2E辅助修正Node fetch透明解压，真实loopback回归增至7项；全部dist/包字节未变，未重复冒报96/21。原 `43511ce3…6b38`和1282组件SBOM只对应rc1（94/21、旧包SHA `404203aa…14731`），原包/证据与镜像均保留。这些浏览器接口/对象均为显式导入fixture；真实全站E2E尚待隔离stage，执行时禁用trace/HAR/video。

随后针对Core实际只读确认rc2安装zlib1.3.2-r0，准备Nginx最终stage补丁；平台rc1实际构建在240秒墙钟限制结束，未产生新tag，失败结果保留。根代理又在原Nginx镜像的32MiB/no-swap/capless/UID65534/只读容器实际确认阿里云v3.24仓库HTTPS200（528376字节、约1.8秒、exit0），因此三recipe只将默认仓库主机替换mirrors.aliyun.com，保留原分支路径、签名验证、apk upgrade zlib及最终USER101。新Web rc4 context SHA `97e8c080…8d22`、平台rc2 SHA `ad57616d…4602`；14份dist仍为`fcc5bd71…0ed3`，没有重跑或改标96/21/7。旧Web rc2/rc3和平台rc1包均校验保留。**两镜像现已实际构建成功**：Web rc4 `09823c12…812a`、平台rc2 `f76c617f…6cee`，最终用户101；分别用24MiB、无网络、capless、只读容器实际确认zlib **1.3.2-r1**。六应用模块之外的第7基础设施镜像drop-nginx供两个网关共用；完整SBOM/Grype和stage运行验收仍另算，单个包修复不代替整体安全门禁。

**新的发布阻断项**：对Ops rc2 `d6453ecb…6068`实际生成3381组件SBOM（SHA `af658075…2919`），以校验过的Grype0.120.1及2026-10-17漏洞库离线扫描，得到Critical17/High156/Medium147/Low27/Negligible87，exit2，High/Critical门禁未通过。报告按镜像和SBOM指纹绑定；同源库可能有重复匹配，不能把匹配数当作独立可利用漏洞数，也不能据此跳过修补。Python/urllib3及Debian依赖的官方修复版本与新候选正在核验，Ops原42项功能回归不证明安全门禁通过。生产未替换。

升级前生产schema1的23任务/115制品已在线备份并通过隔离恢复（115对象、1510006字节、metadata/gzip核验，恢复5.76秒）；另有一次SSH流式取回开发机的AES256GCM加密异地主机副本及认证解密哈希验证。该副本不是定时外部复制或托管vault。独立stage的2GiB ext4接收盘也已实测UID10001超额ENOSPC28，不能替代helper协议和目标写入验收。完整新版本发布、受信任公网HTTPS、生产存量加密及升级后恢复、证书轮换与完整监控链仍待完成。**公网仍是backend 20261016.1 / web 20261016.3**，本文不提供缺乏依据的完成率；本次文档核对未访问服务器，上述运维事实引用根代理保存的实际报告。

## 指南逐项能力映射

| 指南范围 | 当前实现与入口 | 实际验证 / 待完成项 |
|---|---|---|
| 1、5～8 四模块和单向依赖 | core / api / analyzer / web 独立构建；REST/SSE、mTLS gRPC、SQL/S3 分离 | 四模块已有可运行镜像；全量最终源码和镜像指纹正在统一冻结 |
| 2.1、3.1 Agent 注册 | 稳定证书 CN、版本协商、动态能力、工具版本/SHA、配置版本、升级提示、目标权限策略 | 私有mTLS RPC通过；Agent2→Server1和Agent1→Server2两方向真实PROC_CPU/PUT/DB size与SHA匹配，2/2通过，含Agent切换Server与协议降级 |
| 2.2～2.6、9 领域对象 | Task/Attempt/Artifact/AnalysisJob/TaskEvent/AgentEvent、资源组、共享、复合运行、AI 请求和反馈独立持久化 | migration001/002/003 已在独立 PG17.11 执行；004 已在百万任务测试库重复执行并验证数据保留；Go/Python 真实事务测试通过 |
| 2.7～2.10、3、4 状态与协议 | 保留V1字段号；双状态机、attempt/lease fencing、幂等通知与outbox | Proto兼容检查通过；最新真实PG复现并修复最后RPC失败覆盖QUEUED/取消/新attempt的3种竞争。三套重新编译的PG回归12.428秒、零skip；仅在RPC后锁状态，不跨RPC持Task锁 |
| 4.5 上传恢复、5.3.2 优先级 | 按网络/429/5xx退避、永久4xx停止重试、有界保留RAW后纯上传恢复；priority -10..10 + FIFO | pre-helper双Server与005/Proto32通过；6项实际上传恢复覆盖原4项及manifest恢复/多制品顺序，核验PUT字节及SHA/size；独立helper恢复语义需重验 |
| 2.11、5.8、7.4 能力真源 | 18 个 TaskKind = 16 个 Agent 采集类型 + IMPORT_COLLAPSED + COMPOSITE；Schema/Runner/pipeline/平台/权限/预算声明 | 18项共享契约通过；不能将18项全部称作在线采样器 |
| 3.4、5.8～5.10 Runner | Validate/Prepare/Start/Poll/Stop/Collect/Cleanup 生命周期；argv执行、进程组、超时、取消、私有工作目录、工具完整性 | 最新pre-helper Linux Release/Werror与11/11 CTest通过；双Server队列与派发实际负控均能复现旧缺陷。helper新增后须重新冻结、复测及实采 |
| 5.11 真实业务目标 | PID/UID/starttime/namespace复验；Docker/containerd解析、精确容器白名单；普通主机systemd和可选DaemonSet | 已验证独立宿主/容器内PID映射、白名单/UID/starttime拒绝共7项；生产节点保持明确授权范围 |
| perf / procfs | perf CPU火焰图、线程CPU tick采样；原生perf_script/perf_data导入及真实线程/时间/DSO/lost记录 | 冻结Analyzer以perf6.12.95解析实际Core双线程RAW：script91/data93个样本，各自匹配原记录及线程/纳秒时钟；script缺失丢样证明保持未知，perf.data完整扫描可证明本记录丢样为0。独立helper全链重验待完成；procfs不冒称函数栈 |
| Java CPU / Heap | async-profiler collapsed；JFR导入；jcmd HPROF、独立进程对象图/GC roots/支配树/retained/引用路径 | 当前0.2.3 Linux101项含真实JFR/HPROF、五阶段进度及SIGKILL索引恢复；原真实asprof采集属于pre-helper边界。跨UID目标自写RAW接收/权限/预算须在新helper下实测 |
| Go CPU / Heap | 固定目标pprof端口/路径；protobuf、sample_type、labels与包含/排除选择 | 真实CPU/heap采集及原生解码通过；保留不同采样权重与单位 |
| Python CPU / Memory | py-spy与memray attach；调用栈/分配热点/存活与释放 | pre-helper证据保留；新helper下Python CPU已实采。r18实际确认memray的访问检查、dlopen/符号加载均完成，阻塞在SPAWN_CLIENT_BEGIN后，未成功结束；强制结束debugger后toy退出，恢复失败，不作为可用能力验收 |
| gperftools / jemalloc | 业务集成C ABI控制库，真实CPU/heap dump；可选ELF符号附件 | 三种真实原生Runner RAW及Analyzer交叉解析通过；无符号文件时保留地址，不伪造函数名 |
| I/O / 虚拟内存 | 受目标namespace/PID限定的BPF block/file/alloc/free/pagefault Runner；延迟分位、吞吐、设备/进程/文件、队列和时间序列 | 新helper已有实际I/O与pagefault/mmap证据；alloc/free的r18 perf/raw两对照均产出172alloc+172free/344栈块，但20秒仍不退出，需TERM/KILL，target恢复未通过；不能以采到数据替代生命周期验收。未使用SYS_ADMIN/privileged绕过 |
| BOLT | perf + perf2bolt、真实fdata、ELF元数据与可选llvm-bolt产物 | pre-helper真实cycles:u→no_lbr fdata已通过；冻结Analyzer真实工具产物与toy等价回归通过。新helper采集/ELF传输待重验，不承诺业务收益 |
| Script / Resource | 固定受控模板；CPU/RSS/I/O/上下文切换/网络namespace/采集器开销/受权限约束dmesg | 原Runner采集/解析通过；扩展资源字段新增回归中；无日志权限明确 unavailable，不显示假空成功 |
| 3.8、4.9 复合任务 | 最多16节点DAG、依赖、必选/可选、并行/截止时间、部分成功与汇总报告 | Go真实PG的环/依赖/部分失败/超时取消回归通过；浏览器创建/报告联调待完成 |
| 3.9、6.11 计划任务 | 五段Cron或固定间隔、IANA时区/DST、不可变模板快照、唯一时点与跳过错过时点 | Go真实PG及DST春跳/秋重复测试通过；Cron页面创建验收待完成 |
| 3.10、7.10、8.9 规则/AI | 可配置版本规则；明确同意后发送去标识TopN；真实供应商SSE增量、请求恢复、建议反馈、授权与attempt/lease fencing | 规则/AI流式隔离测试通过；真实浏览器导入与规则反馈已通过；没有配置外部模型时UI明确禁用 |
| 6.9、11.2 权限 | 本地账号、OIDC PKCE/nonce/state/JWKS；platform/group角色、资源组、read/operate共享；同一查询范围覆盖结果/事件/SSE/制品 | 真实OIDC/RSA与HTTP/PG权限矩阵通过；浏览器read-share验收继续，404隐藏不可操作对象的存在性 |
| 6.7～6.10 REST/制品 | OpenAPI3.1、统一envelope/字符串ID、Schema、共享限流；按次授权短期URL、过期拒绝、软删除/再分析；GET grant签入identity编码覆盖以保留原始字节 | 60个实际操作、73Schema、18共享参数Schema一致校验通过；浏览器8类原生结果逐件下载为历史快照。新API rc3独立SigV4回归实际先红后绿，移除identity使签名失效，TTL/路径不变；存CE:gzip对象的真实MinIO/浏览器验证仍待stage |
| 2.4、4.4、8.3 采集日志 | 固定runner.log、LOG/log_jsonl、16KiB限额，只允许列表诊断字段，不复制profile或工具原始stderr | API当前attempt/终态120s/清理与presign竞争真实PG回归通过，三套共17.358秒零skip；Core RPC成功/失败/迟到LOG与安全grant回归通过，实际故障上传继续验证 |
| 7.3、7.11 分析隔离 | 独立子进程、CPU/内存/墙钟/文件上限，small/high_memory队列，checkpoint恢复，当前attempt/owner/lease事务提交 | 当前0.2.3镜像55ae36b4…的101项Linux真实PG/S3通过、0skip、59.168秒。0.2.2整容器512MiB/no-swap的10k/50k/100k峰159076352字节、无OOM，100001拒绝；未重跑成0.2.3规模数据。单次星形fixture不是所有拓扑/工具的容量保证 |
| 7.11～7.12 总盘量 | RAW+aux合计32MiB、job目录192MiB、最小余量16MiB；50ms聚合目录/HPROF索引增量、拒绝链接/特殊文件并终止超限进程组 | 最新Linux101项含真实子进程总盘量超限终止、下载余量检查、symlink/FIFO拒绝、租约回归；RLIMIT_FSIZE或tmpfs不代替总量预算 |
| 8 Web工作台 | 浅色仪表台；全类型导入/采集、嵌套Schema表单、目标/资源组、Agent证据、规则/共享/复合/Cron/成员管理、类型专属结果 | 当前rc5实际110单测/23本机Chromium、0skip/0flaky及生产构建通过，375/1440目检/axe通过；下载辅助7项。浏览器对象均显式标记导入fixture，历史8类实际后端原生结果单独保留；当前整站真实E2E仍待执行 |
| 8.3～8.5 列表与创建 | 最多50项批量操作/逐项失败反馈、两任务完整调用树对比、按已选Agent过滤类型 | 新增11项纯回归与显式导入fixture浏览器375/1440、4次axe检查通过；真实共享/批量API全栈仍需执行 |
| 8.4、8.7 请求生命周期 | 默认20秒/正文超时、Query取消、连续失败跨周期退避；有界fetch-SSE精确游标/可见性/卸载Abort | 当前rc5的110/23统一验证含轮询退避/恢复、隐藏暂停、native Abort、SSE重连/精确游标/403停止；时钟/可见性模拟明确标注，真实全栈待验 |
| 8.8 火焰图 | 授权短URL→有界ArrayBuffer→独立Worker gzip/UTF8/JSON/100k-depth128校验；布局Worker、SVG裁剪、高级筛选/联动、键盘与对比 | 当前23项统一浏览器含gzip完整100k、精确过期仅刷新一次、403/410不重试、损坏/外链拒绝、卸载/超时与内联回退；10k/50k/100k全节点及375/1440通过。真实授权下载/批量对比仍待stage；Analyzer规模另有独立512MiB证据 |
| 9.7 生命周期 | 原始14天/中间3天/结果与binary90天/日志14天/manifest90天；过期/缺失/hash隔离、孤立对象、未完成分片有界清理 | 维护模块独立真实PG/S3集成5项为历史快照；最新binary前缀/90天策略离线回归通过，需绑定最终Ops镜像与部署复验；MinIO不支持分片生命周期XML时显式签名abort |
| 9.8 服务端加密 | 独立KMS Secret文件、显式bucket AES256默认规则与自动加密；整bucket存量对象条件复制/内容哈希校验工具 | 私有MinIO v3五阶段20.141秒全部通过，含gzip编码元数据保留、错误密钥3对象拒绝及恢复后原SHA/size；独立8项真实PG锁/游标回归通过。生产dry-run/apply、独立密钥备份及生产恢复仍待执行 |
| 10 Compose / 运维 | 保留现有perf profile，增加全工具/受限BPF/监控/HTTPS；非root、cap drop、只读根、日志/内存/CPU预算 | dropfullstage独立DB/S3/CA联调，不替换生产；本轮并发测试导致I/O争用后已暂停新负载并改为串行 |
| 11.3 Agent权限隔离 | UID10001无cap Agent与独立root helper、有界typed UDS/SO_PEERCRED；共享接收目录与文件冻结方案仍在修改 | WIP已有代码和只读安全审查，尚无最终真实peer/恢复/ACK/取消/跨UID写入/预算验收。helper最终权限/目录/镜像须冻结并重做实采；pre-helper证据不代替此项 |
| 10.7～10.8 多副本/K8s | 外置DB/S3、API/Server/普通Analyzer多副本/PDB/HPA，独立高内存worker；可选最小权限Agent DaemonSet | 官方kubeconform0.7.0+K8s1.34.1严格验证21/21，0错误/0跳过；没有真实集群，不能宣称HA/节点故障演练已通过 |
| 11.5 证书与Secret | 文件挂载、Agent无长期对象密钥；同信任根叶证书轮换、受保护旧叶窗口及失败回滚；IP HTTPS/续期配置 | 原mTLS/角色/fencing通过；新轮换脚本和受信任公网HTTPS实际发布验证待完成 |
| 11.7～11.9 监控 | 四模块指标、OTLP traceparent、Prometheus/Grafana/Tempo、18条告警及受保护内部审计webhook | 真实服务启动为早期快照；promtool修复3处漏报后18规则/12断言通过。Server私有OTLP接收已实测queue/dispatch及安全links；完整Tempo查询、告警触发/恢复审计和低内存持续运行待完成 |
| 11.10 审计 | 登录/权限/任务/下载/Agent/规则/计划等审计；runtime audit表追加权限 | 新独立PG最小权限20项通过，audit INSERT可用、UPDATE/DELETE/TRUNCATE拒绝；不冒称外部WORM |
| 12 故障注入/性能 | 原有取消重试/SIGKILL/租约/outbox测试保留；100k/百万Task/HTTP/SSE基线 | 百万Task最小索引/004幂等迁移和24创建/60查询/20SSE已有本机证据；Analyzer 100k整容器512MiB及浏览器10k/50k/100k已完成。最终helper/全栈恢复、心跳容量/P99、Agent空闲RSS/CPU和上传吞吐等基线仍待补，小机器实测不作SLA |
| 13.7 供应链与发布 | 六镜像release工作流、多架构、SBOM/签名、回滚与备份恢复流程 | 902组件API SBOM只对应旧镜像；最新API已做amd64/arm64交叉编译，Analyzer amd64候选已冻结。最终六镜像SBOM/签名/安全扫描及ARM实际运行仍待完成，远端CI/registry信任不能用配置代替 |
| 13.7 升级前备份 | schema1生产在线备份、隔离PostgreSQL/S3恢复、单次异地主机加密副本 | 最新23任务/115对象全量SHA/size/metadata恢复5.76秒通过；开发机副本AES256GCM认证解密hash通过。生产未迁移/重启；升级后新schema/SSE恢复和定时外部复制仍待执行 |
| 10.4、11.6 配置与统一日志 | API原生Go和Analyzer原生Python均已补CLI>env>JSON>默认、Secret `_FILE`兼容、有效配置安全投影/hash与12公共日志字段；Core对应增补仍在验证 | API 13包/54项单测、go vet、真实PG3套0skip、schema5实际migration入口两次及实际Windows日志入口通过；根代理实际构建API rc2 `6e87244…`，Linux配置/FIFO/投影Secret链接6项全过。Analyzer0.2.3的配置优先级、Secret安全投影、12字段/第三方日志脱敏等已纳入Linux101/101；Core与全栈关联仍待验 |

## 证据入口

- `api/benchmarks/full-integration-2026-10-16.json`、`full-build-proof-2026-10-16.json`：Go真实集成和冻结镜像源码/二进制指纹。
- `analyzer/evidence/README.md`：各轮测试与native/真实Runner交叉解码、BOLT toy等价证据。70项报告只对应已标注0.2.1快照，100k增补另算。
- `analyzer/evidence/release-v23-freeze.json`：当前0.2.3镜像55ae36b4…、26文件及完整证据指纹；`integration-v23-linux.json`为101/101、59.168秒，`perf-runner-v23.json`为当前版本重新交叉解码。`release-v22-freeze.json`及`integration-v22-perf612-linux.json`保留此前0.2.2的92/92、58.459秒历史快照。
- `analyzer/evidence/perf-runner-v22.json`、`benchmark-v22-512m.json`、`benchmark-v22-container.json`：真实Core双线程RAW交叉解码；整容器512MiB、swap0、10k/50k/100k及100001拒绝，非业务压测。
- `docs/evidence/full/native-browser-results.json`：本轮8类原生RAW结果、实际制品数及来源；对应截图同目录。
- `docs/evidence/full/flame-explorer-fixtures/report.json`：高级火焰图的本地导入fixture浏览器交互/375与1440可访问性；不代表真实采集或后端联通。
- `docs/evidence/full/flame-scale-browser/report.json`：10k/50k/100k完整节点、原生Worker、离屏搜索、聚焦及滚动/键盘实际浏览器基线。附源码和bundle SHA，主renderer堆抽样不含Worker、不是容器RSS。
- `docs/evidence/full/web-unified-source-freeze.json`、`web-runtime-context.json`：rc2的96单测/21浏览器及全部bundle/部署包指纹；原运行冻结保存在`web-rc2-runtime-source-freeze.json`。`safe-stage-raw-io-2026-10-18.json`记录后续仅E2E辅助的gzip透明解压负控/修复与7项loopback检查，dist/包未变。`profile-artifact-fixtures/report.json`为gzip授权链10项导入fixture，含紧凑浅图与隐藏叶帧搜索聚焦；`web-rc1-evidence-preserved.json`保留rc1，`compact-shallow-before.png`保留220高度负控。
- `core/evidence/resume-pre-helper-20261018.json`：11项CTest、双Server正负控、6项上传恢复、N/N-1、目标与BPF/BOLT实际采集的pre-helper适用范围；明确不作为新helper的最终验收。
- `api/benchmarks/integration-log-grants-windows-2026-10-17.json`：最新repository/service/httpapi真实PG17.11回归，覆盖005优先级、LOG白名单/终态/竞争；零skip，Windows环境不冒充Linux。
- `api/benchmarks/integration-dispatch-race-windows-2026-10-17.json`及red报告：再增RPC响应丢失/取消/重试竞争的最新三套真实PG回归，覆盖此前LOG/优先级项；最终API交叉编译SHA绑定该报告。
- `api/benchmarks/integration-schema-guard-windows-2026-10-17.json`及red报告：又补取消幂等及schema5门禁，当前三套PG全部通过、13.689秒、零skip；最终跨编译报告绑定此快照，采集manifest新增授权后仍须重验。
- `api/benchmarks/release-cross-build-2026-10-18.json`：当前79文件配置/日志增补、13包/54项通过（无DB环境另有18项skip，真实PG另报）、go vet、amd64/arm64二进制冻结；`integration-config-logging-windows-2026-10-18.json`为重新编译后的3套真实PG、0skip、16.873秒，`migration-config-entrypoint-windows-2026-10-18.json`及`config-logging-entrypoint-proof-2026-10-18.json`验证实际API入口两次migration和12日志字段/CLI秘密拒绝。
- `api/benchmarks/release-cross-build-rc3-2026-10-18.json`：其后仅storage签名/测试2文件变化，新原始字节identity覆盖；13包55项单测、18项无DBskip、vet、实际入口及两架构重新冻结。生命周期/SQL/config未变，之前真实PG证据原样引用，未重标为rc3新跑。独立rc3 context SHA `9621c3f4…3a58`，真实MinIO和新镜像待验。
- `docs/evidence/full/db-permissions.json`：实际20项运行角色权限检查。
- `docs/evidence/full/alert-rule-tests.json`：官方校验过的promtool实际触发/恢复断言，明确不代替线上告警投递；规则单测格式依据[Prometheus官方文档](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)。
- `ops/evidence/encryption-postgres.json`：8项真实PG17.11锁/游标/状态/超时回归，S3为Fake；完整MinIO加密实测另行记录。
- `ops/evidence/encryption-minio-v3.json`：实际私有MinIO五阶段SSE检查全过、20.141秒，含错误密钥拒绝/恢复与编码元数据；未访问生产对象，生产迁移与密钥独立备份另行验收。
- `api/benchmarks/million-task-minimal-index-windows-2026-10-16.json`：百万行、004幂等迁移、最小索引前后SQL计划及真实HTTP/SSE；报告保留本地Windows和资源上限，非公网吞吐证明。
- `docs/evidence/kubernetes-schema.json`：21对象严格Schema验证及文件SHA。
- `docs/evidence/supplychain/`：已实际生成的SBOM/签名公钥与扫描记录；私钥不在仓库中。
- `docs/evidence/full/host-sbom-offline-2026-10-18.json`：服务器本机六组件精确ID采集器20项纯fake-host/文件系统回归，覆盖scope/ID变化/去镜像Labels元数据/资源门禁/输出与清理边界；仅证明工具本机检查，真实六镜像Syft扫描及签名另算。
- `docs/evidence/full/tls-publication-offline-2026-10-18.json`：12项实际Git Bash/假命令事务回归和5份脚本语法；覆盖成对current切换、配置/重载/探测/rename失败恢复、双失败保留恢复文件、无变化续期及监控路径。Windows的ln/readlink明确模拟，Linux UID101/真实Nginx/内部轮换/公网签发仍待现场验收。
- `docs/evidence/full/web-runtime-dependency-audit-2026-10-18.json`：官方npm registry的生产依赖审计，实际npm退出0、117prod/0漏洞，绑定package/lock指纹；不代表Nginx/Alpine OS扫描。`nginx-upstream-metadata-2026-10-18.json`保存官方tag digest/时间和Alpine SecDB实际响应摘要，未推断rc2已修补或存在某漏洞。
- `docs/evidence/full/web-mirror-rc4-freeze.json`、`web-runtime-context-rc4.json`、`nginx-runtime-context-rc2.json`：只替换仓库主机的新候选/未变bundle和实际网络预检范围；新构建/包版本/扫描待执行。原`web-basepatch-rc3-freeze.json`及Web rc3/平台rc1 context报告保留；平台rc1实际240秒构建失败、无新tag。`stage-idle-agent-prepared-2026-10-18.json`只有9项本机fixture，真实60秒Agent主进程/工作负载子进程/容器资源仍待采样。
- `docs/evidence/supplychain/drop-web-20261018-amd64.cdx.record.json`：新Web候选镜像43511ce3…与1282组件SBOM，未代表上线。
- `docs/evidence/full/runtime-images-rc2-2026-10-18.json`、`web-runtime-image-rc2-2026-10-18.json`：根代理实际构建API6e87244a…/Opsd6453ecb…/Web3b7e61ce…候选镜像；Linux API配置6项、Ops隔离回归42项及构建输入SHA。没有更换生产。
- `docs/evidence/full/preupgrade-backup-2026-10-18.json`、`preupgrade-restore-2026-10-18.json`、`preupgrade-offsite-2026-10-18.json`：生产升级前实际备份/隔离恢复/单次异地主机密文副本；后者明确没有定时复制。
- `docs/evidence/full/helper-spool-stage-2026-10-18.json`：独立stage 2GiB ext4/权限/UID10001超额ENOSPC28；不代替helper与真实Profiler联调。
- `docs/evidence/release-20261016.1.json`等历史发布证据保留，不能代表本轮全量版本已上线。

## 外部配置与环境边界

企业身份源、模型服务、异地备份目的地未由用户提供，代码/隔离测试和真实企业接通分别记录。当前单机2vCPU/约3.42GiB不具备物理多节点HA条件。内核权限、工具存在、目标可采与分析成功是不同状态，Agent只按真实探测与目标校验宣布能力。导入始终标注source=import，未知采样率/丢样/时长不补造。所有本轮未完成验收会继续推进；本文不是全量完成声明。

- `docs/evidence/full/stage-browser-sequence-prepared-2026-10-18.json`与`stage-browser-execution-plan-2026-10-18.md`：固定stage的CE元数据seed→浏览器原压缩bytes/Worker→Full/System12项→synthetic10k/50k/100k联合入口，9项纯metadata/假subprocess回归通过，未启动browser；startup不推断扫描通过。`web-guideline-readonly-review-2026-10-18.md`记录§8.6失败结构字段/partial分析视图、末事件check图标限制及§8.9未用DOMPurify的依赖差异；未宣称逐字完整或获奖认证。
