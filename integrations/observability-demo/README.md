# 双服务实时遥测演示

这是仓库自有的**受控合成业务**。网关实际通过 HTTP 请求目录服务，目录服务实际执行 SQLite 或 Python 计算；遥测来自本次请求。它不是静态 profile 导入、生产监控产品或准确率评测。

## 运行流程

`gateway → HTTP client span → catalog → 只读 SQLite / Python CPU`。同一请求使用随机 Trace ID 贯穿两服务。目录服务在 1200 条明确合成数据上执行固定 SQL，不接收用户 SQL、文件路径或任意命令。

| 场景             | 实际工作                         | 预期观察                                                    |
| ---------------- | -------------------------------- | ----------------------------------------------------------- |
| `normal`         | 主键索引读取                     | 正常延迟、成功状态                                          |
| `slow_query`     | 同表笛卡尔积聚合 SQL 执行四次    | 目录服务 `DB_SLOW_QUERY`，网关延迟随下游升高                |
| `cpu`            | 有界纯 Python 位运算循环         | 目录服务可被 Python 采样观察到 CPU 热点                     |
| `upstream_error` | 目录服务实际返回 HTTP 503        | 网关实际收到错误，返回 502；两端记录实际错误率              |
| `timeout`        | 实际重查询遇到网关 50ms deadline | 网关 504/`UPSTREAM_TIMEOUT`；下游可能稍后完成，不伪称已取消 |

`GET /health` 返回本进程实际 PID、UID、角色及 `synthetic_controlled_live_http_demo` 来源。容器共享既有 Agent 的 PID namespace，同 UID10001；管理员可据此选择真实进程，不能把两服务的 PID 相互替代。此 overlay 不增加采集特权，不修改或重启 Agent/Helper。

## 数据契约和边界

`GET /v1/signals?service=gateway|catalog&signal=metrics|logs|traces|changes&start=<RFC3339>&end=<RFC3339>&limit=30` 对齐 Drop `drop-adapter`：

- 指标只计算窗口内实际完成的请求：nearest-rank P95 毫秒、5xx 比例、完成请求数除以查询窗口秒数。`observed_at` 是最后一个实际完成请求的时间。没有请求就返回空指标，不能由空窗口推断服务正常。
- 日志仅返回固定事件类别、真实耗时、状态码和 Trace ID；没有自由日志正文或业务字段。`timestamp` 是实际完成时间。
- Trace 使用实际 `started_at`。目录服务计一个 server span；网关计本地 server/client 两个 span，正常响应还包含下游实际返回的一个 span。错误或超时时没有获得下游 span 摘要，保留本地已观察数量，不猜下游是否完成。跨服务查询会重复看到相关请求，不能相加当成独立请求数。
- `changes` 的 deployment 是实际进程启动事件。没有人为填入历史“发布故障”；启动时间接近慢请求也不能证明发布是根因。
- 查询窗口最多15分钟，日志最多30条、Trace/变更最多20条；最多保存4096个请求，淘汰或响应裁剪明确标记 `truncated`。遥测在内存中，重启清空；不是生产持久化日志系统。
- 若负载不在采集窗口内，Agent 应拿不到该次信号；先开始持续负载，再在界面发起调查。慢 SQL/CPU 的收益和时长只适用于本演示机器，不代表 CRM、Saber 或生产请求。

## 本机验证

仅标准库，无安装依赖：

```sh
python -W error::ResourceWarning -m unittest discover -s integrations/observability-demo -p test_server.py -v
python integrations/observability-demo/verify_local.py --output .local/observability-demo-new-run
```

验证器启动两个独立本机进程，真实执行 normal→慢 SQL→CPU→上游错误→normal，并检查两端 Trace ID、各阶段时间窗及空窗口。输出目录必须全新。它是本机进程/HTTP 验收，不能当成容器隔离、生产采集或模型诊断验收。

## Linux 容器部署

基镜像默认使用本机缓存 `drop-agent:20261020.1`，不拉取依赖，不覆盖旧业务镜像。将本 overlay 追加到**当前已核验的部署文件列表**，先检查合并后的配置；下面的 `...` 表示管理员已有文件参数，不能原样复制。

```sh
docker compose ... -f deploy/compose.agent-expansion-demo.yml build --pull=false observability-catalog
docker compose ... -f deploy/compose.agent-expansion-demo.yml up -d --no-deps observability-catalog observability-gateway
```

两服务无 `ports`、无凭据和现有数据卷，只有内部 control 网络；只读根文件系统、临时合成 SQLite、cap_drop ALL、192MiB/0.5CPU/32 PID 预算。启动前既有 Agent 必须已运行；不要执行 `down -v`。`--no-deps` 保证不重启既有服务；启动后分别等待两服务 health。

读取健康信息及实际 PID：

```sh
docker compose ... exec -T observability-catalog /opt/drop-tools/venv/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8131/health').read().decode())"
```

在演示期间发出真实负载。命令只访问该合成业务内部 HTTP；最多120秒/1000请求，可与界面采集同时运行：

```sh
docker compose ... exec -T observability-gateway /opt/drop-tools/venv/bin/python /opt/drop-observability-demo/load.py --scenario slow_query --duration-seconds 60 --count 1000
```

切换 `normal` 对照或 `cpu`/`upstream_error`。负载进程只是客户端，采集目标应选 `server.py --role catalog` 或 `gateway` 的健康信息 PID。

## 接到 Drop 诊断

管理员审阅 [登记示例](observability.example.json)，把两项 `agent_id` 替换为当前 Agent ID，并将配置登记到 API 的受控 observability 文件挂载。默认无 `group_id`，仅管理员可使用；团队演示必须明确登记组且成员同时具备数据源与 Agent 权限。HTTP 仅用于容器内部网络，telemetry 不映射公网端口。

登记拓扑只包含 `gateway → catalog`。采集目录进程时选择 `catalog` 业务服务；其一跳上游仍可关联到 `gateway`。示例问题：“目录服务延迟升高，请核对同时段 SQL 日志、业务时长和调用链，列出反证与仍缺的验证。”

真实 Agent/DeepSeek 运行与网页结果由主部署验收另行记录。Trace 关联和时间数值可以核对，根因需要受控干预及复测；此演示不会自动修生产代码或把一跳拓扑当成因果结论。
