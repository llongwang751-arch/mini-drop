# Skills

Skill 是经过验证的诊断路线记忆。每个 Skill 描述适用类别、检索词、探针顺序、预期观察和反证要求，不保存某次事故的既定根因。

## 保留结构

```text
skills/
  catalog.json
  <skill-slug>/
    SKILL.md
```

`skills/catalog.json` 当前登记 13 个 Skill：

| Skill slug | 诊断域 | 首选取证路线（缩写） |
|---|---|---|
| `cpu-hotspot-diagnosis` | CPU 热点 | 系统指标 → perf → 连续 Profile |
| `cpp-runtime-diagnosis` | C++ 原生运行时 | perf → 系统指标 → 连续 Profile |
| `dependency-latency-diagnosis` | 下游依赖 | 系统指标 |
| `fd-leak-diagnosis` | FD 泄漏 | 系统指标 |
| `gc-pressure-diagnosis` | JVM GC | 系统指标 → 内存 → JVM Profile |
| `go-runtime-diagnosis` | Go 运行时 | Go Profile → 系统指标 → perf |
| `io-latency-diagnosis` | I/O 延迟 | 系统指标 → eBPF I/O |
| `lock-contention-diagnosis` | 锁竞争 | 系统指标 → perf |
| `memory-growth-diagnosis` | 内存压力 | 系统指标 → 内存 Profile |
| `network-degradation-diagnosis` | 网络退化 | 系统指标 |
| `python-runtime-diagnosis` | Python 运行时 | py-spy → 系统指标 → perf |
| `queue-backlog-diagnosis` | 队列拥塞 | 系统指标 → perf |
| `same-host-contention-diagnosis` | 同机资源争抢 | 系统指标 → perf |

表中的路线只是默认 Prior；工具名是稳定协议标识，页面说明和 Planner 的用户可见文本仍以中文为主。

## 生命周期

```text
仓库内置或候选 Skill
  -> 结构校验
  -> 与当前问题检索匹配
  -> 只作为 Agent 路线先验
  -> 本次独立取证与反证
  -> 评测/发布门禁
  -> 可检索的长期记忆
```

Skill 命中不等于根因成立。无论命中还是未命中，任何报告都必须引用当前诊断新采集的 Evidence；冲突时退出到基线 Planner 并切换尚未覆盖的证据域，而不是沿用旧结论或旧事故 Evidence。

Skill 的可执行工具仍受 Control 端白名单、风险分级、预算、目标绑定和会话版本约束。

## 检索和冲突退出

生产检索不是简单字符串 `contains`，也没有使用 Elasticsearch、向量数据库或外部 embedding 服务。当前只有 13 个 Skill，进程内扫描更容易审计、部署和复现：

1. 先读取所有 ACTIVE Skill 的轻量 catalog/数据库元数据，不读取全部 Markdown 正文。
2. 用中英文 token、中文二元 n-gram 计算 BM25；同时把 token、ASCII 3/4-gram 和受控领域同义概念映射为 512 维确定性哈希特征向量，计算余弦相似度。它是本地词法/概念特征，不是神经 embedding。
3. 总分按结构上下文 30%、BM25 45%、本地特征相似度 25% 组合。环境漂移、显式运行时锚点不匹配、目标子系统冲突和 Collector 能力缺失是硬门禁。
4. Planner 的初始类别只提供加权 Prior，不再是一票否决。问题出现强文本信号时可以跨类别纠偏；跨类候选必须同时通过匹配词、BM25 和本地特征最低门槛。
5. 最高分低于阈值，或前两名在总分、BM25 和特征相似度上都过近时，检索主动弃权并回到基线 Planner。

这套方案适合当前小规模 Skill 集。以后只有在 Skill 数量、租户隔离、在线更新或检索延迟确实达到进程内扫描瓶颈，并经过离线回归后，才考虑接入 Elasticsearch/OpenSearch 或向量数据库；当前代码和部署都不需要它们。

若新 Evidence 与路线预期冲突，就退出旧 Skill 并重新检索或回到基线 Planner。实时自主诊断最多 4 轮；证据反驳、证据不足、部分支持、不可观测或门禁拒绝时，Planner 会从尚未覆盖的 CPU、内存、I/O、网络、运行时或依赖域生成新候选，并把全部 `OTHER/UNKNOWN` 别名规范化为一个未知兜底。

