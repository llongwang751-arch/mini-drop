# Mini-Drop 15 分钟演示录制逐步脚本

这份文档是录屏时直接照着走的操作稿。目标是覆盖题目要求的 4 个演示点：

1. 端到端跑一次：从下发任务到看到火焰图，全程不剪辑。
2. eBPF 现场演示：用 `dd` 现场制造 IO，看 eBPF 采集器的可视化变化。
3. Continuous Profiling 现场演示。
4. 一个最得意的设计，并说明“如果让我重做我会怎么改”。

建议录制时长控制在 12-14 分钟。录屏时保留浏览器和一个 PowerShell 终端，不要只讲代码。

## 0. 录制前准备

### 0.1 启动项目

打开 PowerShell：

```powershell
cd D:\tx\mini-drop
docker compose up --build
```

等待终端里出现类似日志：

```text
GET /api/health HTTP/1.1 200 OK
POST /api/agents/local-agent/heartbeat HTTP/1.1 200 OK
POST /api/agents/local-agent/claim HTTP/1.1 200 OK
```

这说明后端和 Agent 已经启动。

浏览器打开：

```text
http://localhost:8080
```

如果页面右上角显示“控制平面在线”，首页指标里“在线 Agent”为 1，就可以开始录制。

### 0.2 准备一个更适合火焰图展示的目标进程

不要只采集 `PID 1`。`PID 1` 通常很空闲，perf 可能采不到调用栈，火焰图会显示 `no-samples`，看起来单调。录制前建议在 Agent 容器里启动一个 CPU 目标进程。

另开一个 PowerShell：

```powershell
cd D:\tx\mini-drop
docker compose exec agent sh -lc "nohup python -c \"import hashlib, time; end=time.time()+900; data=b'mini-drop-demo'; i=0
while time.time()<end:
    data=hashlib.sha256(data+str(i).encode()).digest()
    i+=1\" >/tmp/minidrop-cpu.log 2>&1 & echo $! > /tmp/minidrop-cpu.pid && cat /tmp/minidrop-cpu.pid"
```

把输出的 PID 记下来，下面用 `{CPU_PID}` 表示。

说明：如果这个命令在 PowerShell 里因为引号不好复制，也可以演示时先用 `PID 1`，但火焰图可能比较单调。单调不是页面坏了，而是目标进程没有足够采样栈。

## 1. 开场与首页说明，0:00-1:00

### 操作

1. 浏览器停留在 `任务总览` 页面。
2. 鼠标指一下左上角 `Mini-Drop 性能诊断控制台`。
3. 鼠标指一下顶部导航：`任务总览`、`采集节点`、`审计日志`。
4. 鼠标指一下右上角绿色点：`控制平面在线`。

### 讲解文案

> 这是我实现的 Mini-Drop 性能诊断系统。它复刻的是 Drop 的核心链路：用户在 Web 创建采集任务，FastAPI Server 负责调度和状态落库，Python Agent 领取任务并调用 perf、eBPF/bpftrace、py-spy 采集数据，Analyzer 生成火焰图、TopN 热点函数和分布结果，最后回到 Web 展示。

> 这个项目不是静态页面，右上角可以看到控制平面在线，首页也显示当前有 1 个在线 Agent。

## 2. 展示 Agent 心跳和审计，1:00-2:00

### 操作

1. 点击顶部导航 `采集节点`。
2. 展示 `local-agent`，确认状态是在线。
3. 点击顶部导航 `审计日志`。
4. 展示 Agent 上线或恢复事件。
5. 回到 `任务总览`。

### 讲解文案

> Agent 每 5 秒向 Server 发送一次心跳。Server 如果 30 秒没有收到心跳，会把 Agent 标记为离线。上线、离线、恢复都会进入审计日志。

> 这一块主要证明系统有真实 Agent 生命周期，而不是前端写死的数据。

## 3. 端到端跑一次 perf 任务，2:00-6:00

题目要求“从下发任务到看到火焰图，全程不剪辑”。这一段不要暂停录制。

### 操作

1. 在 `任务总览` 页面点击右上角 `新建任务`。
2. 弹窗中填写：
   - `目标 Agent`：选择 `local-agent`
   - `目标进程 PID`：填写 `{CPU_PID}`。如果没有准备 CPU 目标进程，就填 `1`
   - `采样时长（秒）`：填写 `8`
   - `采样频率（Hz）`：填写 `49`
   - `采集器`：选择 `CPU / perf`
   - `持续性能采集`：不要勾选
3. 点击 `下发采集任务`。
4. 回到任务列表，等任务状态变化。
5. 看到状态变成 `已完成` 后，点击这条任务。
6. 在详情页展示：
   - 顶部任务 ID、采集器、PID、状态
   - `采样火焰图`
   - `热点函数 TopN`
   - `可验证归因`
   - `状态历史`

### 讲解文案

> 现在我创建一个 perf 采集任务。这里指定目标 Agent、目标 PID、采样时长和采样频率。点击下发后，任务会先进入 PENDING，也就是等待执行。

