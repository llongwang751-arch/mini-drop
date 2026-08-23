# Mini-Drop 新会话交接文档

> 更新时间：2026-08-20
> 本地仓库：`D:\tx\mini-drop`  
> 云端部署目录：`/opt/mini-drop-li-mingyuan`  
> 云端入口：<https://47.112.10.137/>  
> 本文不包含 API Key、SSH 密码、数据库密码、MinIO 密钥或 TLS 私钥。

> **最新交接结论**：本文件前 25 节保留项目全貌；第 26 节之后是 2026-08-20 的最新增量、工作现场、下一位 AI 的任务边界以及监督验收协议。新接手者必须优先阅读第 26 节及后续内容，不能只按 8 月 19 日的旧状态工作。

---

## 1. 给新会话的第一句话

请先阅读本文件和下面两份主文档，再检查 `git status`，不要重置、覆盖或重新初始化仓库。当前工作区包含大量尚未统一提交的有效改动，必须在现状上继续：

1. `D:\tx\mini-drop\docs\guides\Mini-Drop全功能跑通手册-复刻功能与AI方案.md`
2. `D:\tx\mini-drop\docs\guides\cloud-lab-deployment.md`
3. `D:\tx\mini-drop\docs\architecture\导师意见专项整改-20260819.md`

用户的目标不是再做一个简化 Demo，而是继续完善一个以组长代码为基底、对照 Drop 复刻指南、带独立循证 AI 诊断方案的工程化系统。

---

## 2. 项目一句话介绍

Mini-Drop 是一个性能诊断平台：用户在 Web 页面描述故障或创建采集任务，控制面把任务下发给目标机器上的 Agent，Agent 使用 perf、eBPF、py-spy、async-profiler、pprof 等工具采集数据，Analyzer 生成火焰图和指标结果，AI 再基于真实证据提出假设、反证并给出可追溯结论。

---

## 3. 当前技术架构

| 层 | 技术与职责 |
|---|---|
| Web | React，统一 AI 诊断入口、任务面板、计划任务、复合任务、审计与设置 |
| API | Go API Server，承担主要对外接口和兼容转发 |
| Server | Python/FastAPI，任务、Agent、AI 诊断、Campaign、测试集接口 |
| Control | C++ 控制面原型，负责调度语义与原生链路展示 |
| Agent | Python Agent + C++ Native Agent，在目标 Linux 主机执行采集 |
| Analyzer | Python，验证产物并生成火焰图、TopN、趋势图和证据元数据 |
| 数据库 | PostgreSQL + SQLAlchemy/Alembic |
| 对象存储 | MinIO，保存原始采集产物和分析结果 |
| 通信 | HTTP/REST、gRPC、SSE |

核心状态机：

```text
PENDING → RUNNING → UPLOADING → ANALYZING → DONE / FAILED
```

每次迁移均落库并记录 `reason`。

---

## 4. 导师重点与当前实现

| 导师关注点 | 当前实现 |
|---|---|
| 循证诊断 | 假设、支持证据、反证、`evidence_refs`、置信度和证据不足门禁 |
| 性能决策树 | 按影响范围及 CPU/内存/I/O/网络/依赖等故障域选择工具 |
| 测试集 | 仓库内 10 类 Golden；外部 `ai_ops_v2` 有 30 个场景、90 次完成运行；历史 `official-90` 有 90 个唯一记录；OTel live subset 计划 18 次真实 Orchestrator 诊断，尚未云端执行 |
| 过程透明 | 页面展示范围确认、生成假设、决策树取证、证据裁决、结论验证 |
| 先造故障再评测 | 白名单真实故障 Campaign，采集基线/故障/恢复三段快照 |
| Oracle 隔离 | 诊断期间不可见，诊断结束后才用于评分 |
| 多轮诊断 | 证据不足、可信反证和人工纠正可以开启下一轮 |
| 多 Agent | 云端当前有 control Agent 和两个 worker Agent |
| 安全边界 | 工具白名单、参数校验、预算、审批；AI 不直接执行高风险修复 |
| 页面简化 | Drop Insight 与集群诊断已合并成一个“AI 诊断”页面，提供简单/专家模式 |

---

## 5. 最近完成的关键修复

### 5.1 AI 范围确认

涉及文件：

- `D:\tx\mini-drop\server\app\drop_insight\service.py`
- `D:\tx\mini-drop\web\src\components\ScopeCard.jsx`
- `D:\tx\mini-drop\web\src\components\ChatThread.jsx`
- `D:\tx\mini-drop\tests\test_drop_insight_v2.py`

当前行为：

- 模糊问题必须确认服务、环境、Agent、PID、开始时间和结束时间。
- 自动加载在线 Agent 和候选进程。
- 默认时间窗为最近 5 分钟。
- 支持“使用在线演示目标”。
- 范围不完整时保持 `NEEDS_CLARIFICATION`。
- 五项范围补齐后进入 `UNDERSTANDING`，不会猜测 Agent/PID。

### 5.2 方法与测试集

涉及文件：

- `D:\tx\mini-drop\server\app\diagnosis\external_benchmark.py`
- `D:\tx\mini-drop\web\src\components\EvalPanel.jsx`
- `D:\tx\mini-drop\web\src\components\CampaignPanel.jsx`
- `D:\tx\mini-drop\tests\test_external_benchmark.py`
- `D:\tx\mini-drop\benchmarks\external\ai_ops_v2-2.0.0-candidate.1-20260811.zip`

最近修复：

- 外部 ZIP 使用中文目录名，后端已改为按结构和文件后缀定位内容。
- 测试集已移动到 `benchmarks/external` 并随 Server 镜像构建。
- 云端不再依赖无权限的 `/workspace-source/artifacts/...` 目录。
- 无法读取可选目录时返回可解释状态，不再抛出 500。
- 页面显示测试集版本、SHA-256、案例、运行次数、逐次审计轨迹和 Oracle 对比。

云端最新验证：

```text
HTTP 200
版本：2.0.0-candidate.1
场景数：30
完成运行：90
```

### 5.3 真实故障 Campaign

云端最新实测运行：

