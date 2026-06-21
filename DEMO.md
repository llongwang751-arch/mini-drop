# Mini-Drop 10 分钟演示录屏脚本

这份脚本按 10 分钟左右设计，满足题目“≤15 分钟演示视频”的要求。录制重点不是把所有代码讲完，而是让导师看到：

1. 端到端跑一次：从下发任务到看到火焰图，全程不剪辑。
2. eBPF 现场演示：用 `dd` 现场制造 IO，看 eBPF 采集器的可视化变化。
3. Continuous Profiling 现场演示。
4. 一个最得意的设计，并说明“如果让我重做我会怎么改”。

建议总时长：9 分 30 秒到 10 分 30 秒。

## 0. 录制前准备，不录或只快速带过

### 0.1 启动项目

打开 PowerShell：

```powershell
cd D:\tx\mini-drop
docker compose up --build
```

等终端出现：

```text
GET /api/health HTTP/1.1 200 OK
POST /api/agents/local-agent/heartbeat HTTP/1.1 200 OK
POST /api/agents/local-agent/claim HTTP/1.1 200 OK
```

打开浏览器：

```text
http://localhost:8080
```

看到右上角 `控制平面在线`，首页 `在线 Agent = 1` 后再开始正式录屏。

### 0.2 准备一个更好看的采集目标

不要只采 `PID 1`。`PID 1` 很空闲，火焰图可能只有很少样本。录制前可以在 Agent 容器里启动一个 CPU 目标进程：

```powershell
docker compose exec agent sh -lc "nohup python -c \"import hashlib, time; end=time.time()+900; data=b'mini-drop-demo'; i=0
while time.time()<end:
    data=hashlib.sha256(data+str(i).encode()).digest()
    i+=1\" >/tmp/minidrop-cpu.log 2>&1 & echo $! > /tmp/minidrop-cpu.pid && cat /tmp/minidrop-cpu.pid"
```

记住输出的 PID，下面用 `{CPU_PID}` 表示。

如果这个命令不好复制，也可以用 `PID 1`，但火焰图可能比较单调。录制时可以解释：Docker Desktop / WSL2 环境下 perf 可能受内核权限影响。

## 1. 开场，0:00-0:40

### 你要点什么

1. 浏览器停在 `任务总览` 页面。
2. 鼠标指左上角 `Mini-Drop 性能诊断控制台`。
3. 鼠标指顶部导航：`任务总览`、`采集节点`、`审计日志`。
4. 鼠标指右上角 `控制平面在线`。

### 你要说什么

> 这个项目叫 Mini-Drop，是一个性能诊断平台。它复刻 Drop 的核心链路：用户在 Web 创建采集任务，FastAPI Server 负责调度和状态落库，Python Agent 领取任务并调用 perf、eBPF/bpftrace、py-spy 采集数据，Analyzer 生成火焰图、TopN 热点函数和分布结果，最后回到 Web 展示。

> 当前页面显示控制平面在线，并且有在线 Agent，说明不是静态页面。

## 2. Agent 和审计日志，0:40-1:20

### 你要点什么

1. 点击顶部 `采集节点`。
2. 展示 `local-agent` 在线。
3. 点击顶部 `审计日志`。
4. 展示 Agent 上线或恢复记录。
5. 点击顶部 `任务总览` 回首页。

### 你要说什么

> Agent 每 5 秒向 Server 发送心跳。Server 如果 30 秒没收到心跳，会把 Agent 标记为离线。Agent 上线、离线、恢复都会进入审计日志。

> 这部分证明系统有真实 Agent 生命周期，而不是前端写死数据。

## 3. 端到端 perf 演示，1:20-4:00

这一段要完整录下来，不要剪辑。

### 你要点什么

1. 在 `任务总览` 点击右上角 `新建任务`。
2. 弹窗中填写：
   - `目标 Agent`：`local-agent`
   - `目标进程 PID`：填写 `{CPU_PID}`；没有准备就填 `1`
   - `采样时长（秒）`：`8`
   - `采样频率（Hz）`：`49`
   - `采集器`：`CPU / perf`
   - `持续性能采集`：不要勾选
3. 点击 `下发采集任务`。
4. 回到任务列表，等状态变成 `已完成`。
5. 点击这条任务。
6. 展示详情页：
   - `采样火焰图`
   - `热点函数 TopN`
   - `可验证归因`
   - `状态历史`

