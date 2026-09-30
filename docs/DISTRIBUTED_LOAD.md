# 双机负载与一小时资源观测

## 2026-09-30 末窗传输阻塞与独立隧道候选

完整新小时19950请求及质量全成功，资源/清理/原始完整性通过，60RPS P95 68.366ms、持续总体101.374ms，但末窗3570秒P95 810.518ms，整体FAILED（1/120窗）。该窗响应头等待P95 798.998ms，已插桩服务阶段和P95 12.427ms；不能相减不同请求的分位数或据此断言丢包/SSH根因。原始一小时和3578秒中断记录保留。

候选以4条独立SSH TCP连接供每个执行线程固定选路，池限1–8条，旧单路可用`--ssh-tunnels 1`作对照；一个线程等待时，其余线程能使用其他传输。慢请求/失败POST全保留、不重发、不改200ms和120窗门槛，明确记录每次transport_index、池策略及全部隧道清理。图谱优化/两组三段/公网浏览器已通过，Office仍为f37f44e，平台仍为dc50c47；此测量脚本候选只部署到独立fixture源码目录，不重建线上服务。先用可控真实socket阻塞测试验证隔离，再进行一次预先指定的新完整小时，所有成绩并列保留，不挑最好批次或拼接窗口。

## 2026-09-30 失败后修复候选与新实验

旧版同机短测复现2250请求质量通过但首个30秒持续窗P95 201.418ms失败。候选使用每线程HTTP/1.1连接复用、服务端TCP_NODELAY和实例内有界排序分数缓存，查询与完整文本作为缓存键。只缓存确定性分数，每次仍执行FTS及10ms依赖，不能将其称为生产RAG性能。连接/写入/等响应头/读响应的墙钟细分随请求保存；POST异常不重试，失败与调度落后仍记录。`--no-connection-reuse`、`--no-rerank-cache`保留消融入口，计划记录两个布尔配置。200ms门槛不变；历史报告没有配置字段，不回写成新模式。

新短测持续P95 100.757ms、两窗通过，各档5/20/40/60RPS均通过；这不证明历史9个失败窗的唯一根因，也不能替代完整小时。完整3600秒独立运行中，冻结两端源码不变，旧报告保留。见[性能修复](../reports/architecture/performance-fix-20260930.md)。

## 2026-09-30 新版本双机一小时复测（完成，整体未通过）

Windows发压、独立Worker1的一次性回环检索fixture，经SSH隧道实跑3600秒持续阶段；2026-09-30T07:21:53Z开始，08:23:12Z结束。19,950次计划请求全部发出、成功且固定质量检查通过。身份、4份测量源码、原始记录和远端清理复核VERIFIED；性能结论FAILED。

5/20/40RPS档P95分别120.08/116.52/104.83ms通过；60RPS档294.32ms超限，恢复108.50ms通过。一小时5RPS共18,000次，整体P95 160.54ms，但120个30秒窗中9个超过200ms，持续验收未通过。总体summary达标不能覆盖失败分窗。

资源检查通过：3675样本、持续3599、失败0；RSS首末三分之一中位数增加1,458,176 bytes，线程/FD增长0。远端正常退出、remote_cleanup_confirmed=true。这是fixture窗口增长筛查，不是生产容量或无泄漏证明。

耗时分解显示60RPS档的已记录服务阶段之和P95为207.15ms；持续总体为15.25ms，失败分窗剩余耗时包含网络、SSH、客户端及未插桩服务部分。不同请求的分位数不能直接相减归因；尚未证明具体性能根因。实验期间发压机还执行了有界本地代码审查、前端测试和构建，不是独占发压机；所有调度迟到记录保留，最大50.78ms低于预登记250ms门槛。

