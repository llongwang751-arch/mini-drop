# Mini-Drop 全功能验收方案

> - 对照文档：[Drop 复刻指南](../architecture/replication-guide.md)
> - 详细操作：[全功能跑通手册](Mini-Drop全功能跑通手册-复刻功能与AI方案.md)
> - 适用工作区：`D:\tx\mini-drop`
> 更新时间：2026-08-10

## 1. 验收结论

当前版本的核心复刻链路、统一 AI 循证诊断、10 类真实故障 Campaign 和 90 次策略评测已经完成。当前 Docker Desktop/WSL2 环境可判定为 **通过（带原生 Linux 验收条件）**。

| 验收层级 | 当前结论 | 说明 |
|---|---|---|
| 代码与自动测试 | 通过 | Python 626 项、React 22 项和 Go 全量测试通过，Web 生产构建通过 |
| Docker 环境端到端 | 通过 | 核心服务健康，8 类采集任务及 10 类故障 Campaign 已完成联调 |
| AI 正式策略评测 | 通过 | 90/90 个唯一执行 ID，完整性门禁 PASS |
| 原生 Ubuntu 真机 | 待最终签字 | 仍需在原生 Ubuntu 留存 perf/eBPF 和跨语言采集证据 |
| 生产高可用与运维 | 不在本阶段完成范围 | Kubernetes、企业 IAM、多副本压测等列入后续生产化 |

不得把“Docker 环境通过”描述成“原生 Linux 内核验收通过”，也不得把“90 次已执行”描述成“90 次全部成功”。

## 2. 验收原则

验收必须区分三类证据：

1. **代码存在**：只能证明功能已实现。
2. **自动测试通过**：证明约束和回归行为稳定。
3. **真实运行通过**：必须能找到 Task、TaskAttempt、Artifact、Analyzer Job、状态事件和页面结果。

AI 结论还必须满足：

- 结论引用真实 `evidence_refs`；
- 缺少证据时返回 `INSUFFICIENT_EVIDENCE`；
- 被反证的假设不能进入最终结论；
- R2 操作必须人工批准，R3 不由 AI 自动执行；
- 缺失或完整性校验失败的产物不计为证据。

## 3. 验收环境

### 3.1 A 级：Windows + Docker Desktop/WSL2

用于验证 Web、API、数据库、对象存储、Agent、Analyzer、AI、统一测试集和异常路径。

### 3.2 B 级：原生 Ubuntu 22.04/24.04

用于最终验证：

- `perf_event_paranoid`、BPF capability、tracefs/debugfs；
- perf 真实调用栈；
- eBPF I/O 或调度抖动前后的分布变化；
- C++、Python、Java、Go 真实目标进程；
- PID namespace 映射和最小权限运行。

### 3.3 C 级：多节点环境

用于验证 Control 与多个 Worker、离线恢复、跨主机证据、同机噪声邻居、TLS 和 Token。部署说明见 [多节点部署](multi-node-deployment.md)。

## 4. 启动与健康检查

在 **Windows PowerShell 或 VS Code PowerShell 终端**执行：

```powershell
cd D:\tx\mini-drop
docker compose up -d --build
docker compose up -d python-hotspot java-hotspot downstream-service network-proxy
docker compose ps
```

打开：

- Web：<http://localhost>
- 健康接口：<http://localhost/api/healthz>

接口检查：

```powershell
curl.exe -s http://localhost/api/healthz
curl.exe -s http://localhost/api/agents
curl.exe -s http://localhost/api/tasks
curl.exe -s http://localhost/api/analysis-jobs
curl.exe -s http://localhost/api/metrics
```

通过标准：

- 核心容器为 `running`，带健康检查的服务为 `healthy`；
- `/api/healthz` 返回健康；
- 至少一个 Agent 为 `ONLINE`；
- Agent capability 列表包含它实际支持的采集器；
- 页面左下角显示实时连接正常。

## 5. 自动化回归验收

### 5.1 Python

```powershell
cd D:\tx\mini-drop
python -m pytest -q
```

当前基线：`626 passed`。

### 5.2 React

```powershell
cd D:\tx\mini-drop\web
npm run test -- --run
npm run build
```

当前基线：`9` 个测试文件、`22 passed`，生产构建成功。

### 5.3 Go

本机未安装 Go 时使用容器：

```powershell
docker run --rm `
  -v "D:\tx\mini-drop\apiserver:/src" `
  -w /src golang:1.23 go test ./...
```

当前 `internal/config`、`internal/cron`、`internal/httpapi`、`internal/repository` 均通过。

## 6. 基础复刻链路验收

### 6.1 Agent 心跳、离线与恢复

1. 打开“任务面板”，确认 Agent 在线。
2. 点击 Agent ID，查看主机信息、能力和最后心跳。
3. 执行：

```powershell
docker compose stop native-agent
Start-Sleep -Seconds 35
docker compose start native-agent
Start-Sleep -Seconds 10
```

