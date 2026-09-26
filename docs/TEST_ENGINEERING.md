# Mini-Drop 测试开发与质量工程

本文维护测试工程的执行入口与能力边界，评估日期为 2026-09-26。架构及云端状态仍以 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) 为准；业务测量标准维护在 [BUSINESS_ACCEPTANCE.md](BUSINESS_ACCEPTANCE.md)，真机故障标准维护在 [FAULT_PLAZA_ACCEPTANCE.md](FAULT_PLAZA_ACCEPTANCE.md)。

## 1. 项目评估与求职定位

作为个人工程项目，Mini-Drop 的系统深度较好：真实多语言服务、异步任务、采集、分析、鉴权、数据库状态和业务请求能串成完整链路。它的突出价值是失败之后仍能拿到可解释的证据，以及明确区分“执行成功”“证据有效”“根因验证”“业务恢复”。这已经具备性能测试和可靠性测试的应用场景。

目前最影响测开展示的，是测试入口分散、重要环境依赖测试可能跳过，以及功能广度大于持续验证的深度。此前演示主要从 AI 调查开始，面试官容易只看到平台开发。建议保留已有平台，把求职叙述聚焦为：**面向多语言服务的性能与可靠性测试平台，使用自动化回归、受控故障和证据采集辅助缺陷定位。** AI 是辅助调查模块，测试是否通过由明确的判据决定。

这是一种基于现有实现的展示角度，不表示本项目已经成为成熟的通用压测工具、混沌工程系统或生产自动修复平台。

## 2. 已有优势与实证

| 能力 | 实际实现与证据 | 面试时讲清什么 |
| --- | --- | --- |
| 测试分层 | Python pytest、React/Vitest、Go testing、Agent CTest、生成合同检查 | 各层发现什么问题，Mock 验证不到什么 |
| 接口与协议 | `scripts/check_openapi_routes.py`、任务/状态/错误码生成器 | 变更源合同、生成多语言代码、检查漂移 |
| 并发正确性 | `test_drop_insight_report_effects_postgres.py`、`test_outbox_postgres.py`、Go `idempotency_postgres_test.go` | Event/Barrier 控制交错，检查租约接管、串行化与幂等冲突 |
| 证据与负向测试 | `test_drop_insight_policy_evidence.py`、`test_artifact_integrity.py` | 错目标、低质量、未知样本、损坏 hash 和采集失败必须拒绝 |
| 业务性能验收 | `run_business_acceptance.py` 与 `business_test_plan.json` | 相同负载、固定问题、正常/异常/变更后窗口；检查正确性和尾延迟 |
| 故障实验 | 21 个隔离白名单场景、有限时长、finally 清理 | 注入生效、撤销恢复、AI 根因与代码修复是四件不同的事 |
| 可审计失败 | 原始 Artifact、请求数据、版本、哈希、失败报告保留 | 不用重跑成功覆盖失败，不把缺失数字补成零 |

改动前清点为 Python 80 个测试文件、Web 43 个测试文件、Go API 9 个测试文件、原生 Agent 5 个测试源文件。函数定义数和参数化后的执行数不同，这些均不是覆盖率。

本次改动前重新执行 Python：**625 passed / 7 skipped**；`server + analyzer` 行覆盖率 **70.60%**，不是分支覆盖率，也不是整个四语言项目的覆盖率。7 个跳过项是 5 个 PostgreSQL 用例与 2 个可选 Chroma 用例。日志/JUnit/覆盖率位于 `output/sdet-review-20260926/` 及 `output/sdet-baseline-20260926.log`；后续改动结果另存，不覆盖基线。

改动后实测：Python **650 passed / 7 skipped**、Web **203 passed**、Go **73 个测试事件通过 / 1 skipped**（包含子测试），高风险 smoke **82 passed**；3 个 HTTP 场景符合各自预期。完整版本、报告位置与未执行范围见 [本轮验证记录](../reports/architecture/test-engineering-review-20260926.md)。

同日第三轮增量后（见 3.4–3.7）：Python **693 passed / 7 skipped**（累计新增 43 项测试），完整 `python` profile 门禁 `PASSED_WITH_SKIPS`，最新报告 `output/quality/qa-python-round3-20260926/`。

## 3. 本次落地的改进

### 3.1 风险驱动的统一质量入口

源合同是 `contracts/quality_plan.json`，执行器是 `scripts/run_quality_gate.py`。它复用现有测试，不再维护一份独立的成功数字。