### 你要说什么

> 我现在下发一个 perf 采集任务。这里填写目标 Agent、目标 PID、采样时长和采样频率。任务创建后会先进入等待执行，Agent 领取后进入正在采集，采集完成后进入正在分析，Analyzer 生成结果后进入已完成。

任务完成后说：

> 这里是任务详情。火焰图展示调用栈层级和采样权重，块越宽代表这个栈帧出现得越多。右侧 TopN 是热点函数排序，可验证归因基于 TopN 和规则证据生成，不是随便猜结论。

指向状态历史：

> 状态历史记录了每一次状态迁移，并带有 reason。这里显示到毫秒，是为了看清短任务在同一秒内完成的多个状态变化。

### 如果火焰图单调怎么说

> 如果当前环境里 perf 没采到完整 native 栈，系统会标记 degraded，并用 `/proc` 采样做降级可视化。它不会假装采到了真实 perf 栈。换到满足 perf 权限的 Linux 机器上，会展示更完整的调用栈。

## 4. eBPF 现场演示，4:00-6:00

题目明确要求现场制造 IO 或调度抖动。这里用 `dd`。

### 你要点什么

1. 回到 `任务总览`。
2. 点击 `新建任务`。
3. 弹窗中填写：
   - `目标 Agent`：`local-agent`
   - `目标进程 PID`：`1`
   - `采样时长（秒）`：`8`
   - `采样频率（Hz）`：`10`
   - `采集器`：`eBPF 内核探针`
   - `持续性能采集`：不要勾选
4. 点击 `下发采集任务`。
5. 马上切到 PowerShell，执行：

```powershell
docker compose exec agent sh -lc "dd if=/dev/zero of=/tmp/minidrop-io.bin bs=1M count=512 conv=fsync"
```

6. 切回浏览器，等 eBPF 任务完成。
7. 点击 eBPF 任务详情。
8. 展示 `内核写入分布`。

### 你要说什么

> 现在演示 eBPF 采集器。这个采集器使用 bpftrace 挂载内核态 `kprobe:vfs_write`，观察写入事件。

执行 `dd` 后说：

> 我现在用 `dd` 现场制造一次 IO 写入。任务完成后，页面会展示采样窗口内的内核写入分布。

进入详情页后说：

> 这里的内核写入分布就是 eBPF 的专项可视化。它和 perf 不一样，perf 更偏 CPU 调用栈，eBPF 可以从内核探针角度观察系统行为。

### 如果 eBPF degraded 怎么说

> eBPF 对宿主机内核和容器权限要求比较高，需要 Linux kernel tracing 能力和 privileged 容器。docker-compose 已经给 Agent 配置了 privileged。如果当前 WSL2 或 Docker Desktop 内核不支持，系统会明确标记 degraded，不会伪造结果。

## 5. Continuous Profiling，6:00-7:20

### 你要点什么

1. 回到 `任务总览`。
2. 点击 `新建任务`。
3. 弹窗中填写：
   - `目标 Agent`：`local-agent`
   - `目标进程 PID`：`{CPU_PID}`；没有就填 `1`
   - `采样时长（秒）`：`5`
   - `采样频率（Hz）`：`10`
   - `采集器`：`CPU / perf`
   - 勾选 `持续性能采集`
4. 点击 `下发采集任务`。
5. 回到首页下方 `持续采样时间轴`。
6. 如果已经有切片，选择：
   - Agent：`local-agent`
   - 开始时间：当前时间往前几分钟
   - 结束时间：当前时间
7. 点击 `查询窗口`。
8. 点击一个切片查看详情。

### 你要说什么

> Continuous Profiling 不是只做一次采集，而是把采样拆成连续时间切片。每个切片完成后，系统会自动创建下一个切片。

> 页面下方的持续采样时间轴可以按 Agent 和时间范围回看切片，点击某个切片就能查看对应窗口的火焰图和分析结果。

如果暂时没数据：

> 切片需要等第一个持续任务完成后才会出现。正式录制前可以提前创建一个持续采样任务，让时间轴里先有数据。

## 6. 自然语言采集加分项，7:20-8:10

### 你要点什么

1. 回到 `任务总览`。
2. 找到输入框 `一句话创建可验证任务`。
3. 输入：

```text
对 PID 1 做 5 秒 py-spy 采集，频率 10Hz
```