> Agent 轮询领取任务后，状态会变成 RUNNING；采集结束后进入 UPLOADING；Analyzer 生成结果后进入 DONE。每一次状态迁移都会写入数据库，并且带 reason 字段。

进入详情页后讲：

> 这里可以看到采样火焰图。火焰图表示调用栈层级和采样权重，块越宽表示这个栈帧出现得越多。右侧 TopN 是把热点函数按样本数排序，下面的可验证归因不是让模型乱猜，而是基于 TopN 和规则证据生成建议。

> 状态历史里能看到从“新建到等待执行”、“等待执行到正在采集”、“正在采集到正在分析”、“正在分析到已完成”的完整链路。这里的时间显示到毫秒，是为了看清同一秒内的快速状态迁移。

### 如果火焰图显示 `no-samples` 怎么讲

如果火焰图还是比较单调，不要慌，可以这样说：

> 当前环境是 Docker Desktop / WSL2，perf 受宿主机内核权限影响，可能采不到完整 native 调用栈。系统不会伪造真实 perf 栈，而是明确标记 degraded，并用 `/proc` 采样结果作为降级可视化。真正 Linux 机器上满足 perf 权限时，会展示更完整的 native 调用栈。

## 4. eBPF 现场演示，6:00-9:00

这一段对应题目要求：现场制造 IO 或调度抖动，看 eBPF 采集器可视化变化。

### 操作 A：先创建 eBPF 采集任务

1. 回到 `任务总览`。
2. 点击右上角 `新建任务`。
3. 弹窗中填写：
   - `目标 Agent`：选择 `local-agent`
   - `目标进程 PID`：填写 `1`
   - `采样时长（秒）`：填写 `8`
   - `采样频率（Hz）`：填写 `10`
   - `采集器`：选择 `eBPF 内核探针`
   - `持续性能采集`：不要勾选
4. 点击 `下发采集任务`。

### 操作 B：马上制造 IO

切到 PowerShell，执行：

```powershell
cd D:\tx\mini-drop
docker compose exec agent sh -lc "dd if=/dev/zero of=/tmp/minidrop-io.bin bs=1M count=512 conv=fsync"
```

切回浏览器，等待 eBPF 任务完成，然后点击这条任务详情。

### 讲解文案

> 现在我演示 eBPF 采集器。这个采集器使用 bpftrace 挂载内核态 `kprobe:vfs_write`，观察写入事件。为了让它有明显变化，我现场用 `dd` 写入一个临时文件。

进入详情页后讲：

> 这里的“内核写入分布”就是 eBPF 采集器的专项可视化。它展示了采样窗口内哪些进程触发了写入。这个能力和 perf 不一样，perf 主要看 CPU 调用栈，eBPF 可以从内核探针角度观察系统行为。

### 如果 eBPF 显示 degraded 怎么讲

如果页面提示 bpftrace 不可用或 degraded：

> eBPF 对宿主机内核和容器权限要求比较高，需要 Linux kernel tracing 能力以及 privileged 容器。当前 docker-compose 已经给 Agent 配置了 privileged 和宿主机 PID 命名空间，但如果 WSL2 或 Docker Desktop 内核没有开放对应能力，系统会透明标记 degraded。这个地方没有假装采到了真实 eBPF，而是明确把环境限制暴露出来。

录正式视频前，最好在自己的机器上先跑通一次真实 eBPF。如果跑不通，视频里要诚实说明环境限制，并把 README 中的 Linux kernel / privileged 要求指出来。

## 5. Continuous Profiling 现场演示，9:00-11:00

### 操作

1. 回到 `任务总览`。
2. 点击 `新建任务`。
3. 弹窗中填写：
   - `目标 Agent`：选择 `local-agent`
   - `目标进程 PID`：填写 `{CPU_PID}`，没有就填 `1`
   - `采样时长（秒）`：填写 `5`
   - `采样频率（Hz）`：填写 `10`
   - `采集器`：选择 `CPU / perf` 或 `eBPF 内核探针`
   - 勾选 `持续性能采集`
4. 点击 `下发采集任务`。
5. 等第一个任务完成后，留在 `任务总览`，向下看 `持续采样时间轴`。
6. 等 10-20 秒，刷新或等待页面自动刷新，观察是否出现多个切片。
7. 在 `持续采样时间轴` 右侧选择：
   - Agent：`local-agent`
   - 开始时间：选择当前时间往前几分钟
   - 结束时间：选择当前时间
8. 点击 `查询窗口`。
9. 点击时间轴中的某个切片，查看该切片详情。

### 讲解文案

> Continuous Profiling 不是只做一次采集，而是常驻低频采样。每个采样窗口完成后，系统会自动创建下一个时间切片。这样用户可以按时间轴回看任意窗口的性能数据。

> 这里我创建了一个持续采样任务。完成一个切片后，后端会自动续建后继切片。页面下方的持续采样时间轴可以按 Agent 和时间范围查询切片，并点击某个切片回看火焰图。

如果还没出现切片：

> 这里需要等第一个持续任务完成后才会出现切片。为了录制视频，我一般会提前创建一个持续采样任务，让时间轴里有数据，再现场创建一次展示流程。