```text
campaign_20260819_095524_79b48f
状态：COMPLETED
基线 CPU：约 0.30%
故障 CPU：约 96.33%
恢复 CPU：约 0.29%
关联 Task：DONE
TaskAttempt：SUCCEEDED
Analyzer Job：SUCCEEDED
产物完整性：VERIFIED
根因：SELF_CODE_CPU_HOTSPOT
置信度：0.91
Oracle：匹配
恢复清理：成功
```

Campaign 已验证的完整流程：

```text
安全预检
→ 基线快照
→ 白名单故障注入
→ 异常确认
→ Mini-Drop 任务取证
→ 循证诊断
→ Oracle 对比
→ finally 清理
→ 恢复验证
```

以上是 2026-08-19 的旧 `LIVE-CPU-001` 云端验收，不是新 OTel live subset 的运行证明。新的 `T1-CPU-001`/`T1-MEM-001` 子集执行器、18-ID 完整性、6-manifest 门禁和 Oracle 隔离已完成代码及聚焦回归，但尚未在云端生成正式报告。

### 5.4 云端错误修复

- `python-hotspot` DNS/健康检查现已恢复，Server 内访问 `/health` 返回 200。
- Campaign 不再直接向用户暴露冗长的 `ConnectionPool/NameResolutionError`。
- 云端测试集接口权限问题已修复。
- 云端范围确认接口已验证能从 `NEEDS_CLARIFICATION` 进入 `UNDERSTANDING`。

---

## 6. 当前云端状态

云端拓扑：

| 节点 | 主要组件 |
|---|---|
| control | Web、Go API、Python Server、Diagnosis Worker、Analyzer、PostgreSQL、MinIO、Campaign Agent、演示目标 |
| worker1 | 远程 Agent、perf、eBPF、py-spy、async-profiler |
| worker2 | 远程 Agent、perf、eBPF、py-spy、async-profiler |

当前在线 Agent：

- `control-campaign-agent`
- `li-mingyuan-worker-1`
- `li-mingyuan-worker-2`

云端认证说明：

- 浏览器右上角填写的是 **Mini-Drop API Key**。
- DeepSeek Key 只配置在 Server 环境文件中，不应填写到页面。
- 所有已经出现在聊天或截图中的密钥都应轮换，文档中不要记录明文。

---

## 7. 本机运行方法

在 Windows 的 VS Code PowerShell 终端执行：

```powershell
cd D:\tx\mini-drop

docker compose config --quiet
docker compose --profile demo-targets up -d --build
docker compose ps
```

浏览器打开：

```text
http://localhost
```

查看日志：

```powershell
docker compose logs -f --tail=100 web apiserver server control-plane native-agent analyzer diagnosis-worker
```

停止：

```powershell
docker compose down
```

Windows Docker Desktop 适合页面、API 和普通指标联调；perf/eBPF 最终验收优先使用云端原生 Linux。

---

## 8. 云端运行方法

凭据来源：

```text
D:\洛伦兹力不做功\Desktop\cloud-lab-environment-guide(1).md
```

该文件只作为登录资料读取，不要把里面的密码复制到代码、回复或 Git。

### 8.1 control 节点

```bash
ssh root@47.112.10.137
cd /opt/mini-drop-li-mingyuan

docker compose \
  --env-file deploy/env/cloud-control.env \
  -f docker-compose.cloud-control.yml \
  up -d

docker compose \
  --env-file deploy/env/cloud-control.env \
  -f docker-compose.cloud-control.yml \
  ps
```

查看日志：

```bash
docker compose --env-file deploy/env/cloud-control.env \
  -f docker-compose.cloud-control.yml \
  logs -f --tail=100 web apiserver server diagnosis-worker analyzer campaign-agent
```

### 8.2 worker 节点

在 worker1、worker2 分别执行：

```bash
cd /opt/mini-drop-li-mingyuan

docker compose \
  --env-file deploy/env/cloud-worker.env \
  -f docker-compose.worker.yml \
  up -d

docker compose \
  --env-file deploy/env/cloud-worker.env \
  -f docker-compose.worker.yml \
  ps
```

不要停止其他同学的容器、网络和数据卷。

---

## 9. 必须执行的回归命令

### Python

```powershell
cd D:\tx\mini-drop
python -m pytest tests/test_external_benchmark.py tests/test_drop_insight_v2.py tests/test_diagnosis_campaign.py -q
```

最近结果：`19 passed`。

### Go API

```powershell
cd D:\tx\mini-drop\apiserver
go test ./...
```

最近结果：全部通过。

### Web

```powershell
cd D:\tx\mini-drop\web
npm test -- --run
npm run build
```

最近结果：9 个测试文件、22 项测试通过，生产构建成功。

不要把 `web/dist`、`__pycache__`、`.pytest_cache` 等生成目录提交到仓库。

---

## 10. 新会话优先检查清单

1. 运行 `git status --short`，理解现有改动，不执行 `git reset --hard`。
2. 运行上面的 Python、Go、Web 回归。
3. 打开云端页面并按 `Ctrl + F5` 强制刷新。
4. 右上角保存 Mini-Drop API Key，确认 SSE 显示已连接。
5. 新建 AI 会话，输入模糊问题，确认页面展示完整范围表单。
6. 使用在线 Agent 和目标 PID，确认后验证状态进入 `UNDERSTANDING`。
7. 打开“方法与测试集”，确认外部测试集显示 30 个场景和 90 次运行。
8. 运行 `LIVE-CPU-001` Campaign，确认最后为 `COMPLETED` 且恢复成功。
9. 验证任务面板的状态机、产物完整性和 Analyzer 输出。
10. 修改代码后同时更新主跑通手册和云部署记录。

---

## 11. 当前真实边界

以下不应伪装成已完成：

- 尚未完成正式生产级多租户与细粒度 RBAC。
- 尚未完成长期多副本压力与故障恢复验收。
- Windows Docker Desktop 下的 perf/eBPF 结果不等同于原生 Linux。
- 远程 Worker 只能采集自己宿主机上的 PID，不能拿 control 的 PID 给 worker Agent。
- 云端证书仍是实验环境自签名证书。
- 大规模真实业务流量、长周期持续 Profiling 与生产容量评估仍属于后续生产化工作。