4. 点击 `解析并下发`。
5. 看到任务出现在最近任务列表即可，不必等完整完成。

### 你要说什么

> 这是自然语言采集加分项。用户用一句话描述采集意图，系统解析出 PID、采集器、时长、频率和 Agent，然后创建任务。

> 如果用户没有写 PID，系统不会乱猜目标，而是提示必须包含目标 PID，避免性能诊断选错进程。

## 7. 工程化和交付物，8:10-9:00

### 你要点什么

1. 打开 GitHub 或本地 `README.md`。
2. 展示技术栈。
3. 展示评审环境要求。
4. 展示命令：
   - `docker compose up --build`
   - `make demo`
5. 快速展示 `DESIGN.md` 的架构图或状态机图。

### 你要说什么

> 前端使用 React + TypeScript + React Router + Zustand，后端使用 FastAPI + SQLAlchemy + MySQL，Agent 和 Analyzer 使用 Python。Docker Compose 会启动 Server、Agent、MySQL、MinIO 和前端静态资源。

> README 写明了评审环境要求，包括 Ubuntu 22.04、Linux kernel 5.4+、Docker Compose v2，以及 eBPF 所需的 privileged 权限。

> 设计文档里有架构图、状态机迁移图、关键取舍、AI 协作和后续计划。

## 8. 最得意的设计与“如果让我重做我会怎么改”，9:00-10:00

### 你要点什么

1. 回到一个已完成任务详情页。
2. 鼠标指向 `状态历史`。
3. 鼠标指向 `可验证归因`。

### 你要说什么

> 我最得意的设计是 Server 是唯一任务真相源。Agent 不直接改页面，也不直接改数据库。所有任务状态都必须通过 Server 的状态机迁移，合法状态是 PENDING、RUNNING、UPLOADING、DONE 或 FAILED。每次迁移都会落库，并且必须有 reason。

> 这样即使 Agent 失败、重试或者上传异常，Web 也不会展示虚假的成功状态。评审可以通过状态历史看到每一步发生了什么。

> 如果让我重做，我会把任务事件进一步建模为可重放事件流，并引入持久化队列。Analyzer 不再同步跑在 Server 里，而是通过队列异步消费采集结果。然后补 Agent 身份认证、SSO、多租户权限、符号服务、长期保留策略，以及最小权限 eBPF 探针目录。

最后 10 秒总结：

> 总结一下，这个版本跑通了 Mini-Drop 的核心链路：Web 下发任务、Server 调度、Agent 采集、Analyzer 分析、MySQL 状态落库、Web 展示结果。同时覆盖了 eBPF、py-spy、Continuous Profiling 和自然语言采集。

## 录制检查清单

录完前确认视频里出现过：

- 首页右上角 `控制平面在线`
- 首页 `在线 Agent = 1`
- `采集节点` 页面显示 `local-agent`
- `审计日志` 页面有 Agent 事件
- 点击 `新建任务`
- perf 任务从创建到完成
- 任务详情页有火焰图、TopN、归因、状态历史
- eBPF 任务有内核写入分布，或明确展示 degraded 原因
- Continuous Profiling 的持续采样入口和时间轴
- 自然语言采集输入框
- README 的评审环境要求
- DESIGN.md 的架构图或状态机图

## 常见问题与回答

### 状态历史为什么以前时间看起来一样？

任务太短时，PENDING、RUNNING、UPLOADING、DONE 可能在同一秒内完成。现在状态历史显示到毫秒，可以看出先后顺序。录制时建议采样时长填 `8` 秒。

### 火焰图为什么有时单调？

如果目标进程太空闲，或者 WSL2 / Docker 环境限制导致 perf 没拿到 native 调用栈，火焰图就会比较单调。演示时建议先启动一个 CPU 目标进程，再采它的 PID。

### eBPF 如果跑不起来怎么办？

先确认 Docker Desktop / WSL2 已启动，Agent 容器是 `privileged: true`。如果 bpftrace 仍然不可用，说明宿主机内核能力不足。视频里要诚实说明 degraded 原因，并指出 README 已写明真实 eBPF 需要 Linux kernel tracing 权限。

### AI 怎么参与？

可以这样说：

> 我使用 AI 辅助实现和排查，但验收是按题目逐项验证的，包括 Docker 启动、状态机落库、真实采集、测试覆盖率、端到端流程和工程化目录。
