<p align="center">
  <h1 align="center">🔥 Mini-Drop</h1>
  <p align="center"><strong>轻量级 Linux 性能诊断平台</strong> — 火焰图 · eBPF · AI 归因 · 自然语言采集</p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/C%2B%2B-17-00599c" alt="C++">
  <img src="https://img.shields.io/badge/Go-1.23-00add8" alt="Go">
  <img src="https://img.shields.io/badge/python-3.9+-blue" alt="Python">
  <img src="https://img.shields.io/badge/react-18.x-61dafb" alt="React">
  <img src="https://img.shields.io/badge/gRPC-1.80-2ca5aa" alt="gRPC">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="License">
</p>

---

## 目录

- [快速开始](#快速开始)
- [项目概览](#项目概览)
- [环境要求](#环境要求)
- [整体架构](#整体架构)
- [核心流程](#核心流程)
- [8 种采集器](#8-种采集器)
- [智能归因——5 层引擎](#智能归因5-层引擎)
- [任务状态机](#任务状态机)
- [Web 前端](#web-前端)
- [自然语言采集](#自然语言采集)
- [CLI 命令体系](#cli-命令体系)
- [API 速览](#api-速览)
- [部署与运维](#部署与运维)
- [安全设计](#安全设计)
- [AI Provider](#ai-provider)
- [开发命令](#开发命令)
- [仓库结构](#仓库结构)
- [设计原则](#设计原则)
- [关键决策与取舍](#关键决策与取舍)
- [更新日志](#更新日志)

---

## 快速开始

```bash
# 1. 克隆 + 配置
git clone https://github.com/jiangyulin1/mini-drop.git && cd mini-drop
cp .env.example .env

# 2. 启动全栈服务（PostgreSQL + MinIO + Server + Agent + Analyzer + Web）
docker compose up -d

# 3. 端到端演示：启动热点进程 → 创建采集任务 → 轮询完成 → 验证火焰图
bash demo/demo.sh

# 4. 浏览器打开 http://localhost 查看火焰图与诊断
```

> **纯净 Ubuntu 22.04 首次运行**：需要安装 `make` 和 Docker，见下方[环境要求](#环境要求)和[部署与运维](#部署与运维)章节。

**本地运行（无 Docker）：**

```bash
pip install -e ".[dev]"
python dev.py proto       # 编译 gRPC stub
python dev.py server      # 终端 1：FastAPI :8191 + gRPC :50051
python dev.py agent       # 终端 2：Agent 注册并心跳
python dev.py test        # 运行测试
```

---

## 项目概览

- **核心能力**：
  - Web UI 指定目标 PID、采样率、时长，通过 Server 下发任务给 Agent。
  - Agent 在目标主机上执行 perf / eBPF / py-spy / memory / continuous 采集，产物上传 MinIO。
  - Analyzer 将 perf.data 转为 D3 交互式火焰图 + ECharts TopN 热点排行。
  - 5 层智能归因引擎，LLM 辅助推理但受 Schema 硬约束，每条 claim 可追溯到原始证据。
  - 自然语言采集——用户输入"mysqld CPU 飙高"，系统自动匹配进程、选采集器、定参数。
- **运行形态**：React SPA 前端 + Go API + Python AI/兼容服务 + C++ gRPC 灰度控制面与 Agent + Python Analyzer Worker + PostgreSQL + MinIO。

## 关键设计亮点

- **分体部署架构**：Web/Server/DB/MinIO 跑在 Docker 里，Agent 裸机运行且需要 `privileged` + `pid:host`。权限隔离明确，Agent 可独立升级重启，不影响 Web 服务。
- **gRPC 契约优先**：5 个 `.proto` 文件定义全部通信接口，强类型编译期发现字段不匹配，二进制序列化比 JSON 小 3-5 倍。
- **采集器即插件**：所有采集器实现 `Collector(Protocol)` 协议——新增采集器只需实现 `collect(task) → CollectorResult`，Server 不绑定具体工具。
- **工具驱动的 AI 归因**：LLM 不直接输出自由文本。5 层管线——证据采集 → 候选生成 → 五维置信度校准 → LLM 推理（Few-Shot + Schema 硬约束 + 自修复）→ 修复计划。`rules.json` 外部化，不开 IDE 即可扩展诊断场景。
- **证据驱动诊断流水线 v2**：12 个可持久化节点、结构化 Action、CPU/IO/内存/网络/MySQL/JVM 确定性 Finding、静态 Knowledge 引用和报告 Verifier；接口与证据约束见 [`docs/contracts/drop-insight-api.md`](docs/contracts/drop-insight-api.md) 和 [`docs/contracts/collector-evidence.md`](docs/contracts/collector-evidence.md)。
- **自然语言采集**：用户描述意图 → LLM function calling 解析 → `/proc` PID 匹配 → 参数 clamp 安全范围 → 自动创建任务。
- **白名单状态机**：`PENDING → RUNNING → UPLOADING → ANALYZING → DONE/FAILED`，每次迁移必写 `reason + actor` 到审计表，不允许跳状态，DONE/FAILED 终态不可回滚。
- **AI 开关分层降级**：`none` / `nlp-only` / `rca-only` / `full` 四级可切换，不配 API Key 时火焰图等核心功能不受影响，AI 自动降级为纯规则引擎。
- **eBPF 零侵入观测**：bpftrace 内核探针实时采集块设备 IO 延迟分布，不改代码、不重启服务。Web 端 ECharts histogram 绿→红渐变着色 + P50/P95/P99 分位估算。
- **交互式火焰图 + TopN 联动**：D3 火焰图支持缩放、搜索、hover 详情；点击 TopN 柱状图的函数名，火焰图自动高亮对应栈帧。

---

## 环境要求

| 项 | 要求 |
|------|------|
| **操作系统** | Ubuntu 22.04 / 20.04（其他 Linux 发行版需自行适配） |
| **Linux 内核** | 5.4+（eBPF 需要内核支持 BPF 特性） |
| **Docker** | Engine 20.10+ + Compose v2 |
| **make** | `sudo apt-get install -y make`（纯净 Ubuntu 需额外安装） |
| **内存** | 8 GB 以上（PostgreSQL + MinIO + Server + Web 合计约 2 GB） |
| **磁盘** | 20 GB 可用空间（Demo 产物约 500 MB） |
| **Python**（仅本地模式） | 3.9+ |
| **perf** | `linux-tools-$(uname -r)` — 用于 CPU 火焰图采集 |
| **bpftrace** | 0.14+ — 用于 eBPF IO 延迟采集 |
| **py-spy** | 0.3+ — 用于 Python 用户态采样 |

**纯净 Ubuntu 22.04 首次准备（以下命令全部复制执行即可）：**

```bash
# 安装 Docker（如未安装）
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# 登出后重新登录使组生效

# 安装 make
sudo apt-get update && sudo apt-get install -y make

# 安装 perf 和 bpftrace（可选，用于本地模式和 eBPF 演示）
sudo apt-get install -y linux-tools-$(uname -r) bpftrace
pip install py-spy

# 设置 perf 权限（容器内也需要宿主机允许）
sudo sh -c 'echo kernel.perf_event_paranoid=1 > /etc/sysctl.d/99-mini-drop.conf'
sudo sysctl -p /etc/sysctl.d/99-mini-drop.conf

# 克隆项目
git clone https://github.com/jiangyulin1/mini-drop.git && cd mini-drop
cp .env.example .env

# Docker 全栈启动
docker compose up -d

# 一键演示
bash demo/demo.sh
```

**Agent 容器权限：** perf 和 bpftrace 需要访问宿主机内核。Docker Compose 已配置 `privileged: true` + `pid: host` + `SYS_ADMIN` + `BPF` + `PERFMON`。

**bpftrace 兼容性说明：**
- 内核 5.15 上 bpftrace 0.14 不支持 `BEGIN` / `END` 特殊探针，Agent 采集脚本已改用 `interval:s:1` 定时打印替代，采集器端 SIGTERM 终止。
- 内核 5.15 使用 `blk_account_io_done` 替代 `blk_update_request`（后者在 5.15 的 kprobe 列表中不存在）。

---

## 整体架构

```mermaid
flowchart LR
    User["用户浏览器"] --> Web["React SPA\nAnt Design + ECharts"]
    Web -->|REST + SSE| API["Go API :8080\n鉴权 / 任务 / Agent / 兼容路由"]
    API -->|原生 SQL| Postgres["PostgreSQL"]
    API -->|领域编排与兼容接口| Server["Python 服务\nAI / Analyzer / 兼容 API :8191"]
    Server -->|默认任务下发 / 心跳| GRPC["Python gRPC :50051"]
    Go -->|共享 PostgreSQL 状态机| NativeControl["C++ gRPC 灰度控制面 :50052"]
    NativeControl --> Agent["C++ Agent\n受限进程组 + pid:host"]
    GRPC --> Agent
    Agent --> NativeCollectors["C++ 插件注册表\nperf / eBPF / py-spy / pprof / memory / continuous"]
    GRPC --> PythonAgent["Python Agent\n兼容与回滚执行路径"]
    Agent -->|上传产物| MinIO["MinIO\n对象存储 + 预签名 URL"]
    Server -->|持久化| Postgres
    Server -->|创建 AnalysisJob| Postgres
    Analyzer["Analyzer Worker\nLease + Retry + Dead Letter"] -->|领取 AnalysisJob| Postgres
    Analyzer -->|读取原始产物 / 写回结果| MinIO
    Server -->|读取产物| MinIO
    Server -->|可选| AI["OpenAI-compatible\nDeepSeek / OpenAI 等"]
    Web -->|实时事件| SSE["SSE Stream\n任务 / Agent / 诊断"]
```

**核心端口：**

| 服务 | 端口 | 说明 |
|------|------|------|
| Web (nginx) | 80 | React SPA + API 反向代理 + SSE |
| Go API | 8080（容器内） | Web 唯一入口；鉴权、任务与 Agent 原生接口 |
| Python 服务 | 8191（容器内） | Analyzer、AI 领域编排和兼容接口 |
| Server gRPC | 50051 | Agent 通信 |
| PostgreSQL | 5432 | 任务/事件/审计/诊断 |
| MinIO API | 9000 | 对象存储 |
| MinIO Console | 9001 | 管理面板 |

### 架构决策

**为什么采用 C++ / Go / Python / React 四层？** C++ Agent 贴近 Linux 内核，负责真实采集、资源限制、超时和进程组取消；Go API 负责高并发 HTTP 入口、鉴权、任务控制，以及 AI 写入口的 Schema、预算、安全策略和审计；Python 保留性能数据分析、模型调用与多轮诊断领域编排；React 负责交互式火焰图和诊断工作台。迁移采用绞杀者模式，先保持旧接口可用，再逐个替换，避免为了“换语言”破坏已经跑通的链路。当前原生边界及验证记录见 [`docs/architecture/implementation-status.md`](docs/architecture/implementation-status.md) 和 [`docs/contracts/go-diagnosis-query.md`](docs/contracts/go-diagnosis-query.md)。

当前默认栈由 **C++ 控制面（`control-plane`，50051）与 C++ 原生 Agent（`native-agent`）** 构成；Python gRPC 控制面通过 `docker-compose.python-control.yml` 作为回滚，Python 兼容 Agent 通过 `docker compose --profile python-agent up -d agent` 作为本地开发/回滚路径（默认不启动）。C++ Agent 已通过 Collector Registry 原生支持 `perf_cpu`、`ebpf_io`、`pyspy`、`go_pprof`、`memory_smaps`、`sys_metrics` 与 `continuous_perf`；`java_async` 只在 async-profiler 运行时实际存在时声明能力，不用虚假 capability 掩盖缺失依赖。

**为什么 gRPC？** Server ↔ Agent 使用 gRPC，5 个 `.proto` 文件定义全部通信接口，参考 DeepFlow `message/` 模式。强类型契约编译期发现字段不匹配，二进制序列化比 JSON 小 3-5 倍。Web ↔ Server 保留 REST/JSON——浏览器原生支持，易于 debug 和 curl 测试。

**为什么分体部署？** Agent 需要 `privileged` + `pid:host` + `BPF` 等内核级权限，与 Web/Server 混在一个 Docker 里权限模型很脏。分开后，Agent 可以独立升级、独立重启，不影响 Web 服务。生产环境中一台 Server 管理多台主机的 Agent 是标准拓扑。

**采集器统一接口。** 所有采集器实现 `Collector(Protocol)` 协议，Server 不绑定具体工具。新增采集器只需实现 `collect(task) → CollectorResult`。

**Analyzer 火焰图管线。** Agent 可以就地生成分析结果；当只上传原始 `perf.data` 时，Server 创建幂等 `AnalysisJob`，独立 Analyzer Worker 通过数据库租约领取，从 MinIO 下载原始产物并执行 `perf script → stackcollapse-perf.pl → flamegraph.pl`，再把 JSON、SVG 和 TopN 写回 MinIO。Worker 支持版本化 Registry、重试、租约恢复、死信和人工重放。

**MINIO_PUBLIC_ENDPOINT 设计。** Docker 内部 MinIO 使用 `minio:9000`。Agent 通过 gRPC `FetchConfig` 获取 MinIO 地址时，Server 优先下发 `MINIO_PUBLIC_ENDPOINT`（外部可达地址），确保分体部署时 VM Agent 能直传产物到 Windows MinIO。浏览器预签名 URL 同理使用外部地址。

**SQLAlchemy + PostgreSQL 持久化。** 开发默认通过 `docker-compose.yml` 使用 PostgreSQL，`docker-compose.local.yml` 则切 SQLite 零配置。`expire_on_commit=False` 允许 session 关闭后继续读取数据。

---

## 核心流程

### 1) 端到端采集全链路

用户创建采集任务 → Server 写入 PostgreSQL 并置 `PENDING` → Agent 心跳拉取任务 → Agent 执行 perf/eBPF 采集并上传 MinIO → Agent 调用 `NotifyResult` → Server 持久化 Artifact、置 `ANALYZING` 并创建幂等 AnalysisJob → Analyzer Worker 通过租约领取 → 从 MinIO 读取输入、生成或验证可视化结果 → 原子写入输出 Artifact 和 AnalysisJob 终态 → Server 任务置 `DONE`。

全程每一步迁移写入 `task_status_events` 表（`from_status → to_status, reason, actor`）。

### 2) eBPF IO 延迟采集链路

Agent 检查并按需挂载 `tracefs` → 启动 `bpftrace io_latency.bt -o io_latency.txt` → 脚本挂载稳定的 `tracepoint:block:block_rq_issue` 与 `tracepoint:block:block_rq_complete` → 按设备号和扇区关联请求，计算 `(nsecs - start) / 1000` 微秒延迟 → Agent 主动发送 SIGINT 结束采样 → 解析区间计数 → 输出 `ebpf_metrics.json` → Web 使用 EBPFHistogram 渲染延迟分布与 P50/P95/P99。

采集器会区分 tracefs 不可用、探针附着失败和真实 IO 样本为空，不会把 bpftrace 在附着阶段的退出误判成成功。

### 3) 智能归因链路

触发诊断 → 证据采集层从产物提取结构化数据（TopN 热点、占比、采样数、栈深度、IO P99、RSS 趋势）→ 候选生成层匹配 `rules.json` 生成候选原因 → 置信度校准层五维打分 → 低于阈值剪枝 → 高置信度候选 + 原始证据发给 LLM → Few-Shot + JSON Schema 硬约束 + tool_choice → 输出校验（Schema + evidence_refs 完整性）→ 失败自动重试 2 次 → 修复计划（紧急/高/中三级风险 + 预估工作量）→ 用户标注反馈回写校准层权重。

### 4) 自然语言采集链路

用户输入 "mysqld CPU 飙高，帮我看看" → `POST /api/nlp/parse` → LLM function calling 解析意图（进程名 + 采集器类型 + 时长 + 采样率）→ 参数 clamp 到安全范围 → 前端展示确认界面 → 用户选择候选 PID → `POST /api/tasks` 创建任务 → 完成后 `POST /api/nlp/summarize` 生成自然语言总结 + 追问建议。

---

## 8 种采集器

| 采集器 | 类型 key | 采集工具 | 产出物 | Web 可视化 |
|--------|----------|----------|--------|------------|
| **perf CPU** | `perf_cpu` | perf record | flamegraph.json + SVG + top.json | D3 交互式火焰图 + ECharts TopN 联动 |
| **eBPF IO** | `ebpf_io` | bpftrace | IO 延迟 histogram JSON | ECharts 柱状图 + P50/P95/P99 |
| **py-spy** | `pyspy` | py-spy | 火焰图 SVG（--native 混合栈） | iframe SVG 渲染 |
| **Java** | `java_async` | async-profiler | HTML 火焰图 + JFR | iframe HTML 渲染 |
| **Go pprof** | `go_pprof` | pprof | pprof 原始数据 + SVG | SVG / Alert 提示 |
| **Memory** | `memory_smaps` | /proc/PID/smaps | 内存分段 + RSS 趋势 | ECharts 内存时序折线图 |
| **SysMetrics** | `sys_metrics` | /proc 多维 | CPU/线程/FD/网络/IO 时序 | ECharts 多维仪表盘 |
| **Continuous** | `continuous_perf` | perf record（周期） | 多窗口火焰图 + 汇总 | 窗口选择器 + 时间轴回放 |

所有采集器实现统一接口：

```python
class Collector(Protocol):
    def collect(self, task: CollectorTask) -> CollectorResult: ...
```

---

## 智能归因（5 层引擎）

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌────────┐    ┌──────────┐
│ ① 证据   │ → │ ② 候选    │ → │ ③ 置信度 │ → │ ④ LLM  │ → │ ⑤ 修复   │
│ 采集     │    │ 生成      │    │ 校准     │    │ 推理   │    │ 计划     │
└──────────┘    └───────────┘    └──────────┘    └────────┘    └──────────┘
     ↑                                                            │
     └─────────────── ⑥ 反馈闭环 (用户标注修正权重) ─────────────┘
```

**逐层说明：**

| 层 | 职责 | 关键设计 |
|----|------|----------|
| **① 证据采集** | 从产物提取结构化证据——TopN 热点、栈深度、IO P99、RSS 趋势 | 不送整个火焰图 JSON 给 LLM，Token 太大且引入幻觉 |
| **② 候选生成** | 规则引擎匹配 `rules.json` 生成候选原因 | `rules.json` 外部化——运维团队不开 IDE 即可扩展诊断规则 |
| **③ 置信度校准** | 五维打分——正确性、完整性、可操作性、时效性、一致性 | 低于阈值剪枝，避免 AI 被低质量候选污染 |
| **④ LLM 推理** | 高置信度候选 + 原始证据发给 LLM，Few-Shot + JSON Schema 硬约束 | 核心原则：**不让 LLM 输出自由文本**。输出过 Schema 校验 + 引用完整性校验，失败自动重试 2 次 |
| **⑤ 修复计划** | claims 转分级修复建议——紧急/高/中三级，每条带预估工作量 | `requires_user_confirm` 标记需人工介入的风险操作 |
| **⑥ 反馈闭环** | 用户标注"准确/不准确"回写校准层权重矩阵 | 持续优化，不是一次性推理 |

**约束：** 每条 claim 必须带 `evidence_refs`；未配置 AI Key 时 → 规则引擎独立输出降级报告，火焰图等核心功能不受影响。

**如果重做：** 当前 5 层是线性管道（~8s 端到端）。若改为 DAG 并行模式（证据采集 + 候选生成同时启动，LLM 收到第一批证据就流式输出），预计降到 ~2s。另外 `rules.json` 目前是镜像固化的静态文件，可加 gRPC stream 推送通道——Server 更新规则后实时推给所有 Agent，本地热加载，不停机不重启。

### AI 集群诊断控制层

`/api/v1/diagnoses` 是独立于单个 Task 的可恢复诊断会话，覆盖自然语言意图、历史拓扑快照、候选假设、已有证据复用、受控探针、预算、R2 单次审批、证据血缘和等级置信报告。模型只负责意图理解；探针必须来自服务端注册表，R3 重启/迁移/配置修改不会自动执行。

当前轻量版由请求上下文提供服务实例与宿主机映射；没有可靠映射时进入 `NEEDS_SCOPE_CONFIRMATION`，不会向 Agent 扩散采集。后台扫描器使用持久化状态和短租约恢复会话，完成的探针通过 `diagnosis_step_id` 幂等关联，避免重复下发。

诊断结论包含 `cluster_assessment`，会把目标实例、同宿主机实例和一跳下游实例的系统指标放在同一证据平面比较，用于区分自身代码热点、噪声邻居、宿主机资源争抢和下游依赖问题。模糊输入不会被自动执行；系统只返回带审核注释的 `diagnostic_commands`，每条命令都有风险等级、`evidence_refs`、置信度和 `auto_execute=false`。

---

## 任务状态机

```
PENDING → RUNNING → UPLOADING → ANALYZING → DONE
   │         │          │            │
   └─────────┴──────────┴────────────┘→ FAILED
```

- 每次迁移必须提供非空 `reason`，写入 `task_status_events` 表（`from_status → to_status, reason, actor`）
- DONE / FAILED 是终态，拒绝再迁移
- 合法迁移路径由 `ALLOWED_TRANSITIONS` 白名单控制——不允许跳过中间状态
- 每个 Actor（web / server / agent / analyzer / ai）的迁移可审计
- Web 端 SSE 实时推送状态变更 + toast 通知

---

## Web 前端

| 页面 | 路由 | 功能 |
|------|------|------|
| 任务面板 | `/` | 统计卡片、NLP 输入、任务搜索/排序/删除、Agent 列表、SSE 实时通知 |
| 任务详情 | `/task/:id` | D3 交互式火焰图 + ECharts TopN 联动、eBPF IO Histogram、状态时间线、AI 归因 |
| AI 集群诊断 | `/ai-diagnosis` | 自然语言诊断、拓扑目标、假设、受控探针审批、证据血缘与等级置信报告 |
| 诊断历史 | `/diagnoses` | 全量诊断记录、置信度筛选、搜索过滤 |
| Agent 详情 | `/agent/:id` | 资源趋势折线图、采集能力标签、关联任务搜索 |
| 审计日志 | `/audit` | 事件筛选、自由搜索、时间倒序 |
| 系统设置 | `/settings` | AI 连通性测试、API Key 管理、服务健康 |

**技术栈：** React 18 + Ant Design 5 + d3-flame-graph + ECharts + Vite 5 + SSE + React Router 6

**交互设计：**
- **火焰图 + TopN 联动**：点击 ECharts 柱状图的函数名 → 通过 React ref 调用 `flameRef.current.search(funcName)` → D3 火焰图高亮匹配帧
- **暗色模式持久化**：localStorage 存 `mini-drop-theme`，切换即时生效
- **ErrorBoundary 全局捕获**：渲染异常降级为友好错误页（重试/回首页），不白屏
- **自动轮询 + SSE 双通道**：任务执行中 5s 轮询 + SSE 实时事件，确保数据不丢

---

## 自然语言采集

**设计思路：** 用户描述意图 → LLM function calling 解析 → `/proc` 进程名 PID 匹配（**不在 LLM 中做 PID 解析**——这是安全关键点）→ 参数 clamp 安全范围 → 前端确认 → 自动创建任务。

```
用户输入 "mysqld CPU 飙高，帮我看看"
  → POST /api/nlp/parse {query}
    → LLM function calling → {process_name: "mysqld", collector_type: "perf_cpu", duration_sec: 15, sample_rate: 49}
    → /proc 扫描匹配 mysqld → candidate_pids: [{pid: 1234, comm: "mysqld"}]
  → 前端展示确认界面 + PID 选择器
  → POST /api/tasks {name: "NLP: mysqld", agent_id: ..., target_pid: 1234, collector_type: "perf_cpu", ...}
  → 完成后 POST /api/nlp/summarize → AI 总结 + 追问建议
```

---

## CLI 命令体系

所有命令默认 JSON 输出，退出码语义明确（`diff-top` 超阈值返回 2，可做 CI 门禁）。

```bash
# 基础
micro-drop serve                    # 启动 Server
micro-drop agent                    # 启动 Agent
micro-drop version                  # 显示版本
micro-drop ai-config                # AI 配置 + feature flag 状态
micro-drop install-check            # 检查系统依赖和权限

# 采集 / 管理
micro-drop collect --agent agent_1 --pid 1234 --collector perf_cpu  # 远程采集
micro-drop status                   # Server/Agent/Task 概览
micro-drop task-cancel --task-id xxx # 取消任务
micro-drop watch-task --task-id xxx  # 轮询任务直到终态

# NLP / AI
micro-drop parse "nginx CPU 飙高"   # 自然语言解析
micro-drop summarize --top-json top.json           # TopN 总结
micro-drop diagnose-local --evidence evidence.json  # 离线 RCA
micro-drop feedback-stats           # 反馈准确率统计

# 差分 / CI
micro-drop diff-top --base before.json --head after.json --threshold 5
micro-drop ci-check --base before.json --head after.json   # CI 门禁 (exit 2)
micro-drop alert --top-json top.json --hotspot-threshold 70 # 热点告警 (exit 2)

# 本地采集（无需 Server）
micro-drop perf-top --pid 1234 --duration 10  # 本地 perf TopN

# 存储 / 报告
micro-drop storage-ls                          # 列举 MinIO 产物
micro-drop storage-prune --older-than-days 30  # 清理旧产物（dry-run）
micro-drop report --top-json top.json --format markdown --output report.md

# Shell 补全
micro-drop completion --shell bash
# eval "$(micro-drop completion --shell bash)"
```

---

## API 速览

### 任务

```bash
POST   /api/tasks                          # 创建采集任务
GET    /api/tasks?search=&sort_by=&sort_order=  # 列表（搜索+排序+分页）
GET    /api/tasks/{id}                     # 详情
DELETE /api/tasks/{id}                     # 归档（仅终态，保留状态、Artifact 与诊断证据）
GET    /api/tasks/{id}/events              # 状态迁移链
GET    /api/tasks/{id}/artifacts           # 产物列表
GET    /api/tasks/{id}/artifacts/{type}/content  # 产物内容
POST   /api/tasks/{id}/diagnose            # AI 诊断
GET    /api/tasks/{id}/diagnoses           # 诊断历史
```

### 诊断 + Agent + NLP

```bash
GET    /api/diagnoses/{id}                 # 诊断详情（报告+工具+修复计划）
POST   /api/diagnoses/{id}/feedback        # 提交反馈
POST   /api/v1/diagnoses                    # 创建 AI 集群诊断会话
GET    /api/v1/diagnoses/{id}               # 会话详情并推进可恢复工作流
POST   /api/v1/diagnoses/{id}/approvals     # R2 探针单次批准/拒绝
GET    /api/v1/probes                       # 受控探针注册表
GET    /api/agents                         # Agent 列表（含离线检测）
GET    /api/audit-logs                     # 审计日志
POST   /api/nlp/parse                      # 自然语言解析
POST   /api/nlp/summarize                  # 任务结果 AI 总结
GET    /api/storage/presign?key=...        # MinIO 预签名 URL
GET    /api/tasks/{id}/artifacts/{type}/download  # 经 Server 流式下载产物
GET    /api/metrics                        # Prometheus 指标
GET    /api/events/stream                  # SSE 实时事件流
GET    /api/healthz                        # 健康检查（含 DB + 存储检测）
```

---

## 部署与运维

### Docker 部署

```bash
git clone https://github.com/jiangyulin1/mini-drop.git && cd mini-drop
cp .env.example .env
docker compose up -d
```

### 分体部署（推荐）

```
Windows Browser ── HTTPS 443 ──> Control VM
                                   ├── Server / PostgreSQL / MinIO / Web
Linux Worker 1 ── gRPC TLS 50051 ──┤
Linux Worker 2 ── gRPC TLS 50051 ──┤
Linux Worker 1/2 ── MinIO 9000 ────┘
```

```bash
# Control VM：不会启动本机 Agent，Token 和 TLS 强制开启
cp deploy/env/control.env.example deploy/env/control.env
bash deploy/scripts/generate-dev-certs.sh 10.0.0.10
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml up -d --build

# 每台 Worker：修改 AGENT_ID、AGENT_IP_ADDR 和 Control 地址，并复制 ca.crt
cp deploy/env/worker.env.example deploy/env/worker.env
docker compose --env-file deploy/env/worker.env -f docker-compose.worker.yml up -d --build

# 或使用裸机 systemd Agent（安装后不会自动启动，需先编辑 worker.env）
sudo bash deploy/scripts/install-worker.sh "$PWD"
```

完整步骤、证书分发、端口矩阵和验收命令见
[三机 VM 部署与联调指南](docs/guides/multi-node-deployment.md)。SSH 自动化不包含在当前阶段。

### 离线 / 本地 Docker（SQLite，无需拉取外部镜像）

```bash
npm --prefix web run build
# 离线演示使用 Python 兼容 Agent（在 base compose 中挂 python-agent profile）
docker compose -f docker-compose.yml -f docker-compose.local.yml --profile python-agent up -d --build server agent web
```

### 一键演示

```bash
# 前提：docker compose up -d 已运行
bash demo/demo.sh

# 快速过场（每个场景 5 秒）
DEMO_QUICK=1 bash demo/demo.sh

# 只跑 CPU + 内存场景
DEMO_SCENES=cpu,memory bash demo/demo.sh

# 分体部署模式（API 在远程）
SPLIT_HOST=172.17.144.1 bash demo/demo.sh
```

### 演示脚本说明

| 脚本 | 用途 |
|------|------|
| `demo/demo.sh` | 主演示：6 个场景，自动检测 Docker/本地模式 |
| `demo/vm_test_targets.py` | 15 种负载场景生成器 |
| `demo/cpu_hotspot.py` | 简单热点进程（fib/sort/json 每 60s 循环切换） |
| `demo/test_runner.py` | 自动化 E2E 测试套件（16 场景 + 报告） |
| `demo/vm_deploy.sh` | 环境一键部署（依赖安装 + 编译 + 测试） |

### VM 端 perf 权限

```bash
sudo sysctl -w kernel.perf_event_paranoid=1
```

### MinIO 公网端点

Docker 内 MinIO 使用 `minio:9000`。三机模式下 `MINIO_PUBLIC_ENDPOINT` 必须填写 Worker
可访问的 Control 地址。浏览器下载已经改为经 Server 流式转发，因此 Windows 无需访问 9000：

```bash
# 分体部署（仅 Worker 访问）
MINIO_PUBLIC_ENDPOINT=http://10.0.0.10:9000
```

---

## 安全设计

| 层次 | 措施 |
|------|------|
| **HTTP API** | Bearer / X-API-Key / query token 三通道认证 |
| **gRPC** | Token 认证拦截器 + TLS；三机 Compose 默认强制启用 |
| **产物读取** | 沙箱限制在 `MINI_DROP_ARTIFACT_ROOT` 内 |
| **产物下载** | Server 校验任务归属后流式转发；浏览器无需直连 MinIO |
| **Agent 保护** | 拒绝自剖析（target_pid == self PID 时拒绝）；参数 clamp 防资源耗尽 |
| **AI 诊断控制层** | Pydantic 拒绝未知字段、服务范围白名单、固定探针注册表、R2 人工审批、主机/实例/并发/时长预算、多机证据对比、命令仅建议不执行 |
| **密钥管理** | `.env` 已 gitignore，`.env.example` 仅模板占位符 |
| **Nginx** | CSP / HSTS / X-Frame-Options / 速率限制 |

**生产开启认证：**

```bash
MINI_DROP_API_KEY=$(openssl rand -hex 32)
MINI_DROP_INTERNAL_GATEWAY_TOKEN=$(openssl rand -hex 32)
MINI_DROP_GRPC_TOKEN=$(openssl rand -hex 32)
MINI_DROP_API_AUTH_ENABLED=1
MINI_DROP_GRPC_AUTH_ENABLED=1
```

`MINI_DROP_INTERNAL_GATEWAY_TOKEN` 只用于 Go API 到 Python 分析服务的
内部可信跳转，必须与用户 API Key 分开生成，且不应提供给浏览器或 Agent。

Web 顶栏填写的是 `MINI_DROP_API_KEY`（Control REST 访问凭据），不是 AI Provider Key。
认证失败时 Agent/任务状态显示为“未知”并给出明确提示，不会把接口失败误显示成 0 个 Agent。

生产环境可使用 `MINI_DROP_API_PRINCIPALS_JSON` 配置多个身份。每个身份包含
`id`、`api_key`、`roles`、`agent_ids`、`service_ids` 和 `environments`。
`viewer` 只读，`operator` 可创建任务和诊断，`approver` 只能审批受控探针，
`admin` 拥有全部权限；不在资源范围内的 Agent/服务不会被允许操作。

---

## AI Provider

兼容任意 OpenAI-style `/v1/chat/completions` 接口：

```bash
export MINI_DROP_AI_ENABLED=full
export MINI_DROP_AI_PROVIDER=deepseek
export MINI_DROP_AI_BASE_URL=https://api.deepseek.com
export MINI_DROP_AI_API_KEY=<your-key-here>
export MINI_DROP_AI_MODEL=deepseek-v4-flash
```

**开关层级：**

```
MINI_DROP_AI_ENABLED=none      → nlp=off, rca=off, summarize=off
MINI_DROP_AI_ENABLED=nlp-only  → nlp=on,  rca=off, summarize=off
MINI_DROP_AI_ENABLED=rca-only  → nlp=off, rca=on,  summarize=off
MINI_DROP_AI_ENABLED=full      → nlp=on,  rca=on,  summarize=on
```

不配 API Key 时核心采集/火焰图功能不受影响，AI 功能自动降级为规则引擎。

启动 Web 后，可通过“AI 集群诊断”标题区的“AI 服务检测”按钮主动运行完整套件。验证覆盖
Provider 账户/模型/对话、自然语言任务解析、集群诊断意图与安全约束、AI 总结、RCA
证据引用校验。弹窗不会返回 API Key、余额金额或原始思维链；并发运行会被拒绝，避免
重复消耗 Token，也不额外占用一级导航页面。

---

## 开发命令

```bash
# Makefile（Linux / macOS / Git Bash）
make proto          # 编译 gRPC stub
make server         # 启动 Server
make agent          # 启动 Agent
make test           # 运行测试
make eval           # 运行诊断 golden scenarios，生成 JSON/Markdown 报告
make lint           # 语法检查 + ruff + mypy
make fmt            # ruff format
make demo           # bash demo/demo.sh

# dev.py（跨平台）
python dev.py proto
python dev.py server
python dev.py agent
python dev.py test
python dev.py lint
python dev.py install              # pip install -e ".[dev]"

# 完整开发流程
pip install -e ".[dev]"
python dev.py proto
python dev.py server      # 终端 1
python dev.py agent       # 终端 2
python dev.py test
npm --prefix web run dev  # Vite HMR :5173（可选）
```

---

## 仓库结构

```
mini-drop/
├── native/               C++17 控制面与原生 Agent
├── apiserver/            Go API、鉴权、持久化查询与 SSE
├── analyzer/             Python 离线分析与 FlameGraph 工具
├── server/               Python 兼容 API/gRPC、Analyzer Worker 与 AI 编排
├── agent/                Python 兼容 Agent 与完整采集器插件
├── web/                  React 18 Web
├── proto/                跨语言 gRPC 契约
├── benchmarks/           统一 AI 诊断用例
├── golden_scenarios/     Golden 回归输入
├── knowledge/            AI 诊断知识条目
├── demo/                 多语言热点目标与故障负载
├── deploy/               Dockerfile、Nginx、环境模板和 systemd
├── scripts/              运维、实验与评测入口
├── tests/                单元、契约、集成和 E2E 测试
├── reports/              机器生成的验收结果
└── docs/                 architecture/guides/ai/contracts/benchmarks
```

### 真实业务诊断测试集（candidate）

仓库同时保留一套与日常合成回归隔离的真实开源缺陷挑战集：

- 公开题面：`benchmarks/real_world/public/cases.json`
- 评测器私有 Oracle：`benchmarks/real_world/private/oracles.json`
- 产品对照定义：`benchmarks/real_world/comparators.json`
- 校验与评分：`scripts/real_world_benchmark.py`
- 方法说明：`docs/benchmarks/真实业务测试集与成熟产品对照.md`
- 云端页面操作：`docs/guides/云端页面真实业务测试全流程.md`

该测试集当前为 `candidate`。只有完成固定 base/fix 版本的多次本地复现、三段快照和稳定症状验证后，案例才可进入正式 Golden；PR 描述本身不算运行证据。

完整目录说明及迁移期保留边界见
[`docs/architecture/repository-layout.md`](docs/architecture/repository-layout.md)。

---

## 设计原则

- **gRPC 契约优先** — proto 是 Server ↔ Agent 唯一契约来源
- **采集器即插件** — 统一 `Collector(Protocol)` 接口，Server 不绑定工具
- **LLM 工具约束** — AI 只能调预定义 tool，不做自由决策；输出过 Schema + 引用校验
- **归因可追溯** — 每条 claim 带 `evidence_refs`，指向原始证据字段
- **多机因果区分** — 目标实例、同宿主实例和下游实例一起比较，避免把最先告警节点误判为根因
- **人机协同执行** — 模糊自然语言只生成带注释的可审核命令，高风险变更始终人工确认
- **状态机驱动** — `ALLOWED_TRANSITIONS` 白名单，每步迁移必带 `reason` + `actor`
- **降级友好** — AI 不可用时核心功能不受影响
- **防御性编程** — 路径沙箱、参数 clamp、预签名白名单、拒绝自剖析
- **密钥不入仓库** — `.env` 已 gitignore，`.env.example` 仅模板占位符

---

## 关键决策与取舍

### 为什么 Analyzer 使用独立 Worker？

Agent 只负责采集，Server 只负责控制和持久化，耗时分析由独立 Worker 承担。这样 API 重启不会丢失分析任务，多个 Worker 可以通过数据库租约并发消费，失败任务可以指数退避、进入死信并人工重放。Agent 仍可就地生成轻量结果，但 Server 不再在 gRPC 请求线程里同步执行 Analyzer。

### 为什么 D3 火焰图而不是 ECharts 热力图？

D3 火焰图天然支持帧级别的缩放、搜索、hover 详情，这是性能分析的核心交互。ECharts 更适合 TopN 柱状图这种标准图表。两者各自发挥所长，通过 React ref 联动。

### 为什么 bpftrace 而非 libbpf / BCC？

bpftrace 对演示场景足够——Shell 一行命令即可挂载内核探针。libbpf 更适合生产环境（CO-RE 可移植、无运行时依赖），但工程复杂度显著更高。当前阶段 bpftrace 是合理的选择，未来可升级到 libbpf。

### 为什么 AI 不直接接入火焰图全量数据？

火焰图 JSON 树可能有数千个节点，直接送 LLM Token 消耗巨大且容易产生幻觉。证据采集层提取 TopN + 结构化指标，既能被规则引擎处理，也能高效喂给 LLM。这是"人肉分析→结构化证据→LLM 推理"的工程化思路。

---

## 更新日志

### 2026-08-01：Go 控制 API 与持久化 SSE

- Go 原生实现任务取消、任务事件、执行尝试、产物元数据、审计查询和 PostgreSQL 驱动的 SSE；
- SSE 覆盖任务状态、Agent 上下线、AI 诊断完成，并支持复合游标断线续传；
- 真实任务 `task_20260801_071045_64721a07` 验证 Go → C++ → Go 的取消链路；
- 修复 AI Worker 对人工确认态反复推进造成的 `advance_failed` 事件洪泛；
- 全量回归：Python 471 项、Go 测试、React 生产构建均通过。
- Go 进一步原生接管产物内容读取与 MinIO 流式下载，加入任务目录归属校验、路径穿越防护、16 MiB 预览上限及安全下载响应头。
- 任务删除改为生产级软归档：活动任务拒绝归档，终态任务从列表隐藏但保留状态、产物、AI 证据和审计；策略见 [任务归档与 AI 证据保留](docs/ai/task-archive-policy.md)。

### 2026-06-21 — Web 前端业务逻辑完善

- **Dashboard 任务管理**：新增搜索（按名称/ID 模糊匹配）、排序（按字段 + 升/降序）、删除（确认弹窗 + 进行中任务保护 + 级联删除事件/产物/诊断/审计日志）。
- **AuditLogs**：新增事件类型下拉筛选、自由文本搜索、时间倒序、复制 task_id。
- **Settings**：新增内联 API Key 输入框 + 保存/清除按钮、AI 连通性测试按钮。
- **AgentDetail**：新增关联任务搜索过滤、ECharts 实例正确 dispose 重建修复内存泄漏。
- **NLPTaskInput**：Agent 选择优先匹配对应采集器能力的在线 Agent。
- **ErrorBoundary**：全局渲染异常捕获，降级为友好错误页（重试/回首页），不白屏。
- **EBPFHistogram**：eBPF IO 延迟分布 ECharts 柱状图——绿→红渐变着色、P50/P95/P99 分位估算、hint 提示。满足 题目要求 "eBPF 必须在 Web 上有自己的可视化形态"。

### 2026-07-31 — Java 与 eBPF 真实链路验收

- Java：新增 Temurin 21 热点目标，Agent 与目标容器安装 async-profiler 4.4，通过目标 mount namespace 输出 HTML 火焰图；真实任务 `task_20260731_043955_b8b2fe` 完成。
- eBPF：探针切换为 block tracepoint，增加 tracefs 能力检查和早退识别；Docker Desktop WSL2 内核真实任务 `task_20260731_044630_5b86a4` 采到 1082 个 IO 延迟样本并完成分析。
- 持续诊断：诊断历史页展示 anomaly → diagnosis 的幂等 Trigger，并增加 `scripts/continuous_soak.py` 长稳验收工具。
- 本节实现取代下方旧版本中的 kprobe 与宽松退出码策略。

### 2026-06-21 — eBPF bpftrace 兼容性修复

- **io_latency.bt**：移除 `BEGIN`/`END` 特殊探针（bpftrace 0.14 + 内核 5.15 无法解析符号），改用 `interval:s:1` 定时打印 histogram。`blk_update_request` → `blk_account_io_done`（内核 5.15 兼容）。
- **ebpf.py**：退出码放宽（`-9`/`-15`/`255` 均视为信号终止），interval 模式解析器取最后一次 histogram 值。
- **验证**：Linux VM 裸机 root Agent，eBPF IO 延迟采集 DONE，163 samples，7 个延迟区间 [32μs ~ 4ms]。

### 2026-06-21 — 分体部署支持

- **init_service.py**：`FetchConfig` 下发 `MINIO_PUBLIC_ENDPOINT` 而非 Docker 内部地址 `minio:9000`，修复分体部署下 VM Agent 产物上传持续失败。
- **demo.sh**：新增 `SPLIT_HOST` 环境变量支持远程 API 模式。
### 更早版本

- **2026-06-20** — 演示体系完善、`make demo` 6 场景一键跑通。
- **2026-06-18** — SSE 实时事件流、NLP 自然语言采集。
- **2026-06-17** — 5 层智能归因引擎（DeepSeek + Few-Shot + 自修复）、Continuous Profiling。
- **2026-06-16** — eBPF IO 延迟 + py-spy Python 采样 + 8 种采集器完备。
- **2026-06-15** — D3 交互式火焰图 + ECharts TopN 联动、Web 前后端打通。
- **2026-06-14** — gRPC 四项服务 + Agent 主循环 + perf CPU 采集器。
- **2026-06-13** — 项目骨架：Proto 契约 + 状态机 + FastAPI + SQLAlchemy。