---

## 12. 清理记录

已经删除或清理：

- 三份内容重复且过时的导师讲稿/旧演示文档。
- 旧版 UI walkthrough 文档及已经失效的旧页面截图工作区文件。
- 本地 Python `__pycache__`、`.pytest_cache`。
- `web/dist` 和 Vite 临时缓存。
- 外部测试集原来的 `artifacts/benchmarks/external` 空目录。

外部测试集的新规范位置：

```text
D:\tx\mini-drop\benchmarks\external\ai_ops_v2-2.0.0-candidate.1-20260811.zip
```

不要为了“清爽”删除以下内容：

- `golden_scenarios`
- `benchmarks`
- `reports/benchmark/official-90`
- `reports/benchmark/live-subset`（当前为预期独立输出；正式运行前可只有状态 README，不得伪造 18-run 结果）
- `native`
- `apiserver`
- `server/migrations`
- `docs/contracts`
- `external/opentelemetry-demo`（它是开源实验参考，不是运行时主链路）

---

## 13. 新会话可直接使用的任务描述

```text
继续处理 D:\tx\mini-drop。先阅读：
1. D:\tx\mini-drop\docs\Mini-Drop新会话交接文档.md
2. D:\tx\mini-drop\docs\guides\Mini-Drop全功能跑通手册-复刻功能与AI方案.md
3. D:\tx\mini-drop\docs\guides\cloud-lab-deployment.md

不要重置仓库，不要重新初始化，不要覆盖现有有效改动。先运行 git status 和核心回归，再从当前云端/本地状态继续。重点保持：循证诊断、性能决策树、统一测试集、过程透明、Oracle 隔离、白名单真实故障 Campaign、多轮反证和多 Agent。任何结论必须附 evidence_refs；证据不足时返回明确门禁，不编造根因。修改后必须更新测试和主跑通手册。
```

---

## 14. 当前仓库现场：新会话先保护再修改

### 14.1 Git 状态

- 当前分支：`master`。
- 最近已提交历史停留在 2026-07-27，但工作区中包含大量 7 月底至 8 月形成的有效修改、新文件、迁移、测试与报告。
- `git status` 中的 `M/A/D/R/??` 不能简单理解为垃圾文件；它们大多属于多语言重构与 AI 方案演进。

接手后的第一组命令：

```powershell
cd D:\tx\mini-drop
git status --short
git branch --show-current
git log -8 --oneline
```

不要直接执行 `git reset --hard`、`git clean -fdx`、`git checkout -- .`、`docker compose down -v`，也不要递归删除整个项目、数据库目录或数据卷。

### 14.2 当前改动覆盖面

当前工作区同时涉及 React AI 工作台、Go API、Python AI 控制面、C++ Control/Agent、Python Agent/Analyzer、Alembic 迁移、Compose 部署、Golden/外部测试集、90 次评测和导师意见整改。新会话必须基于现状继续，而不是再建一套平行实现。

---

## 15. 仓库地图

| 路径 | 作用 | 注意事项 |
|---|---|---|
| `web/` | React 前端 | AI 入口已合并，不要重新拆成两个重复入口 |
| `apiserver/` | Go API Server | 鉴权、对外接口、转发、Repository、计划任务 |
| `server/` | Python/FastAPI 控制面 | AI 编排、任务、Agent、证据、评测、Campaign、数据库 |
| `native/control/` | C++ 控制面 | 原生任务下发与状态语义 |
| `native/agent/` | C++ Agent | perf、eBPF、proc、语言级采集与上传 |
| `agent/` | Python Agent | 兼容采集链路和结果 outbox |
| `analyzer/` | Python Analyzer | 产物校验、火焰图、TopN、趋势、证据 |
| `proto/` | Protobuf/gRPC | 跨语言通信契约 |
| `server/migrations/` | Alembic | ORM 变化必须配迁移 |
| `benchmarks/` | 统一测试集 | Golden、10 类场景和外部 ai_ops_v2 |
| `golden_scenarios/` | Oracle 标准答案 | 诊断结束后才能用于评分 |
| `reports/benchmark/` | 评测结果 | 含正式 90 次运行记录 |
| `demo/` | 演示目标 | C++、Python、Go、Java、下游和网络代理 |
| `deploy/` | 镜像/Nginx/env 模板 | 密钥只进不提交的环境文件 |
| `scripts/` | 验收与实验 | Campaign、OTel、备份、eBPF、压测 |
| `tests/` | Python 测试 | AI、任务、迁移、采集器、Campaign 等 |
| `docs/` | 有效文档 | `docs/README.md` 是索引 |
| `external/` | 开源实验参考 | 不是主运行链路 |

---

## 16. 两条核心链路

### 16.1 基础性能采集

```text
用户选择 Agent/PID/采集器/时长/频率
→ React 调用 Go API
→ API 校验 Mini-Drop API Key、参数和幂等键
→ PostgreSQL 创建 PENDING 任务并保存 reason
→ Control/Server 将任务交给同宿主机 Agent
→ Agent 执行 perf/eBPF/py-spy/async-profiler/pprof/proc
→ raw 产物上传 MinIO，元数据落库
→ Analyzer 校验完整性并生成 flamegraph/TopN/trend/evidence
→ 状态 DONE 或 FAILED；失败必须写清 reason
→ Web 通过 SSE/轮询展示时间线、产物、图表和错误
```

### 16.2 AI 循证诊断

```text
自然语言问题
→ 理解现象但不立即猜根因
→ 确认 service/environment/agent/pid/time_range
→ 生成多个可证伪假设
→ 决策树选择低风险、高信息增益采集器
→ 白名单、预算、权限、参数校验和必要审批
→ 调用真实基础采集链路
→ 产物完整性与证据质量门禁
→ 归一化成 evidence_refs
→ 支持证据和反证裁决
→ 证据充分：根因 + 置信度 + 限制 + 建议 + 复验
→ 证据不足：INSUFFICIENT_EVIDENCE + 下一轮取证
→ 人工反馈开启新一轮，不覆盖旧证据
→ 修复后以同范围、同负载、同指标做 before/after
```

