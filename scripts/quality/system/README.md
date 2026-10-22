# 真实平台、故障恢复与持续运行

`lab.py` 创建全新的 `txdrop-dev-qa-<随机值>` Compose 项目，复用 `scripts/dev.py` 的隔离配置/证书生成与最终渲染校验。它不接受已有项目、公网URL、业务PID或生产凭据，不调用模型。全部数据库、S3、Agent工作卷都是本轮独立资源；清理逐个重新检查项目标签，禁止以未知资源或生产卷替代。

## 输入与当前构建绑定

先在当前 checkout 构建 API/Core/Web。可使用已有兼容运行镜像，但必须显式挂载**本次构建**的二进制和静态资源；旧镜像只提供运行库。当前 Analyzer Python 源码、全部迁移、任务目录和 Nginx 配置由工具直接复制到私有实例并只读挂载。报告记录实际副本与镜像 ID 摘要；这些不替代编译来源证明，发布时同时保留本次构建回执。

`images.json` 示例（必须已有的兼容镜像，不拉取生产配置）：

```json
{"core":"drop-agent:20261020.3","api":"drop-api:quality","analyzer":"drop-analyzer:20261021.2","web":"drop-web:quality","nginx":"drop-web:quality"}
```

`bindings.json` 示例：

```json
{"api_binary":"/absolute/current-build/drop-api","core_binaries":"/absolute/current-build/core","web_dist":"/absolute/current-checkout/web/dist"}
```

Core目录必须包含 `drop_server`、`drop_agent`、`drop_workload`；必须是 Linux Release 二进制。API必须为当前 Linux 构建。不要把 ASan 工作负载的CPU数据当作生产性能比较。Web必须含 `index.html` 与完整构建资源。浏览器脚本由当前仓库的 Node/Playwright 执行，需要 `web/node_modules` 和已安装 Chromium；也可明确指定已有兼容 Chromium 路径。

宿主没有Node/Chromium时，可以拉取与本机 `playwright`/`playwright-core` **完全同版本**的官方镜像，校验实际ID后用 `--browser-image sha256:<实际64位ID>`。例如 `mcr.microsoft.com/playwright:v1.64.1-noble`；传入运行器的必须是已核对的ID而非可变tag。此模式只需要两个纯JS包目录，无须复制全部node_modules。镜像只读、无capabilities、768MiB/.75CPU/pids128；因服务只发布回环端口而使用host网络，但浏览器强制仅同origin网络请求，不能填充或伪造业务响应。只挂载当前e2e代码/两包、一个上下文文件、**两份本轮admin登录文件**及独立输出目录，不挂DB/S3/CA私钥、Docker socket或生产目录。此模式通常不传宿主的 `--browser-executable`。

```bash
python3 scripts/quality/system/lab.py \
  --images /private/images.json --bindings /private/bindings.json \
  --bootstrap-image drop-core-build:20261020.3 \
  --output /absolute/new-system-run \
  --browser-executable /usr/bin/chromium \
  --soak-minutes 30 --interval-seconds 30
```

普通PR使用 `--soak-minutes 0`，仍完整执行浏览器和故障恢复。持续运行只接受20–120分钟，按固定间隔串行创建真实2秒PROC_CPU任务；没有重试失败窗口来隐藏缺陷。默认常驻服务内存上限合计1776MiB，初始化服务串行运行，容器浏览器另限768MiB；执行前由宿主协调者确认剩余资源，不能并发挤占生产。

control/storage保持内部网络，仅Web另接入口网络并发布精确回环端口。工具检查渲染后只有Web连接入口网，并再次检查Docker实际端口，不能只信HostConfig中的声明。内部网络上的端口声明可能没有实际转发；此情形必须失败，不放宽监听核验。

## 实际链路与判定

浏览器仅拦截网络请求以执行同origin白名单，不填充或伪造API响应，真实完成：登录→界面选择PROC_CPU/已公布受控进程→Go创建→mTLS Core/Agent→procfs实采→MinIO→Analyzer子进程→页面加载真实gzip火焰图。第二个任务在RUNNING时由界面取消，然后同task重试；旧CANCELED attempt必须保留，新attempt编号为2，取消/重试事件各一次。

独立客户端再次校验每个当前attempt的RAW与至少3份RESULT的实际下载字节、大小和SHA，要求COLLECTION_SUCCEEDED与ANALYSIS_SUCCEEDED各一次、正确CPU tick单位；不把导入fixture作为实采。

故障只注入当前随机项目拥有的容器：

1. Agent在真实20秒采集中SIGKILL；先确认RUNNER_ACCEPTED已持久化。重启后journal须返回AGENT_RESTARTED失败，旧任务无重复采样、新建任务恢复成功。
2. Analyzer在采集前停止，真实任务必须进入COLLECTED/PENDING；启动后同作业成功。这证明pending队列持久性，**不冒称正在持有租约的worker崩溃测试**。
3. PostgreSQL停止3秒后启动；原会话、历史结果摘要和新建实采必须恢复。不证明主从HA、主机断电或长期故障恢复。

持续窗口检查任务/attempt/制品/事件一致性，定期保存实际Docker stats展示值与运行/OOM状态。展示值不是精确字节RSS；这是固定资源、串行有限负载的持续可用性记录，不是最大吞吐、数天长稳或无内存泄漏认证。

`summary.json`、`browser.json`、`progress.json`为固定字段公开收据。`private/`与`.local/dev/qa-*`包含私有日志/新生成证书和凭据，禁止上传GitHub或CI artifact。失败轮次保留固定错误码与已有窗口，仍按标签清理本轮容器/卷/网络；不执行生产`compose down -v`。真实执行完成前，不把脚本或mock边界测试称为端到端成功。

故障定位可用 `make_diagnostic_context.py` 生成唯一注册日志的临时插桩上下文，记录原文件、补丁、插桩文件和两个构建基础镜像的 SHA；它不改仓库C++。诊断镜像在固定日志 `stage` 增加 gRPC 0–16 状态与 `control_internal`、`dns`、`failed_connect`、`other` 四类枚举。错误正文仅在进程内与固定字面量/前缀比较，禁止记录正文和 details。运行时必须给 `--diagnostic-manifest`，回执明确 `acceptance_eligible=false`；这类运行不能作为最终未插桩门禁通过证明，修复后须恢复普通runtime重跑。API还要显式检查真实 `/health/ready` 的依赖枚举，Compose的liveness通过不代表控制面就绪。

只有显式插桩轮的原始浏览器门禁已失败、当前Agent未注册且有gRPC14、Core无INTERNAL事件、显式ready200、独立PG确认tasks/attempt均为零，才运行受限解析器对照：保留原default失败，停止本轮Agent，仅加native重建观察最多20秒，再去掉native重建default观察最多45秒。各腿检查新CID固定注册日志、镜像/环境/挂载/用户/网络一致性；不用数据库残留Agent行判断成功，不创建任务，不访问生产或模型。只有native成功而恢复default仍失败，才记为解析策略差异；这本身不证明具体软件包缺陷。公开`resolver-diagnostic.json`只保存白名单元数据，原browser失败和`acceptance_eligible=false`保持，异常仍执行owner清理。