[原始报告](../reports/business-acceptance/deployment-20260930/distributed-hour/report.json)、[可分享图表](../reports/business-acceptance/deployment-20260930/distributed-hour/report.html)、[完整请求/资源/测量源码](../reports/business-acceptance/deployment-20260930/distributed-hour/evidence.zip)、[逐窗耗时分解](../reports/business-acceptance/deployment-20260930/distributed-hour/timing-analysis.json)。原20260928失败报告继续保留，不改写或拼接本次分数。

## 2026-09-28 双机一小时实测（完成，整体未通过）

Windows 发压、独立 Worker1 的一次性 Linux 回环检索样例经 SSH 隧道接受请求。2026-09-28T13:59:24Z 启动，15:00:43Z 结束；持续阶段完整 3600 秒。计划 19,950 次均发出、成功且固定问题质量检查通过，未发出数为 0。原始请求、远端资源、身份、连续阶段窗口及退出清理均经双机专用复核，结果完整性 VERIFIED，测量结论 **FAILED**。

| 阶段 | 负载与样本 | P95 | 判定 |
|---|---|---|---|
| 阶梯 | 5 / 20 / 40 / 60 RPS，各15秒 | 135.97 / 124.29 / 118.91 / 117.48ms | 四档通过；未测出容量上界 |
| 降载恢复 | 5 RPS，75次 | 267.92ms | 超过200ms门槛 |
| 一小时持续 | 5 RPS，18,000次 | 总体143.88ms | 120个30秒窗中5个超标，持续判定失败 |

5个失败窗起点为持续阶段第600、2070、2610、3450、3510秒，P95分别278.73、370.59、236.78、216.44、214.12ms。不能用总体P95达标覆盖这些窗口。持续发压最大调度迟到2.05ms；超标窗口中服务已标记阶段耗时之和P95约15.2–15.3ms。剩余耗时包含网络、SSH、客户端及未插桩的服务开销，尚不能分别归因；不能直接断言“网络抖动已定位”。

资源检查通过：3675个记录、持续阶段3599个样本、0个失败样本；RSS首末三分之一中位数增加1,470,464 bytes，线程和文件描述符中位数增长均0。观测对象包含同进程的HTTP样例、控制器与采样线程，不是生产业务独占资源，也不能据此证明没有内存泄漏。远端已正常退出，原始文件保留。

测量开始时git head为`f707a42`，当时包含待提交测量源码，精确版本以4个文件的逐字节SHA为准；两端一致，直到测量结束保持冻结。[原始报告](../reports/business-acceptance/deployment-20260928/distributed-hour/report.json)、[请求/资源及精确测量源码包](../reports/business-acceptance/deployment-20260928/distributed-hour/evidence.zip)、[已复核图表](../reports/business-acceptance/deployment-20260928/distributed-hour/report-v2.html)、[耗时分解](../reports/business-acceptance/deployment-20260928/distributed-hour/timing-analysis.json)。初版图表误用本机标签，新版由修正后的生成器重建，原始报告和成绩没有改写。

同轮此前双机短测315/315通过，只验证短链路。[短测原始包](../reports/business-acceptance/deployment-20260928/distributed-quick/evidence.zip)。小时结束后才应用历史CI浮点分桶修复：计划偏移使用index/rate，不再由大单调时钟差值产生边界31/29错分；旧实测不重新计分，此修复也不解释小时测量的真实尾延迟超限。

执行器 `scripts/run_distributed_endurance.py` 在发压机运行，通过 SSH 启动目标机上的一次性 SQLite FTS5 检索样例，仅监听目标机回环地址。发压请求经 SSH 端口转发；公网、SSH 隧道、排队及服务处理均计入端到端延迟。P95 200ms 门槛保持不变，不能拿此结果声称生产服务容量。

## 环境与部署文件

两端需要 Python 3.10+、SQLite FTS5、`psutil>=7.2,<8`。目标机只需原始字节复制以下文件，保留目录结构：

- `scripts/run_distributed_endurance.py`
- `scripts/run_load_endurance.py`
- `scripts/process_resource_monitor.py`
- `demo/rag_service/app.py`