## 渐进式披露与完整正文

Skill 的披露分两层：

```text
候选检索：slug、类别、触发词、适用环境、探针顺序
    ↓ 命中且通过歧义/能力/环境门禁
完整加载：skills/<slug>/SKILL.md 全文、章节和来源摘要
```

仓库内置 Skill 命中后，服务端只允许在仓库 `skills/` 目录内解析名为 `SKILL.md` 的 UTF-8 文件，并校验 128 KiB 上限、必需章节、目录名和启动时种入数据库的 SHA-256。任一完整性检查失败都拒绝加载并退回基线 Planner。完整正文会进入 LangGraph 和 legacy Diagnosis Agent 的当前轮上下文，后续重规划仍沿用；但它始终标记 `is_evidence=false`，不能覆盖 Harness、目标绑定、工具白名单、审批、预算或 Evidence Gate。

数据库中由验证轨迹生成的学习型 Skill 没有对应仓库 Markdown，因此只披露已经门禁过的结构化路线，不伪造“完整文档已加载”。

## 多轮复用与动态树投影

一个会话不会每轮重新忘记 Skill。activation 在 PostgreSQL 中保存逐轮 `reuse_trace`：

| 状态 | 含义 |
|---|---|
| `ACTIVATED` | 首次命中并进入路线 |
| `REUSED` | 下一轮继续使用同一 Skill 的剩余探针 |
| `SWITCHED` | 反证或人工改向后切换到另一条 Skill |
| `DEVIATED` | 旧 Skill 不再满足上下文或被明确排除，退回重新规划 |
| `EXHAUSTED` | 路线已无可用探针；保留停止/证伪合同，但不再发起旧工具 |

初始规划、证据不足、可信反证、用户补充与人工干预五条路径都会重新解析当前 Skill 状态。服务端发布幂等的 `skill.route_activated`、`skill.route_reused`、`skill.route_exited` 事件；页面动态探索树读取 `skill_trace`、`skill_route_overlay` 和工具节点的 `skill_route_refs`，显示“召回 → 激活 → 第 N 轮沿用 → 偏离/退出”以及分类纠偏、正文 hash、已加载章节和当前探针步骤。

Skill 泳道使用虚线并标明“非证据先验”。工具节点上的“Skill 路线第 N 步 · 已采用”只表示实际 Tool Call 与推荐工具名对齐，不能解读为 Skill 已证明根因。

## 会话级 AUTO/DISABLED

`skill_policy` 是创建 Diagnosis 时的不可变实验标记：

- `AUTO` 允许生产 Planner 调用混合检索与歧义门禁。
- `DISABLED` 直接跳过 Skill 检索，只运行基线 Planner。

验证中心提供“真实 Skill AUTO vs DISABLED”：同一份请求会创建两条真实 Diagnosis，面板读取各自的激活、Tool Call、Evidence、轮次、首条证据耗时、路线和报告。事后切换标记会破坏对照，服务端不提供这种操作。若服务端返回的目标、进程或时间窗不完全相同，页面会明确提示“当前不是严格同 Scope 实验”；这类结果只能用于看真实路线，不能用于证明准确率或速度收益。

公网验收脚本不会要求 `DISABLED` 也必须执行 Skill 推荐的采集器，否则“基线未命中、AUTO 命中”反而无法形成报告。关闭组未走到预期采集器时会明确记录为未命中；AUTO 仍必须让预期采集器成功结束，产出非空火焰图/TopN、命中热点函数、生成支持 Evidence，并由 Report 引用。非关键后续探针的分析失败会保留在 `tool_route_observations` 中，Agent 已有可信根因链并成功完成多轮时不会因此抹掉整次验收；但每个 Task 仍须有状态事件和 Attempt，采集成功的 Task 仍须有带有效 SHA-256 的 Artifact，每组至少有一个成功 Task，支持证据必须关联成功 Task 并被报告引用。

同一浏览器会把 query、开始时间和 AUTO/DISABLED 两条 Diagnosis ID 保存到 `localStorage`（键 `mini-drop:skill-ab-history:v1`，最多 12 组）。返回验证中心后，面板按 ID 从服务端重新读取当前状态、证据和报告；本地记录不复制诊断结果。清除站点数据或更换浏览器会丢失这份入口索引，因此这不是账号级或服务端 A/B 历史。