首次安装沿用现有环境准备：Python 使用 `python -m pip install -e ".[dev]"`，Web 使用 `npm --prefix web ci`，Go 按 `apiserver/go.mod` 准备。执行器自身不安装依赖、不启动 Docker、不访问部署环境。

```powershell
# 默认执行 smoke，输出目录自动带 UTC 时间与随机 ID
python scripts/run_quality_gate.py

# 列出/校验风险合同，不运行测试
python scripts/run_quality_gate.py --list

# 全部 Python 测试与覆盖率记录
python scripts/run_quality_gate.py --profile python

# 本地跨语言回归
python scripts/run_quality_gate.py --profile local

# 真实本机 HTTP 三窗性能实验
python scripts/run_quality_gate.py --profile business
```

| 配置 | 执行内容 | 不在该配置内的验证 |
| --- | --- | --- |
| `smoke` | 6 项合同/计划检查；状态机、预算、证据、产物、Analyzer、业务验收与测试基础设施的高风险回归 | 全量功能、真实数据库与浏览器 |
| `python` | 合同检查；全部 pytest；行覆盖率 JSON | Go/Web/原生运行；缺少可选依赖时的真实检索 |
| `local` | `python` + React/Vitest + Go API + Go 故障目标编译 | Go race、原生 Linux、PostgreSQL 专项、浏览器 E2E、生产发布 |
| `business` | 固定测试计划下 3 条 HTTP 场景，每条正常/异常/变更后三个窗口 | 外部原办公助手实验、真实 LLM、生产流量、容量极限 |

每次生成新的 `output/quality/<run-id>/report.html` 和 `report.json`，包括 profile、源码 commit、脏工作树标志、源码摘要、计划 hash、开始/结束时间、每个套件状态、风险映射、失败用例、跳过原因、原始日志、JUnit 与文件 hash。Go 数量包括 testing 框架报告的父测试及子测试，不与 pytest 数字相加来制造“总覆盖率”。`go-demo` 目前仅验证编译，不能因退出码为零称其存在行为测试。

报告没有远程资源依赖，可以直接打开 HTML。风险区显示本次执行了哪些套件，不表示该风险已穷尽；没有执行的风险显示 `NOT_RUN`。报告摘要由 JSON 生成，不手工改 HTML 中的结果。

运行结束会再次核对源码与测试计划摘要；执行中发生源码变化会标为 `FAILED / SOURCE_CHANGED_DURING_RUN`，需在稳定工作树重跑。开发依赖也明确声明测试直接使用的 `jsonschema` 和 `PyYAML`，避免本机已有包掩盖干净 CI 的收集错误。

判定规则：

- `PASSED`：选定配置的所有执行项通过且没有跳过；不是整个平台发布许可。
- `PASSED_WITH_SKIPS`：实际用例通过，但存在合同明确允许的环境跳过，报告保留原因。它不能证明被跳过能力正确。
- `FAILED`：失败命令、超时、缺失/损坏报告、未知跳过、零测试或全部跳过。Go 报告出现重复终态或已启动用例没有终态，也拒绝通过。
- 中途报告保持 `RUNNING`；进程被外部强制终止时不会留下伪造的完成状态。

执行器不会自动重试失败；输出目录已存在就拒绝启动，防止覆盖证据。覆盖率目前是观测项，没有凭空设一个百分比作为硬门槛，也没有启用差异覆盖率或分支覆盖门槛。

### 3.2 PostgreSQL 并发进入独立 CI

原 CI 普通 pytest 没有配置数据库，所以关键数据库竞争用例会跳过。本次新增 PostgreSQL 16 服务容器，数据库名称固定为 `mini_drop_ci_test`；Python/Go fixture 继续在各自随机 schema 内创建和清理数据。

专项必须执行 5 个 Python 用例和 1 个 Go 真实幂等竞争用例。校验器检查指定用例、JUnit、Go 测试与包级 PASS、运行元数据，拒绝缺失、失败及 skip。普通 Go job 也增加 `-race -count=1`。无论成功失败，保存日志、JUnit、Go JSON、数据库日志和 commit/run 信息。

Native Agent 使用 `ctest --no-tests=error`；Control 当前没有注册测试，因此工作流明确标记为只构建，避免零测试被称作通过。Python CI 复用统一质量入口并上传报告。

本次只完成本地配置与静态校验，**尚未推送运行 GitHub Actions、没有实跑这份新 PostgreSQL 作业**。本机 Windows race 尝试因运行时错误 `0xc0000139` 退出；Linux CI 的实际 race 成绩仍待执行，不能写成已经通过。