这里的 AI 不是文本客服：它需要结构化范围、受控 Tool Calling、真实证据引用、反证、质量门禁和可重复验证。诊断冻结后才允许 Oracle 评分。

---

## 17. 核心数据对象

| 对象 | 人话解释 | 关键内容 |
|---|---|---|
| Agent | 目标主机上的采集执行进程 | ID、能力、心跳、在线状态 |
| Task | 一次采集请求 | PID、采集器、时长、频率、status、reason |
| TaskAttempt | Task 的一次真实执行 | 重试、Agent、时间、执行结果 |
| Artifact | 采集/分析文件 | 类型、大小、哈希、完整性 |
| Analyzer Job | 一次 raw 分析 | PENDING/RUNNING/SUCCEEDED/FAILED |
| Diagnosis Session | 一次 AI 会话 | 范围、假设、工具、证据、报告 |
| Hypothesis | 可证伪根因候选 | 判断条件、支持证据、反证、状态 |
| Evidence | 通过质量校验的数据 | 来源、时间窗、质量、evidence_refs |
| Campaign | 真实故障闭环 | 基线、注入、取证、诊断、Oracle、恢复 |
| Oracle | 标准答案 | 只在评测阶段读取 |
| Schedule | 周期任务 | 周期、目标、模板、启停 |
| Composite Task | 多采集器编排 | 子任务、依赖、聚合结果 |

---

## 18. 采集器速查

| 页面名称 | 底层能力 | 适合定位 | 主要条件 |
|---|---|---|---|
| CPU 火焰图 | perf | CPU 热点、锁竞争调用路径 | 原生 Linux、perf 权限和符号 |
| Python 火焰图 | py-spy | Python 函数热点 | Python PID、ptrace 权限 |
| 持续火焰图 | 周期 perf/切片 | 热点随时间变化 | 常驻 Agent 和时间窗存储 |
| Java 火焰图 | async-profiler | CPU/alloc/lock | Java PID 和正确 event |
| Go pprof | `/debug/pprof` | CPU/heap/goroutine | 目标开放正确 pprof 地址 |
| I/O 延迟 | eBPF/bpftrace | I/O 延迟和分布 | 原生 Linux、tracefs、BPF/PERFMON |
| 内存趋势 | `/proc/<pid>/smaps` | RSS/PSS/Swap 增长 | Agent 可访问目标 `/proc` |
| 系统指标 | `/proc` 和系统统计 | CPU/负载/线程/FD/网络 | 普通 Linux 权限 |

PID 属于具体主机，必须交给同宿主机 Agent；worker Agent 看不到 control 主机上的 PID。

---

## 19. 端口和两类密钥

| 服务 | 默认入口 | 说明 |
|---|---|---|
| Web/Nginx | 宿主机 80 | 本地 `http://localhost` |
| Go API | 容器内 8080 | 由 Nginx 代理 |
| Python Server | 容器内 8191 | `/api/healthz` |
| gRPC Control | 50051 | Agent 任务和心跳 |
| MinIO | 9000 | 原始/分析产物 |
| Go demo pprof | 6060 | demo profile 下启用 |
| Python demo | 8081 | python-hotspot |
| downstream demo | 8082 | 下游异常 |
| network proxy | 8083 | 网络异常 |
| Java demo | 7070 | Java 目标 |

- `MINI_DROP_API_KEY`：页面右上角填写，用于访问 Mini-Drop API。
- `MINI_DROP_AI_API_KEY`：只放 Server 私有环境，用于调用模型。

页面右上角不是填写 DeepSeek Key。任何密钥都不要进入 Git、截图、回复或交接文档；已公开的密钥应轮换。

---

## 20. UI 页面说明

### AI 诊断

- **诊断会话**：创建问题、确认范围、看假设、工具、证据、结论。
- **诊断历史**：查看或删除历史会话。
- **方法与测试集**：查看循证方法、决策树、测试集、逐次运行、Campaign 和评分。
- 六步过程：理解问题 → 确认范围 → 生成假设 → 决策树取证 → 证据裁决 → 结论验证。

### 任务面板

- 快速可视化：手工选择采集器、Agent、PID、时长。
- 自然语言：把采集意图转为结构化参数。
- 最近任务：查看状态、reason 和结果。
- Agent 候选：只显示支持当前采集器的在线 Agent。

### 计划任务与复合任务

计划任务用于周期采集；复合任务编排多个采集器并保存子任务依赖和聚合结果。审计日志记录操作、上下线、状态迁移和 AI 工具调用；系统设置展示能力与参数，敏感值必须掩码。

---

## 21. 本地启动与停止

启动 Docker Desktop，等待 Engine running。在 VS Code PowerShell 输入：

```powershell
cd D:\tx\mini-drop
docker version
docker compose version
docker compose config --quiet
docker compose --profile demo-targets up -d --build
docker compose ps
```

打开 `http://localhost`，填写本地 `.env` 中对应的 Mini-Drop API Key并保存，确认 SSE 已连接。

日志：

```powershell
docker compose logs -f --tail=100 server apiserver control-plane native-agent analyzer diagnosis-worker web
```

停止但保留数据：

```powershell
docker compose down
```

不要随意加 `-v`。

---

## 22. 验收顺序

1. Agent 心跳、上下线与审计。
2. 系统指标和内存趋势。
3. 验证 `PENDING → RUNNING → UPLOADING → ANALYZING → DONE`。
4. 检查 raw、Analyzer 结果、图表、哈希和完整性。
5. perf/eBPF/语言采集在云端原生 Linux 做最终验收。
6. AI 输入模糊问题时必须先 `NEEDS_CLARIFICATION`。
7. 补齐 service/environment/Agent/PID/开始/结束时间后才推进。
8. 工具卡展示工具、参数、风险、审批、任务 ID、状态和 reason。
9. 结论含 evidence_refs、支持证据、反证、置信度、限制和下一步。
10. 修复后用同范围、同负载、同指标复测。

---