目标机可用隔离 venv；无需启动 Docker，无需数据库或业务数据目录。通过 `--remote-python` 指定绝对解释器路径。SSH 采用已有可信 known_hosts、BatchMode 和已有密钥；不得为省略握手取消主机身份校验。两端文件 SHA 必须逐字节一致，传输时不要转换换行。

目标必须是用户授权的独立测试主机。不要与同一目标上的故障注入、构建或其他压测同时执行。记录其他常驻进程和资源限额，才能解释剩余环境影响。机器指纹来自 Windows MachineGuid 或 Linux machine-id 的 SHA-256；不同 OS 身份不能证明不同物理硬件或独占宿主机。

## 先验证链路，再运行一小时

以下 HOST、路径和密钥由部署负责人填入已经核实的目标。每次两端均使用新证据目录，拒绝覆盖。

```powershell
python scripts/run_distributed_endurance.py --host USER@HOST --identity C:/keys/test.key --remote-root /opt/mini-drop-load/SNAPSHOT --remote-python /opt/mini-drop-load/venv/bin/python --remote-output /var/tmp/mini-drop-load-quick-UNIQUE --output output/quality/distributed-quick-UNIQUE --rates 5 10 --step-seconds 6 --soak-seconds 30

python scripts/run_distributed_endurance.py --host USER@HOST --identity C:/keys/test.key --remote-root /opt/mini-drop-load/SNAPSHOT --remote-python /opt/mini-drop-load/venv/bin/python --remote-output /var/tmp/mini-drop-load-hour-UNIQUE --output output/quality/distributed-hour-UNIQUE --rates 5 20 40 60 --step-seconds 15 --soak-seconds 3600 --concurrency 128
```

短版约 57 秒，验证双机身份、远端完整阶段、请求正确性、资源采样与清理，不是小时级验收。一小时命令持续阶段 18,000 个请求，额外阶梯 1,875 次与恢复 75 次，共 19,950 个计划请求；120 个 30 秒持续窗口必须分别达标。某个高档发生服务 SLO 超限可界定测试区间；发压饱和或调度落后则使整轮 INVALID，不能解释为服务容量。

## 证据与清理

原始请求、资源记录、双方系统身份、PID/create_time、版本与源码指纹、阶段资源观测窗口、SSH 错误日志及完成报告都保存。控制通道只有阶段变更和结束两类命令，不开放远程故障接口。目标机资源仅观测本次 fixture PID，首次 CPU 无基准时保留 null。远端阶段先确认、发压端再执行；各阶段资源窗口必须连续且覆盖完整发压时长。

执行完会关闭远端 HTTP 服务与线程并等候 SSH 进程返回 0，才记录 `remote_cleanup_confirmed=true`。发压端中断关闭控制 stdin 请求清理；目标机额外有 4,500 秒硬退出上限。退出不删除任何证据目录。硬 TTL 或失联导致中断时，远端已刷盘 JSONL 仍在，但不伪造完成报告。

先执行双机专用复核，再生成图表：

```powershell
python -c "from scripts.run_distributed_endurance import verify_distributed; print(verify_distributed('output/quality/distributed-hour-UNIQUE/report.json'))"
python scripts/render_load_report.py output/quality/distributed-hour-UNIQUE/report.json --output output/quality/distributed-hour-UNIQUE/report.html
```

复核会重算每个原始请求与分窗、校验远端机器/进程/源码及清理结果。有效记录可以是 INVALID 或 FAILED；需要验收通过时使用 `verify_distributed(path, require_pass=True)`。数据摘要用 SHA 发现损坏，不是对人为整套伪造证据的密码学防护。

截至新增代码的本地检查，真实 fixture 协议、正常退出和 stdin EOF 清理已通过回归；双机实测与一小时结果以单独归档记录为准，本说明不宣称已经实跑。