### 3.3 两个真实验收缺陷

| 缺陷 | 最小失败输入 | 旧行为 | 修复后 |
| --- | --- | --- | --- |
| 缺失阶段被补零 | 30 请求中只有 1 条记录 retrieval=123ms | 其余 29 条被当作 0，阶段 P95=0 | P95=123ms，`stage_sample_counts.retrieval=1`；没有记录的阶段不造数据 |
| 低质量降级误通过 | 30/30 HTTP 成功且响应更快，但均为降级、质量检查全失败 | `DEGRADED_AVAILABLE` | `REJECTED`；降级也须达到质量门槛且不低于正常基线 |

修复位置为 `server/app/drop_insight/business_acceptance.py`，回归在 `tests/test_business_acceptance.py`。真零值、全部阶段缺失、质量低于基线、旧报告兼容及篡改拒绝都纳入测试。原有成功率、负载可比性、总请求延迟、trace 去重等判据继续保留。

新增阶段计数字段会影响旧实际 RAG 报告的精确比较，因此投影器只兼容该新增字段的缺失，继续核对旧指标与结论。已经用仓库历史实际 RAG 报告只读验证；没有回写旧 JSON，也不把有错误旧指标的报告“迁移”为新通过结果。前端保留原阶段数值接口，新样本数目前在机器报告中可见。

### 3.4 重复性能回归，与它暴露的一个场景缺陷

`scripts/run_business_acceptance.py` 新增 `--repeat N`（1..10）：每一轮都是独立 campaign（新服务、新窗口、新端口），逐轮落盘为 `campaign-rXX-cases/`，聚合报告写入 `repeat_summary`（`mini-drop.business-repeat-summary.v1`，标注 `ALL_RUNS_RECORDED; NOT_BEST_OF`）：逐场景 outcomes、`outcome_stable`，以及 baseline/fault/after 三个窗口的 P95 min/mean/max/stdev。`--require-outcomes` 语义为所有轮次都必须达标。质量门禁新增 `stability` profile（`business-stability` 套件，`--repeat 3`）；CI business 作业新增独立重复步骤（见 3.5 的“尚未推送”边界）。

**真实发现**：首次 `--repeat 2` 时 RAG-03 两轮均为 `REJECTED`（预期 `DEGRADED_AVAILABLE`），且稳定复现。根因是场景设计缺陷：旧基线不含任何依赖耗时（P95≈30ms），恢复阈值=基线×1.3≈39ms，而降级路径固定多出 10ms 超时等待（P95≈43ms）——机器越快越必然 REJECTED，此前通过只是因为当时基线绝对值更慢。修复：RAG-03 基线锚定为 `Settings(dependency_latency_ms=10)`，与处置后的超时常数一致，比值随机器缩放；修复后两轮均 `DEGRADED_AVAILABLE`，after/baseline≈1.02。两次 REJECTED 的原始证据保留在 `output/qa-business-repeat-20260926/campaign.json`，未删除；`tests/test_business_repeat.py::test_degradation_scenario_baseline_shares_the_dependency_constant` 防止场景回退。测试计划中 RAG-03 的 requirement 已同步。

### 3.5 真实 Chromium 回归进门禁与 CI

`scripts/verify_frontend_workbench.mjs` 三处更新：`MINI_DROP_ACCEPTANCE_CHROME` 优先，否则自动探测 `ms-playwright` 下最新 `chromium-*`（兼容 chrome-win64 / chrome-win / chrome-linux 布局，CI Linux 与本机共用一条路径）；新增 `--output` 参数供门禁隔离证据目录；断言随 9 月 23 日体检改版更新——结论摘要改为“首屏可见”并记录实测位置（当前 672px），排查树为默认视图后改为断言真实父子树画布渲染、全屏弹窗画布占满视口。当前 7 项检查、0 个浏览器异常（`output/qa-browser-20260926/browser8/result.json`）。

`contracts/quality_plan.json` 新增 `web-browser` 套件（风险映射 ui-regression）与 `browser` profile，并把 `web-browser` 加入 `local`（需要先 `npm --prefix web run build`）；CI 新增独立 `web-browser` 作业（npm ci → 构建 → playwright chromium → 脚本）。与 3.2 相同的边界：**工作流已配置、本地已全绿，尚未推送运行 GitHub Actions，不能称 CI 已通过。**

### 3.6 关键模块分支覆盖率门槛（先基线，后约束）

