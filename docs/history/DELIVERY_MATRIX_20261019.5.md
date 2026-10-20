> 历史快照：以下文字保留迁移前状态。当前目标与验收以 ../DELIVERY_MATRIX.md 为准。

# 全量交付与验收矩阵

更新：2026-10-20。最近验收的生产版本为 **20261019.5-platform**：[https://120.24.187.205](https://120.24.187.205)。以tx-drop继续完整平台并吸收drop-drop开发/视觉设计。企业登录、异地备份展示保持移除；用户本次明确要求新增AI诊断，20261020.1候选已实现，尚未部署。范围以共享契约和指南为准，下面区分实现、真实验收和剩余边界。

完整镜像、版本、回退和实际检查见[发布说明](releases/20261019.5-platform.md)；[部署证据](evidence/upgrade-20261019/deployment-proof.json)绑定最终16容器及保留数据卷。历史快照见[原矩阵](history/DELIVERY_MATRIX_before_20261019_release.md)，其中“当前/待完成”仅代表其日期，不再覆盖本页。

本次诊断 Agent 候选见 [DIAGNOSIS_AGENT](DIAGNOSIS_AGENT.md)：4个受控工具、DeepSeek真实工具调用、本地证据模式、持久化证据/报告、取消/租约恢复、独立确认复测。已通过隔离PG及实际模型联调（导入样本）、HTTP权限和5个浏览器流程，Web133单测通过。新增schema6及65操作/82 Schema契约。SSH在认证前断开，22/80连接后无服务响应，本机尚无法切换服务器；不能将本机验收当作上线验收。密钥仅存忽略目录供安全部署，不在报告中。

| 指南范围 | 当前交付及实际证据 | 验收边界 |
|---|---|---|
| 四模块/契约 | C++、Go、Python、React独立；18 TaskKind、61 HTTP操作/75 Schema、Proto兼容实际检查通过 | 18类型含IMPORT_COLLAPSED与COMPOSITE，不是18采样器 |
| 注册/目标/状态 | mTLS、版本协商、能力探测、PID/UID/starttime/namespace、attempt/lease栅栏、事务事件/Outbox | 生产仅授权Agent容器受控目标；不冒称任意业务主机可采 |
| Core/最小权限 | 最终Core5严格Release/Werror与16CTest及UID10001预算回归通过；Agent UID10001 cap0，helper独立无凭据、有界物理盘 | helper PERFMON/SYS_PTRACE/DAC_READ_SEARCH/KILL/BPF仅Agent容器PID namespace；无SYS_ADMIN/hostPID |
| 采集收尾/恢复 | 最终Core5的16类Agent/20真实场景全部完整采集上传分析，RAW hash/size与目标恢复通过；Python内存最终正负控、60秒基线及公网proc/perf通过；历史Core3九helper场景、Core2上传恢复/RPC分别保留 | 16类/20场景均为明确自有UID10001受控进程；历史RPC/上传恢复未冒称最终二进制全部重跑 |
| Python内存 | 真实跨UID附加、memray完整FileReader、RAW hash/ACK与目标恢复；最终Agent/S3/Analyzer成功 | 缺少跨UID信号权限在附加前PROFILER_PERMISSION_DENIED |
| 原生解析/结果 | 独立预算进程、原生perf/JFR/HPROF/pprof/memray/native解析与类型视图；stage8类实际文件导入/下载 | 导入source明确；业务现场未知指标不补造 |
| partial失败 | 真实Analyzer最终提交故障，checkpoint绑定attempt/run/RAW可读、result409；真实桌面/手机失败视图 | 不把摘要当完整成功；过期lease/重试清理保留 |
| BOLT | 真实工具与固定toy优化前后6次校验成功 | 不执行用户ELF、不承诺业务收益 |
| 权限/协作 | 本地登录、资源组、read/operate共享；真实stage read-share授权、批删/对比通过 | OIDC后端保留，展示取消，无外部企业接通声明 |
| 计划/复合 | Cron/IANA/DST、最多16节点DAG、真实PG竞争与stage创建/报告通过 | 不是跨机器调度容量结论 |
| 规则/反馈 | 版本规则、实际幂等导入/反馈通过 | AI界面和模型请求移除；后端保留，不宣称模型服务已接入 |
| Web/本地开发 | 133单测、构建、33浏览器、6stage；独立CLI8回归及Linux真实首次/重复启动 | Windows整栈未实跑；浏览器fixture明确导入 |
| 下载/编码 | 按次授权、短URL、真实hash/size；CE:gzip对象identity覆盖及Worker实际通过 | 签名URL/Cookie不写报告或HAR |
| 公网升级 | HTTPS、Secure Cookie、真实PROC_CPU/PERF_CPU成功，11路由×两宽度浏览器通过 | 生产数据卷、历史结果与命名镜像保留 |
| 存储保护 | SSE-S3旧对象维护迁移、升级前后实际备份/恢复、KMS独立及RSA加密恢复副本 | 定时同机备份不能称异地复制；展示已取消 |
| HTTPS/定时运维 | Let's Encrypt IP证书、13事务回归；stage真实内部换叶/故意错配回滚及PERF成功，生产CA不变换叶及公网proc/perf成功；4组timer/service检查通过 | CA信任根轮换需独立迁移；不把readiness回退扩大为任意掉电恢复 |
| 容器换址 | 真实重建stage API变更IP，负控502、代理刷新后401，生产未变；本地up自动刷新已实跑 | 更新部署必须刷新minio/web/gateway代理；helper在Agent之后重建 |
| 监控/追踪/告警 | 7生产目标up，Grafana/Tempo实际检查；stage四服务span links、真实规则失败/恢复、合成运输各有证据 | 合成运输只证明Alertmanager到审计，不等于18规则实触发；单机无HA |
| 供应链 | Web生产npm审计0；既有API/Web/网关扫描按其镜像证据保留 | Analyzer57C/329H、Ops依赖61H未处置完，整体安全门禁未过 |
| 性能/规模/架构 | 历史百万task SQL/HTTP/SSE与100k解析/Worker分项基线保留；API双架构交叉编译 | 已测最终60秒Agent主进程/工作负载/容器分项基线；全工具ARM、全部Runner开销/心跳P99/WAN/长期运行/物理HA未验收 |

剩余工作见[CAPABILITY_GAPS](CAPABILITY_GAPS.md)。当前页面是实际交付矩阵，不是全部指南完成声明。

Core3 Java CPU失败、Core4 Java堆附加失败均保留；Core5修复预算与原生jattach后完成全部16类/20场景。TaskKind只改Java堆runner/tool要求，API当前只读目录与Core同源；程序镜像复用不等于旧内置catalog仍有效。完整过程和证据见当前发布。