## 6. 自然语言采集加分项，11:00-12:00

### 操作

1. 回到 `任务总览`。
2. 找到页面中间的输入框 `一句话创建可验证任务`。
3. 输入：

```text
对 PID 1 做 5 秒 py-spy 采集，频率 10Hz
```

4. 点击 `解析并下发`。
5. 等任务出现在最近任务列表。

### 讲解文案

> 这是自然语言采集加分项。用户可以用一句话描述采集意图，系统解析出 PID、采集器、时长、频率和 Agent，再创建任务。

> 我这里没有让系统随便猜目标。如果用户没有写 PID，系统会提示必须包含目标 PID。这样做是为了避免性能诊断里选错进程。

## 7. 工程化与交付物，12:00-13:00

### 操作

1. 打开 GitHub 仓库或本地 README。
2. 展示 README 顶部技术栈。
3. 展示“评审环境要求”。
4. 展示常用命令：
   - `docker compose up --build`
   - `make demo`
5. 如果时间够，打开 `DESIGN.md`，展示架构图和状态机图。

### 讲解文案

> 前端使用 React + TypeScript + React Router + Zustand，后端使用 FastAPI + SQLAlchemy + MySQL，Agent 和 Analyzer 使用 Python。Docker Compose 会启动 Server、Agent、MySQL、MinIO 和前端静态资源。

> README 写明了评审环境要求，包括 Ubuntu 22.04、Linux kernel 5.4+、Docker Compose v2，以及 eBPF 所需的 privileged 权限。

> 设计文档里有架构图、状态机迁移图、关键取舍、AI 协作和后续 7 天计划。

## 8. 最得意的设计与“如果让我重做我会怎么改”，13:00-14:30

### 操作

1. 回到一个已完成任务详情页。
2. 鼠标指向 `状态历史`。
3. 鼠标指向右侧 `可验证归因`。

### 讲解文案

> 我最得意的设计是“Server 是唯一任务真相源”。Agent 不直接改页面，也不直接改数据库。所有任务状态都必须通过 Server 的状态机迁移，合法状态是 PENDING、RUNNING、UPLOADING、DONE 或 FAILED。每次迁移都会落库，并且必须有 reason。

> 这样即使 Agent 失败、重试或者上传异常，Web 也不会展示虚假的成功状态。评审也可以通过状态历史看到每一步发生了什么。

> 如果让我重做，我会把任务事件进一步建模为可重放事件流，并引入持久化队列。Analyzer 不再同步跑在 Server 里，而是通过队列异步消费采集结果。然后补 Agent 身份认证、SSO、多租户权限、符号服务、长期保留策略，以及最小权限 eBPF 探针目录。

## 9. 结尾，14:30-15:00

### 讲解文案

> 总结一下，这个版本已经跑通 Mini-Drop 的核心链路：Web 下发任务、Server 调度、Agent 采集、Analyzer 分析、MySQL 状态落库、Web 展示火焰图和分析结果。同时实现了 eBPF、py-spy、Continuous Profiling 和自然语言采集。生产级能力上，我后续会继续补 gRPC、SSO、多租户权限、队列化 Analyzer 和完整 LLM 智能归因。

## 录制检查清单

录完视频前确认至少出现过这些画面：

- 首页右上角 `控制平面在线`
- 首页指标中 `在线 Agent = 1`
- `采集节点` 页面显示 `local-agent`
- `审计日志` 页面有 Agent 事件
- 点击 `新建任务`
- perf 任务从创建到完成
- 已完成任务详情页展示火焰图、TopN、归因、状态历史
- eBPF 任务详情页展示内核写入分布，或明确展示 degraded 原因
- Continuous Profiling 的持续采样入口和时间轴
- 自然语言采集输入框和解析下发
- README 的评审环境要求
- DESIGN.md 的架构图或状态机图

## 常见问题与回答

### 图里的状态时间为什么之前看起来都一样？

任务太短时，PENDING、RUNNING、UPLOADING、DONE 可能在同一秒内完成。现在状态历史显示到毫秒，可以看出先后顺序。演示时建议采样时长填 `8` 秒，不要填太短。

### 火焰图为什么有时很单调？

如果目标进程太空闲，或者 WSL2 / Docker 环境限制导致 perf 没拿到 native 调用栈，就可能只看到很少的栈。演示时不要只采 `PID 1`，建议先启动一个 CPU 目标进程，再采它的 PID。

### eBPF 如果跑不起来怎么办？

先确认 Docker Desktop / WSL2 已启动，Agent 容器是 `privileged: true`。如果 bpftrace 仍然不可用，说明宿主机内核能力不足。视频里要诚实说明 degraded 原因，并强调 README 已写明真实 eBPF 需要 Linux kernel tracing 权限。

### 可以说 AI 帮忙做吗？

可以，但不要说“我不懂，AI 做的”。推荐说：

> 我使用 AI 辅助实现和排查，但验收是按题目逐项验证的，包括 Docker 启动、状态机落库、真实采集、测试覆盖率、端到端流程和工程化目录。