`python-all` 开启 `--cov-branch`，全库口径从纯行覆盖 70.60% 变为：语句 70.61%、分支 55.65%、行+分支合并 66.84%——引用时必须写清口径，不能混用。`critical_coverage` 按模块设下限（2026-09-26 本地基线向下取整，约束新增未测分支而非追全库 100%）：

| 模块 | 基线（行+分支） | 下限 |
| --- | --- | --- |
| `business_acceptance.py` | 94.69% | 94 |
| `event_store.py` | 95.70% | 95 |
| `hypothesis_predicate.py` | 85.39% | 85 |
| `report_conclusion.py` | 69.40% | 69 |
| `fix_verification.py` | 13.64% | 13 |

低于下限、或模块从未被执行（`observed=false` 记 0）都会判 `FAILED`。门禁证据：`output/quality/qa-python-final-20260926/report.json` 的 `critical_coverage`（GATED，0 breaches）。

### 3.7 补测 fix_verification、共享 fixture 与 property-based 测试

- **`fix_verification.py`：13.64% → 100%（分支含行），下限钉到 100。** `tests/test_fix_verification.py` 用 18 项行为测试覆盖修复复测闭环：核心不变量是“修复前后任一任务缺 TopN 数据只能 `REJECTED`，不能把数据缺失当作热点消失”；另有相对下降阈值边界（恰好 1−0.3 判 VERIFIED、略高判 REJECTED）、非 dict 行与字符串占比的容忍、请求窗口重叠的字符串/naive/aware 时间语义，以及 sqlite 引擎上 `verify_diagnosis_fix` 持久化、缺失数据留痕、新序优先与 limit。`quality_plan.json` 中该模块下限从 13 上调到 100：此后新增分支必须带测试才能进门禁。
- **`tests/conftest.py` 收拢共享 fixture。** `postgres_sessions` 与 `NOW` 从 `test_drop_insight_report_effects_postgres.py` 移入 conftest；原模块保留 re-export 作为历史导入点，`test_outbox_postgres.py` 改从 conftest 导入 `NOW`。本地验证 skip 行为与收集数不变；PG 用例的真实执行仍只能由 CI 作业证明。
- **hypothesis 进入 dev 依赖，property-based 测试落地。** `tests/test_business_acceptance_properties.py` 用 6 个 property（合计约 500+ 随机样例）锁定业务验收的统计与判定三角形：P50/P95 必须是观测样本且符合 nearest-rank 重算（oracle 独立于实现）、阶段统计恰好覆盖观测样本（缺失不补、真零保留）、after 窗口任意失败→`REJECTED`、任意降级→`DEGRADED_AVAILABLE`、全健康→`IMPROVEMENT_VERIFIED`、跨窗口复用 trace 或任一负载字段漂移→`INCOMPARABLE`。
- 全量 **693 passed / 7 skipped**；门禁 `output/quality/qa-python-round3-20260926/` 为 `PASSED_WITH_SKIPS`、`critical_coverage` 0 breaches（fix_verification 100.0% 达标）。

## 4. 与开源工具对比

以下是官方机制对照，未在同机、同负载下比较性能，不能据此给出领先排名。

| 参照项目 | 可借鉴的能力 | Mini-Drop 的现状 | 实际取舍 |
| --- | --- | --- | --- |
| Grafana k6 | 开放/封闭负载模型、场景与阈值，阈值失败返回非零退出码 | 已有固定到达率、客户端排队计入延迟、可比性及质量门禁；当前只有小型固定 HTTP 案例 | 需要通用协议与阶梯负载时接 k6，不重写完整压测器 |
| Chaos Mesh | 串并行/条件故障工作流、连续状态检查与自动终止 | 白名单场景、限制时长、finally 清理已存在；通用业务探针、组合实验与运行中止损不足 | 先完善当前 Compose 故障实验协议；无需为对标立即迁 K8s |
| Grafana Pyroscope | 连续 Profile 查询、并排对照和差分火焰图 | 已有窗口化连续采集和多语言产物；跨版本比较、统一历史检索不如成熟工具完整 | 为测试 run、工作负载和 commit 关联 Profile；热点占比不能替代业务性能验证 |

