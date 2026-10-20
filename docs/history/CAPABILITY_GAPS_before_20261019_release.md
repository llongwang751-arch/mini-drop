# 全量实现的剩余工作与外部配置

更新：2026-10-18。范围仍为根目录《Drop 性能诊断系统架构与复刻指南》的全部能力。以下区分实际隔离验证、尚需实现或验收的要求与外部接入条件，不提供缺乏依据的完成率，也不把未验收项视作用户同意删减范围。逐项入口见 [DELIVERY_MATRIX.md](DELIVERY_MATRIX.md)。

**当前线上（2026-10-18 17:35 CST）**：用户要求先部署，已完成 `20261018.1-limited`，入口 [http://120.24.187.205](http://120.24.187.205)。线上为 API rc4 / Analyzer rc4 / Web rc7 / 内部网关 rc2；保留原 Core/Agent `20261016.1` 与数据容器。迁移 schema 5，真实 PROC_CPU 与明确标记的导入任务、RAW 校验、375/1440 生产浏览器及旧结果兼容检查通过，8 容器运行，配置已持久化。该事实替代下方历史“尚未上线”的时点描述。

**仍未完成**：最终 helper 与高级现场采集、完整功能 stage/partial 故障全链、新 BOLT toy 工具验证、Analyzer 安全处置（57 Critical / 329 High 匹配）、未部署 Ops 的 61 High 处置、HTTPS/生产加密与密钥运维、定时异地备份、完整监控/恢复、多架构与 HA、真实企业身份源/模型服务接入。用户仅要求先发布当前可用范围，没有取消这些后续工作。详细上线范围和回滚见 [有限发布说明](releases/20261018.1-limited.md)。

## 上线前核对记录（保留原时间与版本范围）

最新：§8.6 partial分析摘要已补入三模块，不再只展示保存的原始制品；API rc4实际76单测/三套PG0skip、Web rc6实际131单测/28浏览器/7下载辅助通过，候选镜像分别已构建为`94ad66ca…bc47`/`8f3ead04…8053`，尚未上线。Analyzer rc4的121项真实Linux/PG/S3已通过（64.033秒、0skip）；修正验证驱动路径后的resume2在最后case_bolt失败，完整工具/规模验收仍未全过。其新6368组件SBOM实际扫描为57Critical/329High、exit2，待按源码/包/实际调用链逐项核验处置，不作整批误报认定。API/Web/平台Nginx三者扫描均0Critical/0High、exit0，各有3Medium，API另有1Unknown；Ops rc4实际42测试/5SSE和SBOM成功，但仍有61High（0Critical），整体安全门禁未过。Core r18的16CTest及6项诊断回归绿，Python内存/alloc实采及正常退出/恢复仍未过，后续Java/native高级实采和最终helper全链也需验收。整套stage、生产HTTPS/加密/证书与监控恢复验收尚未完成，公网仍旧版；早期结果按历史范围保留。

公网仍运行 backend **20261016.1** / web **20261016.3**；下述新候选没有因此自动上线。08:38 UTC一次公网与内部ready检查通过；后续API/Web候选构建还核验生产非空容器ID前后未变，未进行生产切换，均不作为持续可用性证明。

最新补充（2026-10-18 15:55 CST）：Web rc5已实际完成110单测/23浏览器检查/7下载辅助、生产依赖100项0漏洞，含七阶段进度、当前执行失败详情/制品与DOMPurify最终清洗；真实stage和新镜像待验。新helper的r14 CTest16/16及6类恢复通过，高级采集仍有Python内存/alloc-free未过，后续Java/本地内存工具与最终全链还需验证。Ops/Analyzer安全修补候选已准备但未构建/复测。384MiB工具基镜像SBOM扫描OOM已真实记录，未宣布安全门禁通过；旧锁/工作目录仅做元数据取证、没有删除。公网07:47 UTC一次健康检查通过，生产没有切换。以下旧版本结果按其原范围保留。

## 保留的早期验证证据（按各自版本解读）

| 范围 | 已验证内容与边界 |
|---|---|
| Core pre-helper | Linux Werror/11项CTest、双Server队列32正负控、6项实际上传恢复、N/N-1双向PROC_CPU、7项目标身份验证；BOLT及BPF syscalls/vfs/block/pagefault/mmap实际采集通过。均为独立helper接入前快照，不能套用于新权限边界。 |
| Analyzer 0.2.3 | `release-v23-freeze.json`绑定26文件SHA `08656189…583e`、amd64镜像 `55ae36b4…28ed`；配置/日志增补后Linux真实PG/S3 **101/101、0skip、59.168秒**。perf6.12.95重新交叉解析实际Core RAW，script91/data93个样本分别匹配各自记录，保留线程/纳秒/DSO和有证据的丢样状态。 |
| Analyzer规模 | 此前0.2.2整容器512MiB、swap0、1CPU、无网络，10k/50k/100k星形导入fixture流水线为0.331639/0.732258/1.318464秒，容器峰159076352字节、无OOM；100001节点非重试拒绝。0.2.3未改变解析/流水线/预算但未重跑规模基线，不重标为0.2.3实测；不代表所有拓扑、工具或业务SLA。 |
| Web | 当前rc2统一96单测、21项本机原生Chromium、0skip/0flaky；375/1440目检、axe零违规、无文档溢出。包括完整100k gzip Worker加载、短URL仅精确过期重授一次及损坏/越权/超时边界；浅图SVG220→104、细帧说明和搜索末叶聚焦实际通过。API/S3明确为导入fixture，包SHA `0741948c…19976`；根代理已实际构建rc2镜像 `3b7e61ce…4892`、用户101，尚未切换公网。 |
| 安全E2E执行辅助 | Full/System spec后续增至7项实际loopback下载/错误脱敏回归：先复现Node fetch透明解压CE:gzip，再改原始HTTP有界流保留精确S3字节；相关spec TS/Prettier通过。runtime/dist/context不变，96/21没有重跑。真实stage须 `--trace off --reporter line`，不采集HAR/video，不保留签名URL/Cookie/原错误Call log。 |
| Nginx OS补丁 | 平台rc1构建超时失败保留；确认Aliyun仓库HTTPS连通后，三recipe仅换仓库hostname，保留分支/签名检查/apk upgrade和最终USER101。Web rc4 `09823c12…812a`、平台rc2 `f76c617f…6cee`已实际构建；分别用无网络、capless、只读容器实测zlib1.3.2-r1。14份dist不变，旧包/镜像均保留；完整SBOM/Grype和stage验收仍待完成。 |
| Ops实际安全门禁 | rc2 `d6453ecb…6068`已生成3381组件SBOM；Grype0.120.1、10月17日漏洞库离线扫描得到17Critical/156High等434条匹配、exit2，门禁失败。正在核对CPython/urllib3/Debian的实际包和官方修复；重复库匹配不等同于独立可利用漏洞，也不是忽略理由。旧42项功能检查不能代替安全检查。 |
| API配置/日志 | 原生Go CLI>env>JSON>默认和Secret引用、脱敏配置/hash与12公共日志字段已实现。当前79文件冻结；13包/54单测、go vet、3套真实PG0skip、实际schema5 migration入口两次与原值不泄露检查通过。amd64/arm64已重新冻结；根代理实际构建API rc2 `6e87244…`，Linux配置/FIFO/投影Secret链接6项通过；最终stage全栈关联仍待验。 |
| 原始制品下载修正 | API rc3仅改storage签名与测试：短URL签入response-content-encoding=identity，防止存量CE:gzip元数据令浏览器透明解压并破坏原SHA/size。独立SigV4回归先红后绿，移除override令签名无效，TTL/路径不变；13包55项单测、vet、实际配置入口与amd64/arm64重新冻结，未重跑未变生命周期的PG套件。新context SHA `9621c3f4…3a58`，实际MinIO/浏览器验证与新镜像仍待完成。 |
| SSE-S3 | 私有MinIO v3五阶段20.141秒通过：无加密legacy、同key条件复制AES256、默认加密新对象、错误密钥拒绝3对象、恢复密钥后原SHA/size；gzip编码元数据保留。PG锁/游标/状态检查另有8项真实PG证据；未执行生产迁移。 |
| 发布准备与备份 | Web rc2镜像已构建，最终SBOM/发布验收待完成；原rc1镜像43511ce3…及1282组件SBOM、原包/证据均保留，不套用到rc2。根代理本轮Ops rc2的42项真实PG/S3检查已通过。schema1生产23任务/115对象/1510006字节的升级前在线备份与隔离恢复5.76秒通过。开发机取得一次AES256GCM异地主机副本并验证认证解密hash，尚无定时外部复制。stage 2GiB ext4/UID10001超额ENOSPC28通过，只覆盖接收盘硬限额。 |

## 仍需实现或完成的本轮工作

| 指南要求 | 剩余工作 | 当前证据不能替代的部分 |
|---|---|---|
| §11.3 Agent专用用户/root helper | 冻结UID10001无cap Agent、typed UDS/SO_PEERCRED helper及最终权限；实际验证peer拒绝、target/PID重用、token幂等/恢复/ACK/取消、断流与限额；验证Java/memray目标自写RAW的共享接收/权限/冻结/磁盘预算。 | helper仍在修改；pre-helper测试、代码审查和工具已安装均不等于新隔离边界实测。 |
| §5.9 BPF完整能力 | 新helper已有BOLT、I/O、pagefault/mmap实际分项证据；alloc/free的r18 perf/raw对照均有172alloc+172free/344栈块，但20秒后仍不退出，正常收尾及target恢复未过，继续定位。 | 有采样输出不等于完整Runner生命周期成功；不扩大到SYS_ADMIN/privileged绕过。 |
| §5.11 容器目标 | 保留已测PID/UID/starttime/namespace映射；明确并验证跨容器mount/net路径与连接的安全支持范围。 | 当前默认Compose只允许受控进程，PID映射通过不等于自动进入任意容器namespace或所有采样器均可跨容器使用。 |
| §10.4 配置优先级 | API本机/私有PG与Linux6项、Analyzer0.2.3 Linux101项中的配置增补均已完成；Core仍在冻结验证，继续最终stage检查普通配置/Secret引用及hash边界。 | `_FILE`读取单个Secret不是通用配置文件层；API/Analyzer完成不能代替Core证据。Secret值及其可猜摘要均不进入公开hash。 |
| §11.6 统一结构化日志 | API与Analyzer已补time/level/service/version/request_id/task_id/attempt_id/agent_id/stage/duration_ms/error_code/message；继续验证Core对应实现及全栈关联。 | API入口/HTTP与Analyzer第三方日志脱敏已有实际回归；全栈日志关联仍需stage，未知上下文为空不伪造。 |
| §8.6 失败与状态语义 | rc6已补独立partial分析摘要视图/API，最新attempt/run/input绑定，重试/重新分析清理；真实Analyzer→故障→API→Web全链仍待stage。 | 仅在完整parsed成功回传后发布有界摘要，不公开解析中途损坏数据或声称任务成功。当前/result仍仅最新成功；缺阶段事件保持未知。 |
| §8、§12.7 真实整站E2E | 用最终镜像恢复隔离stage，完成实际授权gzip下载、存CE:gzip对象identity签名覆盖、导入/采集/恢复、Cron/DAG创建与结果、read-share/批量/两任务对比、partial真实后阶段故障/重试、完整路由axe/键盘/响应式及真实100k。 | 当前本机131/28、7项安全辅助及历史8类原生结果下载只覆盖各自版本和导入fixture场景。 |
| §10、§13.7 完整发布 | 有限版本已上线；最终Core/Agent双镜像仍待冻结，新BOLT toy工具回归待完成；统一七镜像指纹、SBOM/安全处置/签名、完整stage与升级验收仍需完成。 | API rc4/Web rc7/网关rc2的精确SBOM与Grype High/Critical门禁已过；Analyzer仍57Critical/329High，Ops rc4仍61High，整套安全尚未过。当前上线保留旧Core/Agent、数据卷和命名镜像。 |
| §11.5、§13.7 HTTPS/证书 | 实际签发公网IP受信任HTTPS、切Secure Cookie并外部复验；验证内部叶证书轮换/旧叶窗口/失败回滚及定时任务。 | 配置/脚本存在不能代替真实证书、信任和续期验证。用户已选择公网IP，不要求先提供域名。 |
| §9.8、§13.7 生产数据保护 | 已完成升级前schema1生产备份/隔离恢复及一次加密异地主机副本；仍需生产整bucket dry-run/apply、升级后新schema/SSE的备份恢复、KMS主密钥独立备份及保留/清理定时任务。 | 私有MinIO v3和升级前备份不能证明生产已加密，单次人工异地副本不等于定时复制或托管vault。 |
| §11.7～11.9 监控 | 最终配置下查询API→Server→Agent→Analyzer span links，实际告警触发/恢复及内部审计投递，验证Dashboard与小内存持续运行。 | 18条promtool规则/12断言和私有Server OTLP接收已过，不等于完整Tempo链、实时告警和生产投递已过。 |
| §12.6、§12.8 恢复/容量 | 最终helper/全栈故障恢复；补齐心跳数量/P99、入出队延迟、Agent空闲RSS/CPU、每类Runner开销、RAW大小/分钟及上传吞吐等基线。 | 已有百万Task SQL/HTTP/SSE和Analyzer/浏览器100k基线是分项证据，不给WAN吞吐、心跳容量或生产SLA结论。 |
| §13.7 多架构 | 最终多架构构建和ARM运行/工具验证。 | API amd64/arm64交叉编译成功，不代表ARM镜像或采样工具已实际运行。 |

## 需要外部配置或环境才能真实接通

- 企业登录：OIDC签名/JWKS、nonce/state/PKCE和权限隔离测试已有；真实企业身份平台尚未提供。
- AI建议：模型服务地址、模型名和Secret凭据尚未提供；当前无外部模型时页面明确禁用，规则诊断独立可用。未擅自接付费服务。
- 异地备份：需要另一台存储或云端目的地。可以继续验证本地恢复，不能把同机另一目录称作异地。
- 高可用集群：K8s清单21对象严格Schema通过；仅有一台服务器，未运行真实多节点故障演练。
- 远端CI/registry信任：没有Git远端/registry身份；离线manifest签名与实际CI/registry签名分别记录。

这些条件不取消已授权的实现、隔离验收或单机上线工作。

## 数据解释边界

- 导入数据明确标记来源，平台不能验证其采样现场；缺失指标保留未知。
- CPU样本、存活对象和规则相关信号不直接证明锁等待、泄漏或业务收益；BOLT产物需业务验证。
- 网络计数按实际network namespace范围解释；上下文切换按RAW中的计数范围解释；无dmesg权限给出原因。
- 旧MinIO社区二进制的网关、非root和网络隔离不等于上游漏洞全部消除；SSE-S3保护静态对象内容，不写成第三方漏洞修复。