## 23. 测试集、Oracle 与 Campaign

测试资产有四层：

1. `golden_scenarios/`：确定性回归。
2. `benchmarks/cases/`：代码、CPU、下游、GC、I/O、负载、内存、网络、噪声邻居、队列共 10 类。
3. `benchmarks/external/ai_ops_v2-2.0.0-candidate.1-20260811.zip`：30 场景、3 策略、90 次展示与审计数据。
4. `reports/benchmark/official-90` 是历史 10 案例/90 记录 Campaign；新的 `scripts/run_live_subset.py` 独立运行 CPU/MEM 两案例、18 次真实 Orchestrator 诊断，不修改历史结果。

页面必须展示版本、SHA-256、来源、策略、每次运行 ID、时间、状态和诊断结束后的 Oracle 对比。若是 100%，必须能下钻解释，不能写死。

Oracle 在诊断阶段不可见，只有诊断冻结后 Evaluator 才能读取。

真实故障 Campaign：基线 → 白名单注入 → 异常确认 → 真实任务取证 → 循证诊断 → Oracle 对比 → finally 清理 → 恢复验证。禁止 Web 任意 shell，每个故障必须有超时、清理和恢复检查。

---

## 24. 云端三节点

- control：Web、API、Server、PostgreSQL、MinIO、Analyzer、Diagnosis Worker、Campaign Agent 和部分 demo。
- worker1/worker2：各自运行远程 Agent，只采本机进程。
- 多 Agent 是多个采集节点，不是多个大模型。

---

## 25. 常见故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 认证失败 | Mini-Drop API Key 未填/不一致 | 检查右上角和服务环境，保存后刷新 |
| SSE 断开 | API、认证或 Nginx 问题 | 查浏览器 Network 和服务日志 |
| Agent 离线 | 心跳、网络、重启、时钟 | 查 Agent 日志、心跳、数据库时间 |
| Agent 下拉为空 | 无在线 Agent 或能力不匹配 | 查 capabilities |
| PID 不存在 | 属于别的主机或进程退出 | 使用同 Agent 的候选进程 |
| perf 图太浅 | 目标负载低、符号缺失、时长短、权限 | 换热点目标、延长、查符号/权限 |
| Analyzer RUNTIMEERROR | raw 格式不符 | 下载 raw、查日志、重放分析 |
| Java event 不支持 | 使用错误 event | 改用 cpu/wall/alloc/lock |
| Go pprof 6060 拒绝 | 未监听或地址错误 | 检查目标监听和容器网络 |
| eBPF tracefs 不可用 | Docker Desktop/权限不足 | 转原生 Linux并配置 BPF 权限 |
| python-hotspot DNS 失败 | demo 未启动或网络错误 | 启动 demo profile、查服务网络 |
| no completed TaskAttempt | Task 尚未完整成功 | 查 Task/Attempt/Analyzer/Artifact |
| 一直需确认范围 | scope 不完整或 payload 错 | 检查六项范围和接口响应 |
| 一直证据不足 | 未导入证据或质量门禁失败 | 看证据卡并开启下一轮 |

---

## 26. 2026-08-20 最新增量：真实开源缺陷页面化验证

### 26.1 用户最新要求

用户明确要求：

1. 验收者不在本机操作 Docker；
2. 可以使用云服务器；
3. 测试必须从 Web 页面发起并在页面展示过程；
4. 不接受只在 AI 沙箱或隐藏 CLI 中跑测试；
5. 测试集应来自真实开源缺陷，并与成熟产品建立公平比较协议；
6. 未实际运行的结果不得显示为通过或 100%。

云端服务底层仍可使用现有容器编排，但这属于运维实现，验收者只通过浏览器操作。

### 26.2 新增真实业务候选集

目录：

```text
benchmarks/real_world/
├── manifest.json
├── comparators.json
├── public/cases.json
├── private/oracles.json
└── examples/normalized-result-template.json
```

目前收录 7 个真实开源 PR：

| Case | 来源 | 当前页面能力 |
|---|---|---|
| RW-GRAFANA-123359 | Grafana PR #123359 | 云端机制级复现 |
| RW-OTELPY-4224 | OpenTelemetry Python PR #4224 | 云端机制级复现 |
| RW-PROM-19412 | Prometheus PR #19412 | 题面/Oracle，未接执行器 |
| RW-REDIS-15427 | Redis PR #15427 | 题面/Oracle，未接执行器 |
| RW-K8S-138571 | Kubernetes PR #138571 | 云端机制级复现 |
| RW-K8S-140886 | Kubernetes PR #140886 | Abstention 题，未接执行器 |
| RW-ENVOY-42752 | Envoy PR #42752 | 云端机制级复现 |

三个必须分开的数字：

```text
候选案例：7
云端页面可运行的机制级复现：4
完整上游仓库 base/fix A/B：0
```

禁止把 4 个机制级复现描述成“4 个完整开源项目已经复现”。

### 26.3 新增后端入口

实现文件：

- `D:\tx\mini-drop\server\app\diagnosis\real_world_runs.py`
- `D:\tx\mini-drop\server\app\main.py`

接口：

```text
GET  /api/v1/real-world-benchmarks/catalog
POST /api/v1/real-world-benchmarks/runs
GET  /api/v1/real-world-benchmarks/runs/{run_id}
```

可执行案例按以下阶段运行：

```text
PREFLIGHT
→ BASELINE
→ INCIDENT
→ DIAGNOSIS
→ VERIFICATION
→ ORACLE_COMPARISON
→ COMPLETED / FAILED / INTERRUPTED
```

执行状态与评分资格分开记录：`COMPLETED` 只表示 worker 正常结束；当前 `MECHANISM_REPRO` 固定为 `scoring_status=UNSCORED`、`passed=null`。重启时发现遗留 `RUNNING` 会转为 `INTERRUPTED`，不自动重放非幂等脚本。Oracle 必须在 `DIAGNOSIS` 完成后读取。运行结果必须包含 run ID、阶段时间线、服务端证据对象及引用、反证引用、置信度和局限。

### 26.4 新增前端入口

实现文件：