官方来源：[k6 阈值](https://grafana.com/docs/k6/latest/using-k6/thresholds/)、[k6 负载模型](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)、[Chaos Mesh 工作流](https://chaos-mesh.org/docs/create-chaos-mesh-workflow/)、[状态检查](https://chaos-mesh.org/docs/status-check-in-workflow/)、[Pyroscope Profile 对比](https://grafana.com/docs/pyroscope/latest/view-and-analyze-profile-data/pyroscope-ui/)。

本项目有辨识度的部分是测试失败后的证据链和受约束诊断。成熟开源项目在通用负载、实验编排、查询体验和生态上更完整，不适合声称本项目已达到这些工具的生产成熟度。

## 5. 接下来最值得优化的事项

| 优先级 | 问题与动作 | 完成标准 |
| --- | --- | --- |
| P0 | 在新 CI 实际执行 PostgreSQL 并发与 Linux race | 5+1 指定测试零跳过，报告和构建版本可追溯；工作流全通过 |
| P0 | 推送并实跑本轮新增 CI：web-browser 作业与 business 重复步骤 | 真浏览器与 3 次重复实验在 Linux CI 全通过，证据 artifact 可追溯 |
| P1 | 关键真实浏览器回归接 CI | 已配置并本地全绿（3.5）；剩余同 P0 推送执行，不能用 jsdom 代替浏览器 |
| P1 | 补可选 Chroma 集成作业 | 安装 retrieval 依赖，独立索引目录，两个真实检索用例不跳过；留出评估数据 |
| P1 | 做透一个真实业务缺陷闭环 | 回归失败→Profile 定位→最小修复→排序/隔离/新鲜度回归→同负载复测；优先复用已有 RAG JSON 解码案例 |
| P1 | 严格根因门禁缺少独立对照 | 针对 20 个失败项补采集可达性和判据映射，不降低门禁换通过率 |
| P2 | 拆分仍约 8,500 行的 `drop_insight/service.py` | 以状态、取证、报告等真实边界拆分，保持行为回归，不把“拆文件”本身当收益 |
| P2 | 扩大关键模块清单并逐步上调下限 | 新增预算、幂等相关模块入 `critical_coverage`（如 `report_conclusion.py` 69% 仍可提高）；随债务清偿上调既有下限 |

重复性能回归（多次独立运行、记录 P95 波动）已在 3.4 完成；`fix_verification.py` 补测、conftest 收拢与 hypothesis property-based 测试已在 3.7 完成。

还未解决的边界：长期 soak 与容量极限缺少新实测；受限资源账号目前采取明确拒绝，并未实现完整多租户资源过滤；Trivy 和 Go lint 的部分检查仍为 report-only，不能称全部安全规则阻断发布。多语言架构便于展示跨栈能力，也增加依赖和维护成本，下一步应优先闭环测试，不再扩展技术栈。

## 6. 测开面试演示路线

建议选择能够自己逐行解释的一条主线：

1. **讲风险**：例如降级接口 200 但答案质量下降，为什么只断言状态码不够。
2. **讲用例设计**：正常、异常、边界、稀疏数据、质量低于基线；指出旧代码会怎样误判。
3. **运行 `smoke` 并打开报告**：展示失败/跳过规则、风险映射、源版本与原始日志，不只展示绿色通过数。
4. **运行 `business`**：解释同负载三窗、固定问题与引用校验；第三案例持续依赖慢，回退只能判降级。
5. **展示一次取证链**：已有服务请求→进程→Profile/指标→报告；说明 AI 提议不构成测试真值。
6. **讲质量落地**：用例如何进入 CI，真实数据库竞争与 SQLite/Mock 有什么差别，为什么允许的 skip 仍不代表执行成功。
7. **讲重复运行暴露的场景缺陷**：`--repeat 2` 让 RAG-03 在快机器上稳定翻成 REJECTED（阈值 1.3×基线，降级路径固定多 10ms），修复是把基线锚定到同一依赖常数——用 `output/qa-business-repeat-20260926/` 里失败与修复后的两份 campaign 证据讲“性能验收的阈值必须随机器缩放”。

可使用的项目介绍：

> 我围绕多语言服务开发了性能与可靠性验证能力，设计接口合同、任务状态、并发幂等和证据门禁回归，并通过受控故障和同负载 HTTP 实验验证业务变化。测试失败后关联进程采样辅助排查，判定逻辑独立于 AI。我还修复了缺失阶段被补零、低质量降级被误通过的问题，并为质量入口加入防止跳过和缺失报告假绿的检查。

这些描述只有在你确实理解并能解释对应实现后才适合用于个人贡献陈述。可引用真实执行结果；不要写“根因准确率95.2%”“21/21自动修复”“节省80%排障时间”或“生产高可用”等未证实成绩。最新严格历史验收是 **1/21**，不是准确率；故障撤销也不是修复代码。