4. 打开“审计日志”。

通过标准：Agent 先变为 `OFFLINE`，重启后恢复 `ONLINE`，离线和恢复均有审计记录。

### 6.2 任务状态机与分析闭环

在“任务面板”选择“系统指标”，选择在线 Agent，填写属于该 Agent 的 PID，创建任务。

通过标准：

```text
PENDING → RUNNING → UPLOADING → ANALYZING → DONE
```

任务详情必须能看到：

- 每次迁移的时间、actor 和 reason；
- 至少一个 TaskAttempt；
- 原始 Artifact；
- `SUCCEEDED` Analyzer Job；
- 经过 SHA-256 校验的分析产物；
- 页面上的系统指标结果。

### 6.3 八类采集器

| 采集器 | 目标 | 预期结果 | 当前 Docker 状态 |
|---|---|---|---|
| CPU/perf | C++ 热点进程 | 交互火焰图、TopN | 已联调 |
| Python/py-spy | `python-hotspot` | Python 调用栈火焰图 | 已联调 |
| Continuous Profiling | 持续运行目标 | 时间切片、窗口回看、停止续建 | 已联调 |
| Java/async-profiler | `java-hotspot` | Java HTML/调用栈结果 | 已联调 |
| Go pprof | 开启 pprof 的 Go 服务 | pprof 产物与热点 | 已联调 |
| eBPF I/O | I/O 压力目标 | I/O 延迟分布与原始事件 | WSL2 已联调，仍需 Ubuntu 签字 |
| 内存趋势 | 任意 Linux 进程 | RSS/PSS/Swap 趋势 | 已联调 |
| 系统指标 | Agent 主机/进程 | CPU、负载、线程、FD、网络 | 已联调 |

逐项 PID、点击步骤和预期页面见[全功能跑通手册](Mini-Drop全功能跑通手册-复刻功能与AI方案.md)。

### 6.4 Continuous Profiling

1. 创建“持续火焰图”任务。
2. 等待至少两个切片。
3. 用时间窗口查询切片。
4. 打开任意切片。
5. 点击停止。
6. 再等待一个切片周期。

通过标准：窗口过滤正确；停止后保留已有切片但不再新增；停止一个任务不会删除其他持续任务。

### 6.5 Analyzer 失败与恢复

1. 创建采集任务。
2. 在任务进入分析前执行 `docker compose stop analyzer`。
3. 确认任务与 AnalysisJob 没有消失。
4. 执行 `docker compose start analyzer`。

通过标准：租约过期后 Job 可重新领取；重试超过上限进入 `DEAD_LETTER`；重放接口可以再次分析，且无需重新采集。

## 7. 数据、产物和安全验收

### 7.1 PostgreSQL 与 MinIO

```powershell
docker compose exec -T postgres psql -U mini_drop -d mini_drop -c "\dt"
docker compose restart server analyzer
Start-Sleep -Seconds 15
curl.exe -s http://localhost/api/tasks
```

通过标准：任务、Attempt、Artifact、AnalysisJob、诊断与审计记录在重启后仍存在；MinIO 原始产物和分析产物可下载；哈希不匹配产物不能进入 AI 证据链。

### 7.2 安全边界

通过标准：

- AI 只能调用工具白名单；
- 不把模型文本直接拼接成任意 Shell；
- R2 单次批准，R3 仅人工处理；
- Artifact 访问校验任务归属和 object key；
- API 不返回密钥；
- 审批人、时间、动作和结果可审计。

## 8. 统一 AI 诊断验收

AI 的唯一入口为 `/ai-diagnosis`。旧 `/drop-insight` 和 `/diagnoses` 会重定向到该页面。

### 8.1 创建诊断会话

1. 打开 <http://localhost/ai-diagnosis>。
2. 点击左侧“诊断会话”。
3. 输入问题，例如：“订单服务最近 5 分钟延迟升高，请定位原因”。
4. 按页面追问补齐服务、环境、Agent、PID 和时间范围。
5. 提交会话。

通过标准：信息不足时先追问，不猜测目标；范围冲突时进入 `NEEDS_SCOPE_CONFIRMATION`。

### 8.2 查看透明诊断过程

诊断过程必须依次显示：

1. 归一化范围；
2. 候选假设；
3. 支持/反证所需证据；
4. 工具名称、参数、风险、预算和执行状态；
5. 审批状态；
6. 关联 Task 与 TaskAttempt；
7. Artifact 完整性和 Analyzer Job；
8. 被支持、被反证或证据不足的假设；
9. 带证据引用、置信度和局限性的报告。

通过标准：过程不是黑盒；点击证据引用可以追溯到真实任务和产物。

### 8.3 审批与反证

- 低风险探针允许自动执行；
- R2 显示批准/拒绝按钮，批准前不得创建对应高风险任务；
- 拒绝后记录原因并选择低风险路径或停止；
- 反证与假设冲突时，假设不得进入最终报告。

### 8.4 诊断历史与删除