- `D:\tx\mini-drop\web\src\components\RealWorldBenchmarkPanel.jsx`
- `D:\tx\mini-drop\web\src\components\EvalPanel.jsx`
- `D:\tx\mini-drop\web\src\api\client.js`

页面位置：

```text
AI 诊断 → 方法与测试集 → 真实开源缺陷：页面化云端复现与成熟产品对照
```

页面职责：

- 展示 7 个案例、真实 PR 来源和复现级别；
- 只允许 4 个有执行适配器的案例点击“在云端运行”；
- 展示运行进度、服务端时间线和 baseline/incident/verification；
- 诊断冻结后展示 Oracle 对比；
- 展示 RCAEval、OpenRCA、HolmesGPT、Pyroscope 的比较维度；
- 成熟产品未同题运行前不显示虚构分数。

### 26.5 四个页面可运行机制

| Case | baseline | incident | verification | 预期根因 |
|---|---:|---:|---:|---|
| Grafana queue dedup | 10 个稳定键 | 600 个队列项 | 10 | `workqueue_pointer_identity_breaks_deduplication` |
| OTel Python weakref | 0 个存活对象 | 600 | 0 | `strong_callback_references_retain_reader_exporter` |
| K8s full sync | 150 次操作 | 9000 | 150 | `periodic_full_sync_cost_in_large_cluster_mode` |
| Envoy debug eval | 0 次求值 | 4000 | 0 | `per_chunk_debug_log_expression_evaluation` |

这些数字用于验证机制和反事实恢复，不等同于上游完整项目的性能 Benchmark。

---

## 27. 最新验证记录

### 27.1 自动测试

执行：

```powershell
cd D:\tx\mini-drop
python -m pytest -q tests/test_real_world_benchmark.py tests/test_real_world_runs.py
```

最近确认结果（本地机械回归，不替代云端验收）：

```text
Python 全量：883 passed, 116 warnings
Go 全量：全部通过
Web Vitest：11 个测试文件、29 个测试全部通过
Web 生产构建：4624 modules transformed，构建成功
RealWorld admission/hash 聚焦：58 passed, 1 warning
Evaluator/outbox/API 聚焦：104 passed, 116 warnings
```

warning 主要来自 FastAPI、`pytest_asyncio` 的弃用提示，不是业务失败。评分器只认可当前 run/case 所属且哈希有效的结构化证据；任意字符串引用和自报快照角色不再得分，`UNSCORED` 不进入正式分母。`git diff --check` 已无 whitespace error；Windows 工作区仍提示部分文件下次由 Git 触碰时会从 LF 转为 CRLF。

### 27.2 前端

最近确认：Web Vitest 11 个测试文件、29 个测试全部通过；Vite 生产构建成功，共转换 4624 个模块。这些结果仅是本地机械回归。

### 27.3 云端 API 实跑

最近确认结果：

```text
catalog=7
runnable=4
full_upstream_replays=0

RW-GRAFANA-123359  COMPLETED
RW-OTELPY-4224     COMPLETED
RW-K8S-138571      COMPLETED
RW-ENVOY-42752     COMPLETED
```

以上 4 条 `COMPLETED` 仅表示机制适配器执行结束，均应解释为 `MECHANISM_REPRO + UNSCORED`，不能表述为正式基准通过；完整上游 base/fix replay 仍为 0。本切片只完成持久化、证据完整性和前端口径的本地机械回归，没有重新执行云端验收，也没有启动正式 OTel 18-run。

2026-08-21 的新鲜安全预检未建立云端可用性：对 `47.112.10.137:443` 的标准证书校验因缺少可信签发链失败；本机未配置 `mini-drop-control` SSH alias，也没有该公网 IP 的已锁定 host-key 条目。仓库内能找到的是 Hyper-V `.10/.11/.12` 实验拓扑、历史验证报告和 Paramiko `RejectPolicy`/known-host lockfile 机制，不能据此推导当前公网三节点已就绪。未使用 `curl -k`、`StrictHostKeyChecking=no` 或自动接受未知主机密钥；因此当前云端三节点 authenticated readiness 与 acceptance 仍未证明。

云端页面运行说明：

`D:\tx\mini-drop\docs\guides\云端页面真实业务测试全流程.md`

真实测试集与产品对照说明：

`D:\tx\mini-drop\docs\benchmarks\真实业务测试集与成熟产品对照.md`

---

## 28. 当前工作区保护清单

当前新增/修改的核心文件尚未统一提交。接手者先执行：

```powershell
cd D:\tx\mini-drop
git status --short
git diff -- README.md docs/benchmarks/sources.md server/app/main.py web/src/api/client.js web/src/components/EvalPanel.jsx
```

本轮有效修改至少包括：

```text
M  README.md
M  docs/benchmarks/sources.md
M  server/app/main.py
M  web/src/api/client.js
M  web/src/components/EvalPanel.jsx
?? benchmarks/real_world/
?? docs/benchmarks/真实业务测试集与成熟产品对照.md
?? docs/guides/云端页面真实业务测试全流程.md
?? reports/benchmark/real-world/
?? scripts/real_world_benchmark.py
?? server/app/diagnosis/real_world_runs.py
?? tests/test_real_world_benchmark.py
?? tests/test_real_world_runs.py
?? web/src/components/RealWorldBenchmarkPanel.jsx
```

还存在 `.tmp-real-world-*.tar`、`.tmp-web-dist.tar`、`.tmp-cloud-verify.py` 等部署临时文件。它们不属于项目成果，提交前应在确认路径后删除，并检查 `.gitignore`；不要把临时包提交到 Git。

仓库中还存在历史 bundle、JVM crash/replay log。是否删除必须先核对用途和 Git 跟踪状态，禁止批量 `git clean -fdx`。

---

## 29. 云端现场与部署边界

### 29.1 当前入口

```text
Web：https://47.112.10.137/
Control 代码：/opt/mini-drop-li-mingyuan
Compose project：mini-drop-cloud-control
```

云端认证、SSH、数据库和模型密钥不写入本文。凭据从用户本地私有环境文件或云环境说明中获取，禁止打印、复制到聊天或提交到 Git。

