# HTTP 负载测试（Locust）

对已部署的 Drop 从公网发有界只读负载，回答一个具体问题：**在“若干人同时排查”这个量级下，
公网入口、静态资源和登录后的读接口还能不能稳定给出可接受的延迟。**

它不是容量测试，不找饱和点，也不替代 `scripts/quality/performance`（采集对被测进程的扰动）
和 `scripts/benchmarks`（Core 心跳 gRPC 基线）。三者对象不同，结论不能互相替代。

## 为什么先排除写接口

| 限制 | 代码位置 | 对压测的影响 |
| --- | --- | --- |
| 登录 10 次/15 分钟/IP | `api/internal/httpapi/server.go:54` | 20 个虚拟用户各自登录会把自己锁死，必须先换少量会话再复用 |
| 写请求 60 次/分钟/用户 | `api/internal/httpapi/server.go:55` | 撞限流得到的 429 比例是限流器的性质，不是平台容量 |
| 创建任务串行槽位容量 1 | `api/internal/httpapi/server.go:56` | 并发创建只会排队并返回 429 |

因此本套件**没有** `--writes` 开关。要测创建任务链路，应走串行队列（见运维手册），
不要拿压测去撞产品自己设计好的限流。

## 三层对象

| 层 | 端点 | 测什么 | 需要账号 |
| --- | --- | --- | --- |
| 边缘与静态 | `/`、`/<spa-route>`、`/assets/*` | 网关 TLS（`cpus: 0.15`）、web 容器 gzip 与缓存头、首屏字节数 | 否 |
| 认证拒绝路径 | `/api/v1/tasks` 无 Cookie | 网关→web→Go 认证中间件的固定开销 | 否 |
| 登录后读路径 | `/api/v1/overview`、`/tasks`、`/tasks/:id`、`/events`、`/result`、`/agents`、`/schedules`、`/quality/runs` | Go 查询、PostgreSQL（`cpus: 0.5`）、序列化与代理 | 是（≤8 会话） |

## 运行

```bash
# 公开层（无需账号）
python scripts/quality/loadtest/run.py --host https://82.157.81.79 \
  --profile public-step --insecure --out .local/loadtest/public-step

# 认证层：先写两行凭据（第一行用户名、第二行口令），文件不入库
#   .local/loadtest/account.txt
python scripts/quality/loadtest/run.py --host https://82.157.81.79 \
  --profile api-step --sessions 4 --insecure --out .local/loadtest/api-step
```

`--insecure` 只对本次进程关闭证书校验：生产是 IP 自签证书，不加会在每个请求上失败。
输出目录里 `locust*_stats.csv` 是未加工的原始统计，`receipt.json` 是合成后的收据，
`meta.json` 记录本次负载模型与**明确没测的东西**。会话池用完即删。

## 硬上限

| 项 | 上限 | 理由 |
| --- | --- | --- |
| 虚拟用户 | 100 | 生产是 4C8G、29 容器的小机队，再高会把演示环境压坏 |
| 单次总时长 | 600 秒 | 有界实验；不是稳定性或长时间浸泡测试 |
| 预登录会话 | 8 | 登录限流 10 次/15 分钟，留 2 次余量 |
| 单阶段最短 | 5 秒 | 更短的窗口不足以形成稳态 |

越界一律拒绝启动，不做“自动截断到上限”的猜测。

## 读结果时别犯的错

- **客户端延迟包含公网 RTT。** 本机到该主机的 TCP+TLS 建连约 90–130ms，空载单请求
  p50 就在 44ms 左右。要看的是**相对变化和 P95/P99 的拐点**，不能拿 44ms 说“接口很慢”。
- **Locust 走 HTTP/1.1，浏览器走 HTTP/2。** 真实浏览器会多路复用，同一批小请求的
  表现好于本套件；这里的数字对浏览器是偏保守的。
- **没有服务端采样就别说瓶颈。** 本机无该主机的 SSH 凭据，CPU/内存/PostgreSQL 指标为
  `null` 并列在 `not_measured`。只有拿到服务器本机数据，才能把延迟归因到容器 CPU 配额、
  数据库等待或网络。
- **0 失败不等于有余量。** 20~40 个虚拟用户在交互式思考时间下只有个位数 RPS，
  远未触到饱和；通过门槛只说明这个量级没退化。

## 回归

```bash
cd scripts/quality/loadtest && python -m unittest test_loadtest
```

11 项离线测试覆盖百分位合成、Aggregated 行去重、越界拒绝、阶梯边界和凭据文件解析；
它们不连服务器，不能证明平台性能。