1. 点击左侧“诊断历史”。
2. 打开一条历史会话，确认状态和报告仍可回放。
3. 点击删除并确认。

通过标准：默认执行软删除，历史列表移除；真实 Task、Artifact 和审计证据不会被无条件级联销毁。

### 8.5 方法与测试集

点击左侧“方法与测试集”，右侧应完整展示：

- 循证诊断、性能决策树和统一测试集的说明；
- 10 类场景及标准根因、必需证据和安全限制；
- Golden 回归的逐场执行过程；
- 真实 Campaign 的基线、故障、诊断、恢复和 Oracle 对比。

## 9. 十类真实故障 Campaign 验收

在“AI 诊断 → 方法与测试集”中找到“真实故障 Campaign”。

1. 选择场景。
2. 点击“一键制造故障并评测”。
3. 观察阶段：安全预检 → 基线快照 → 真实注入 → 故障确认 → 诊断取证 → 恢复快照 → Oracle 对比 → 清理。
4. 展开快照、任务、证据和对比结果。

正式场景：

- 代码热点
- CPU 饱和
- 下游故障
- GC 暂停
- I/O 争用
- 流量过载
- 内存增长
- 网络异常
- 同宿主机噪声邻居
- 队列积压

通过标准：

- 按钮只调用白名单故障开关；
- 每次创建独立 Campaign ID；
- 故障前后指标有可验证变化；
- 真实采集任务绑定有效 TaskAttempt；
- 隐藏 Oracle 只在评分阶段读取；
- 无论中途成功或失败，finally 清理均执行；
- 失败实验如实显示，不自动伪造成通过。

## 10. 正式 90 次策略评测验收

实验规模：10 个场景 × 3 种策略 × 3 次重复 = 90 次。

运行：

```powershell
cd D:\tx\mini-drop
python scripts/run_official_campaign.py `
  --base-url http://localhost `
  --output-dir reports/benchmark/official-90 `
  --timeout 120
```

完整性检查：

```powershell
python scripts/diagnosis_benchmark.py status `
  reports/benchmark/official-90/submissions.json

python scripts/diagnosis_benchmark.py evaluate `
  reports/benchmark/official-90/submissions.json `
  --require-complete `
  --output reports/benchmark/official-90/evaluation-confirmed.json
```

当前正式结果：

| 指标 | 结果 |
|---|---:|
| 计划/唯一执行 | 90 / 90 |
| 原始 Campaign | 90 |
| 完整完成 | 84 |
| 真实失败 | 6 |
| 重复/计划外/缺失 | 0 / 0 / 0 |
| 完整性门禁 | PASS |

| 策略 | 平均分 | 根因精确率 | 必需证据覆盖率 |
|---|---:|---:|---:|
| CONSTRAINED_HYBRID | 87.89% | 86.67% | 70.00% |
| EXPLORATORY | 85.78% | 83.33% | 70.00% |
| DECISION_TREE | 83.67% | 80.00% | 70.00% |

6 次失败均发生在内存故障确认阶段，已保留用于暴露注入器稳定性。详细报告见 [正式 90 次评测](../../reports/benchmark/official-90/README.md)。

## 11. 当前已知边界

### 必须完成的最终验收

- 在原生 Ubuntu 上复验 perf、eBPF 和跨语言采集矩阵；
- 保存内核、权限、TaskAttempt、Artifact 哈希、Analyzer Job 和可视化截图；
- 现场制造 I/O 或调度抖动并展示分布变化。

### 评测暴露的质量改进项

- 提升内存故障注入稳定性；
- 将 I/O 场景从进程自身写入扩展为共享宿主机争用；
- 提升当前 70% 的必需证据覆盖率；
- 继续收紧“证据不足却完成”的状态门禁。

### 后续生产化，不阻塞本阶段验收

- 多副本压力和故障转移测试；
- 企业 OIDC/RBAC、多租户和密钥轮换；
- MinIO 生命周期与孤儿产物巡检；
- Kubernetes/Helm、SBOM、镜像签名和灰度回滚；
- 长时间 Continuous Profiling 容量测试。

## 12. 最终验收记录模板

```text
验收日期：
验收人：
Git commit：
操作系统/内核：
Docker/Compose：

[ ] 核心容器健康
[ ] Agent 心跳、离线、恢复和审计
[ ] 状态机与 TaskAttempt
[ ] 八类采集器
[ ] Continuous Profiling
[ ] Analyzer 重试与重放
[ ] PostgreSQL/MinIO 持久化与哈希
[ ] 统一 AI 入口
[ ] AI 范围确认、假设、反证和审批
[ ] AI evidence_refs 与置信度
[ ] 十类真实故障 Campaign
[ ] 90 次正式策略评测完整性门禁
[ ] Python/React/Go 自动化回归
[ ] 原生 Ubuntu perf/eBPF 最终签字

失败项及证据：
遗留风险：
最终结论：通过 / 带条件通过 / 不通过
```