### 29.2 本轮部署方式

- 云端 Control 资源较小，直接在云端构建 Web 曾超过 12 分钟并造成 SSH 迟缓，因此已中止；
- 前端在本地完成生产构建后，将已验证的 `web/dist` 同步到云端运行容器；
- Server 新模块同步到云端 Server 容器并重启；
- 云端 API 的 4 个案例均完成过机制适配器执行，但属于 `MECHANISM_REPRO + UNSCORED`，不是正式基准通过；
- 云端页面 bundle 已确认包含真实业务测试面板。

这代表当前运行实例已更新，但下一位 AI 仍需保证云端源码目录与运行实例一致，不能只修改容器而不保留可复现部署方式。

### 29.3 不得破坏的云端现场

云端源码中可能存在其他成员的独立修改，例如 demo、OTel 脚本、benchmark adapter、环境文件。同步前先：

1. 只读查看 `git status`；
2. 对目标文件做哈希；
3. 备份将要覆盖的文件；
4. 只同步本任务涉及的文件；
5. 不执行 reset、clean、全目录覆盖或删除数据卷。

---

## 30. 下一位 AI 的优先任务

### P0：把本轮成果整理成可提交状态

1. 删除本轮 `.tmp-*` 部署文件；
2. 复查所有新增 JSON 不含密钥、私有路径和 Oracle 泄漏；
3. 跑 Python、Web 构建和相关回归；
4. 检查 `git diff`，按功能拆分有意义的中文 commit；
5. 提交前由监督者复查，不直接强推。

### P0：保持页面真实运行

1. 浏览器进入“方法与测试集”；
2. 逐一运行 4 个机制级案例；
3. 验证每个 run 有服务端时间线和三段快照；
4. 失败时页面显示失败阶段和原因，禁止自动写成成功；
5. 确认 Oracle 在诊断冻结后才出现。

### P1：完成至少 1 个 FULL_UPSTREAM_REPLAY

建议优先 `RW-OTELPY-4224`，原因是 Python 依赖和资源成本相对低。

完成条件：

1. 固定 upstream base/fix commit；
2. 在独立 runner 拉取代码并校验 commit；
3. 运行相同 harness、相同负载、至少 3 次；
4. 记录环境、命令、耗时、RSS/GC/weakref 等数据；
5. base 稳定出现问题，fix 稳定消失；
6. 生成 baseline/incident/verification；
7. `execution_fidelity` 才能标为 `FULL_UPSTREAM_REPLAY`；
8. 从 Web 页面发起并查看过程，不只提供 CLI 脚本。

### P1：成熟产品公平同题对照

优先顺序：

1. Pyroscope：比较 CPU hotspot 火焰图和时间窗口查询；
2. RCAEval 小样本：比较根因 Top-K；
3. HolmesGPT：比较多轮工具调查和证据引用；
4. OpenRCA：用于研究大遥测工具选择。

比较时必须固定事故窗口、可见遥测、模型、温度、Token、工具调用和时间预算。能力不同的产品只比较重叠部分。

### P1：补充页面证据

- 真实开源来源链接；
- 环境指纹、runner ID、commit SHA；
- 命令摘要但不显示密钥；
- 每阶段原始指标；
- 诊断耗时、工具调用、成本；
- evidence refs 与 counter-evidence refs；
- 限制和不确定性。

### P2：资源允许后扩展

- Prometheus backing array retention；
- Redis SCAN/expiration starvation；
- Kubernetes uncertain revert 的 abstention 测试；
- OTel Demo 的 CPU、内存、GC、队列、下游和网络业务故障；
- 完整多节点长稳与容量测试。

---

## 31. 新 AI 禁止事项

1. 不得把候选 PR 数量当成运行通过数量；
2. 不得把机制级复现冒充完整上游 A/B；
3. 不得因为有 Oracle 就生成预填答案；
4. 不得在诊断前把 `private/oracles.json` 传给模型或页面；
5. 不得伪造成熟产品分数；
6. 不得只在本地/AI 沙箱跑完后声称页面已验收；
7. 不得使用任意 shell 故障注入，必须白名单、超时和 finally 清理；
8. 不得把 DeepSeek Key 填入页面右上角；
9. 不得输出 SSH、数据库、MinIO、Mini-Drop 或模型密钥；
10. 不得覆盖其他成员的云端修改；
11. 不得跳过测试、构建、云端 API 或页面验证；
12. 不得在失败时自动降级成模拟成功。

---

## 32. 交付报告格式

下一位 AI 每完成一个阶段，必须给监督者提交以下内容：

```markdown
## 本轮目标

## 修改文件
- 绝对路径：改了什么、为什么

## 实际执行
- 命令/页面操作
- 运行环境
- run ID / task ID

## 验证结果
- 测试数量与结果
- Web 构建结果
- 云端 API/页面结果
- 截图或日志位置

## 没有完成的部分
- 原因
- 下一步

## 风险
- 数据兼容
- 权限/资源
- Oracle 泄漏
- 回滚方式
```

“代码写完”“理论上可行”“应该通过”均不算完成证据。

---

## 33. 监督协议

用户将让另一位 AI 实施，我作为监督者按以下门禁验收：

### 33.1 提交修改前

另一位 AI 应先提供：目标、文件清单、数据迁移影响、测试计划和回滚方案。没有理解现有状态前不得大规模重构。

### 33.2 代码完成后

监督检查：

1. diff 是否只包含计划范围；
2. 是否保留组长基底和原有复刻功能；
3. 是否新增测试；
4. 是否泄漏 Oracle 或密钥；
5. 是否真实调用基础采集链路；
6. 是否能从 Web 发起并看到过程；
7. 是否诚实标注执行保真度；
8. 是否有失败路径、恢复和审计。

### 33.3 合并门禁

满足以下条件后才建议提交或部署：

- 相关 Python 测试全部通过；
- Web 测试和生产构建通过；
- API 契约无破坏或已有迁移；
- 页面运行至少一个成功案例和一个失败/证据不足案例；
- 云端运行结束后目标恢复；
- 文档与页面状态一致；
- Git 不含临时包、密钥和生成缓存。