## 服务端随机实验与长期监控

验证中心另有一套持久化实验，不等同于上面的双会话演示：

1. 实验先保存分流比例、每臂最小标注数、最小效果、显著性阈值和安全护栏。
2. 服务端使用实验盐、分层键和用户/流量单元键做 `SALTED_SHA256_STICKY_BUCKET`，稳定分到 `AUTO` 或 `DISABLED`；原始单元键不落库，浏览器也不能自行指定实验臂。
3. 每次分流都会创建真实 Diagnosis，并保存 Assignment、终态、报告验证状态、工具次数和安全事件。
4. 根因准确率只接受人工标注或受控 Oracle，不能拿模型自己的结论给自己打分。
5. 平台计算两比例 z 检验、95% 置信区间、效果百分点、验证报告率和安全护栏。后台只在出现新标注时追加指标快照，避免空轮询重复写入。
6. 达到样本量、显著性、最小效果和护栏后，状态最多自动变为 `ROLLOUT_RECOMMENDED`；只有具备权限的人审批后才是 `APPROVED`。这仍不会自动改 Prompt、代码、工具权限或线上流量配置。

因此“长期稳定性测试”在本项目中有明确载体：持续积累真实终态和人工/Oracle 标签，按时间查看准确率、验证率、工具成本和安全事件快照，而不是只跑一次本地脚本。“线上 A/B 校准”则是用这些真实标注重新调整分流比例、最小样本量、效果门槛和护栏；未经标注的数据不能声称准确率提升。

## Skill 与 LATS 的关系

LATS 决定“在多个候选分支中下一步访问谁”，Skill 提供“哪些分支或探针通常值得优先”。两者职责不能混写：

- `AUTO` 命中的 Skill 可以调整候选生成、probe order 或候选 Prior，但不能写入 Visits、Reward、Observation 或 Evidence。
- `DISABLED` 跳过 Skill 检索，仍可运行同一套 UCT/预算/Evidence Gate；它不是“关闭 AI”。
- `AUTO` 命中后也必须重新执行本次会话的探针；`DISABLED` 也可以自主跨域扩展。反证、证据不足或门禁拒绝只改变搜索奖励与下一方向，不能让 Skill 直接给出根因。
- 默认选择是论文式 UCT；只有配置明确为 PUCT 时 Prior 才进入选择奖励。页面在 UCT 模式下不能把探索项标成“先验奖励”。
- 候选评估公式只有在服务端完成多次独立采样时才使用 `lambda * LM + (1-lambda) * SC`。Skill match、候选排名和模型自报的 `self_consistency` 都不能冒充 SC；没有独立样本时保存 `SC=null` 与 `LM_ONLY_SC_UNAVAILABLE`/确定性 fallback。
- 工具结果经过 Evidence Gate 后形成的 reward 才能回传。Skill 命中本身不产生正奖励，Skill 冲突也必须由本次观察决定退出或转向。

验证中心的“完整 LATS 冻结回放”和“真实 Skill AUTO vs DISABLED”是两种不同实验。前者固定 `skill_policy=DISABLED`，在同一不可变 fixture 上证明 Selection、Expansion、Evaluation、Simulation、Backpropagation、Reflection、兄弟分支 reset 和恢复；后者用两条真实诊断证明 Skill 路线是否改变，并可能调用真实采集器。当前页面没有把二者拼成一个“冻结 Skill A/B”，因此不能把冻结回放结果宣称为 Skill 效果。

## 540-case 新评测

```bash
python scripts/generate_diagnosis_benchmark_v2.py
python scripts/run_diagnosis_benchmark_v2.py
python -m pytest tests/test_diagnosis_benchmark_v2.py -q
```

生成器不会读取已退役的旧测试集。公开输入和私有 oracle 分开保存，包含正向复用、信息不足/误导以及能力/环境漂移三类。Case 提示词是新生成的，但分类与正确 Skill 来自现有 catalog，所以这是项目内路线盲测，不是外部独立根因评测。页面数据来自生成的 `benchmark-report.json`；报告不存在时必须报缺失，禁止前端写死一组好看的数字。