---

## 34. 可直接交给下一位 AI 的完整提示词

```text
接手 D:\tx\mini-drop 项目。你的任务是在现有组长基底和多语言复刻链路上继续完善，不是另建简化 Demo。

首先完整阅读：
1. D:\tx\mini-drop\docs\Mini-Drop新会话交接文档.md，尤其第 26～34 节；
2. D:\tx\mini-drop\docs\guides\Mini-Drop全功能跑通手册-复刻功能与AI方案.md；
3. D:\tx\mini-drop\docs\guides\云端页面真实业务测试全流程.md；
4. D:\tx\mini-drop\docs\benchmarks\真实业务测试集与成熟产品对照.md。

先运行 git status、相关回归和前端构建。不得 reset、clean、重新初始化、覆盖其他人的云端修改或删除数据卷。

当前真实状态：7 个真实 PR 候选，4 个云端页面机制级复现，完整上游 base/fix A/B 为 0。必须保留这个区分。测试必须从云端 Web 页面发起并展示服务端 run ID、阶段时间线、baseline/incident/verification、诊断、Oracle 对比与恢复，不能只在 AI 沙箱或隐藏 CLI 中运行。

优先任务：
1. 整理本轮改动、清理临时部署包、跑全量相关测试；
2. 从页面重验 4 个机制级案例；
3. 为 RW-OTELPY-4224 实现第一个完整上游 base/fix A/B runner，并接入页面；
4. 设计 Pyroscope/RCAEval/HolmesGPT 的公平同题对照，未实际运行前不填分数；
5. 所有结论必须含 evidence_refs、反证、置信度、局限和修复后验证；证据不足必须明确 abstain。

每轮修改后按照本文件第 32 节格式汇报，不要只说“已完成”。提交、推送或云端覆盖前先交给监督者复查。
```
| Campaign 预检失败 | 目标健康/DNS/开关错误 | 先独立请求目标 `/health` |
| Docker API 500 | Engine 更新或卡死 | 重启 Docker Desktop，查 `docker version` |

---

## 35. 回归矩阵

核心回归：

```powershell
cd D:\tx\mini-drop
python -m pytest tests/test_external_benchmark.py tests/test_drop_insight_v2.py tests/test_diagnosis_campaign.py -q

cd D:\tx\mini-drop\apiserver
go test ./...

cd D:\tx\mini-drop\web
npm test -- --run
npm run build
```

AI/证据专项：

```powershell
cd D:\tx\mini-drop
python -m pytest tests/test_claim_evidence_verifier.py tests/test_hypothesis_predicate.py -q
python -m pytest tests/test_fix_verification.py tests/test_drop_insight_closed_loop.py -q
python -m pytest tests/test_benchmark_runner.py tests/test_diagnosis_eval.py -q
python -m pytest tests/test_state_machine.py tests/test_artifact_integrity.py -q
python -m pytest tests/test_migrations.py tests/test_sql_repository.py -q
```

原生 perf/eBPF、三节点、多副本、Campaign、备份恢复与持续 Profiling soak 属于环境型验收，不能只靠单测宣布完成。

---

## 36. 工程规则与提交要求

1. 先确定唯一数据源，避免 Python/Go/前端各维护一套枚举。
2. API 变化同步 schema、实现、前端、契约和测试。
3. ORM 变化补 Alembic migration。
4. 状态迁移落库并带 reason。
5. 产物保存类型、大小、哈希和完整性。
6. AI 输出结构化并经服务端验证。
7. 高风险操作审批，故障注入白名单化。
8. UI 展示人话错误，技术堆栈放技术详情。
9. 密钥、密码和私钥永不进 Git。
10. 修改后同步更新测试、主跑通手册和本文件。

提交信息要说明目的，例如 `feat(ai): 增加证据门禁以阻止无依据根因`，不要只写 `fix/update/wip`。

---

## 37. 剩余工作优先级

### P0

- 保持 Python/Go/Web 回归通过。
- 云端认证、SSE、Agent 在线。
- AI 范围确认可用。
- 外部测试集 30/90 可追溯。
- `LIVE-CPU-001` Campaign 可重复完成并恢复。
- 对 OTel live subset 执行云端只读预检，再完成 CPU/MEM smoke、warmup、6 个正式窗口和 18-run 报告门禁。

### P1

- 将大量工作区改动按功能拆分成可解释提交。
- 统一 TaskKind、采集能力和状态枚举的单一来源。
- 拆分 AI orchestrator 的 planner/executor/verifier/reporter。
- 按兼容路径下线台账收敛重复 API。
- 统一 AI 会话模型，减少历史兼容分支。

### P2

- 多租户、RBAC、审计保留。
- 多副本并发、容量和故障恢复压测。
- 长周期持续 Profiling。
- 正式证书、密钥管理、监控和备份恢复。
- 更大真实业务测试集与专家标注。

---

## 38. 完成定义与新会话首轮行动

功能只有在代码、测试、契约、UI 成功/失败路径、日志审计、操作文档、真实环境验证和安全门禁都具备时才算完成；只有按钮或 HTTP 200 不算。

新会话建议依次执行：

```text
1. 阅读本文件第 14、25、26、30、35、37 节。
2. git status --short，保护当前现场。
3. 跑 Python 核心、Go 全量、Web 测试和构建。
4. docker compose config --quiet。
5. 容器已运行时先 compose ps，不重复重建。
6. 验证 API Key、SSE、Agent。
7. 跑一次系统指标基础任务。
8. 跑一次 AI 范围确认。
9. 查看 30 场景/90 次评测。
10. 对 OTel checkout、Compose、节点资源、端口、Agent 能力和活动任务做云端只读预检。
11. 经明确确认后，先跑 CPU/MEM 丢弃式 smoke 和 warmup，再执行 `scripts/run_live_subset.py`；核对 `PUBLISHED/FIXTURE_FAILED/SKIPPED`、18 个唯一 ID、6 个 manifest、空 cleanup errors 和 `complete=true`。
12. 按真实失败修改，不做无依据大重构。
13. 更新测试、主手册和交接文档。
```