当前报告中：

| 指标 | 结果 | 口径 |
|---|---:|---|
| 总体 | 506/540（93.70%） | 预测的 Skill 与私有 oracle 一致，负例应为弃权 |
| 正例复用 | 334/360（92.78%） | 选中正确 Skill |
| 负例拒绝 | 172/180（95.56%） | 信息不足、误导、能力或环境漂移时不激活 |
| 误激活 | 8/180（4.44%） | 负例中仍选了 Skill |
| No-Skill 弃权基线 | 180/540（33.33%） | 全部不复用；负例正确，正例全失败 |

这些数字只测路线记忆的选择/拒绝，不测根因、Evidence 质量或诊断耗时。生成器用固定种子产生新提示词，但分类和正确 Skill 来自本仓库 catalog，因此它也不是外部独立盲评。

## 540 条受控根因与 500 组同题 A/B

`root-cause-v1` 与上面的 `diagnosis-v2` 不是同一评测。它从故障广场 21 个可执行白名单合同生成 540 条受控变体，公开问题与私有根因真值分开；只有预测根因正确并在固定两次工具预算内到达关键采集器，才计为 Evidence 合格 Top-1。

```bash
python scripts/generate_root_cause_benchmark.py
python scripts/run_root_cause_benchmark.py
python -m pytest tests/test_root_cause_benchmark.py -q
```

当前 540 条中关闭 Skill 为 231/540（42.78%），启用 Skill 为 382/540（70.74%）。固定其中 500 条做同题同预算 A/B 后，关闭/启用为 41.20%/68.40%，提升 27.20 个百分点；改善 136、退化 0、不变 364，关键采集器提前 187 组。配对差值的 5,000 次确定性 bootstrap 95% 区间为 `[23.2, 31.2]` 个百分点，不一致样本的双侧精确符号检验 `p=2.295887e-41`。页面读取生成的 JSON 展示结果，报告缺失就明确报缺失。

这组结果测的是可执行合同上的确定性受控回放，不是 540 次公网 Linux 故障，也不能替代 live Collector/Artifact/Evidence/Report 验收。完整构造方法见 `PROJECT_LEARNING_GUIDE.md` 第 28 节。

## 四层测试集

1. **静态盲测**：公开 query 与私有 oracle 分离，调用生产检索器，测路线选择和安全弃权。当前 540-case 属于这一层。
2. **受控根因回放**：同题、同预算、公开输入/私有真值，测路线是否及时到达关键采集器以及根因 Top-1。
3. **受控故障合同/集成**：用故障广场的 21 个白名单 demo（Python 7、Go 4、Java 5、C++ 5）检查场景 ID、时长上限、自动停止、会话策略、至少 3 个有报告轮次、多轮干预、动态树和空 Profile 失败契约。这一层可以用模拟 HTTP/Artifact，不得写成公网真机成功率。
4. **Linux live E2E**：在真实 Linux Agent 上重放带真值的故障，让 Collector、MinIO、Analyzer、Evidence gate 和 Report 全部经过服务端链路，再做 `DISABLED`/`AUTO` 成对对照。真实采集成功率和端到端耗时只能来自这一层。

当前 Windows Docker daemon 未运行，不能在本机替代 Linux 真机验收。云端已有 Python 源码热点、Go CPU 热点、C++ CPU 热点、Java GC 压力以及持续 perf/eBPF 的独立 live 证据；21 场景严格闭环 Campaign 以 `scripts/run_fault_plaza_closure_campaign.py` 为入口，只有真实 Task、Attempt、非空且完整性通过的 Artifact、SUPPORT Evidence、Report 引用、终态和故障清理全部成立才计通过。`scripts/run_live_skill_ab_campaign.py` 仍用于成对路线验收，运行人需要为两个实验臂重置故障、保持负载一致并核对 Scope 相等。

最新候选回归为 Python 501 passed、3 skipped，Web 32 个测试文件/133 tests，Go 两个模块全包通过，OpenAPI 80 个 method/path 对通过，Web 生产构建和 bundle 检查通过。同一发布版本的故障广场 live Campaign 为 21/21；这些数字仍不能替代生产流量 A/B 或人工页面验收，最新线上发布与边界以 `PROJECT_CONTEXT.md` 为准。
