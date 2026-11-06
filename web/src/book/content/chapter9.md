## 9.1 图变绿了，为什么还不能说优化成功

假设第一次报告里某函数占 70%，复测降到 10%。你可能会说“它快了七倍”。但如果第一次总 CPU 时间是一秒，第二次是十秒，那个函数可能从 0.7 秒变成一秒，实际还慢了。占比的分母会变，火焰图宽度不等于请求耗时。

再换一种情况：第一次采正常业务，第二次采到一个空闲线程，图自然干净。比较程序必须先确认“比较的是同类东西”，才有资格计算差值。Drop 的复测不是两张截图摆一起，而是持久保存 baseline、candidate、原诊断和新诊断，再在提交边界检查它们是否仍指向当前证据。

本章沿两条路径学习。第一条是 `api/internal/service/diagnosis_verify.go` 的现场复测，它主要检验现象能否复现。第二条是 `code_repair.go → ops/code_executor.py → integrations/.../harness/run_job.py`，在登记源码的隔离副本中做补丁、功能回归和受控性能对照。

不要把两者混成“AI 自动找到根因并上线修好”。现场重复采样没有实施代码干预；隔离基准即便改善，也只证明这个组件、这些输入和当前条件下的变化。先把能证明的范围压准，实验才有价值。

## 9.2 自动比较先验身份，算术排在后面

`compareProfiles` 读取结果的单位、总权重和 Top 表，同时解码两项 task 的参数。下面是实际源码中的拒绝条件，换行只是便于阅读：

```go
if a.Kind != b.Kind || a.AgentID == nil || b.AgentID == nil ||
    *a.AgentID != *b.AgentID || p.PID != q.PID || p.Scope != q.Scope ||
    string(pt) != string(qt) || p.Source == "import" || q.Source == "import" ||
    out.Unit == "" || out.Unit == "unknown" || out.Unit != y.Summary.Unit {
    out.Limitations = append(out.Limitations, "采集器、来源、目标身份或测量单位不一致/未知，拒绝自动比较。")
    return out, nil
}
```

第一行挡住任务类型不一致和缺少现场 Agent。第二行核对同一节点、PID、scope 与序列化目标。第三行拒绝把导入数据混入现场自动比较，并要求单位已知且一致。返回没有 Go error，因为“数据有效但不可比”本身就是一种正常业务结果，不能假装算成功再把限制藏在日志里。

这里也要如实看待实现边界：这个函数没有单独要求两次 duration、frequency 或 options 完全相等，更不会凭空知道两次请求负载是否一致。目标结构的比较也不等于它亲自复核远端 `/proc`。运行身份由采集和源码绑定等其他边界核验；负载、配置、版本仍要由实验设计记录。

因此 `comparable=true` 应理解为通过了当前程序规定的基础口径检查，不是统计学或因果认证。读一个布尔字段之前，先读它怎么被计算出来。把字段名字当成自然语言承诺，是很多演示报告夸大的起点。

## 9.3 samples、cpu_ticks、bytes，不能换一个名字就相减

PROC_CPU 的 cpu_ticks、CPU 栈的 samples、堆的 bytes，分别是不同测量对象。两边都叫 total_samples 也不意味着数值都表示采样次数；必须读 summary.unit。项目保留历史字段名兼容接口，所以领域含义要以类型和单位一起确定。

比较器对相同符号计算 `candidate_self_percent - baseline_self_percent`，得到的是百分点。70% 变 10% 为负六十个百分点，既不是“快六十倍”，也不是请求延迟下降 60%。返回的符号还做了摘要化处理，模型不需要拿到原始函数名才能描述差异大小。

下面摘自 `diagnosis_verify.go`：

```go
if out.Unit == "samples" && x.Total >= 1000 && y.Total >= 1000 {
    out.Outcome = "comparison_ready"
} else {
    out.Limitations = append(out.Limitations, "样本数不足1000或单位不是采样计数；只展示描述差异，不给统计显著性结论。")
}
```

这里的一千是程序的展示门槛。通过门槛仍没有置信区间、独立性判断或显著性检验；名字叫 comparison_ready，不叫“证明有收益”。另一个限制也值得注意：Top 表没有返回的符号按零展示，只表示未进入这张有界表，不表示它从未执行。

纸上练习：基线 2000 samples，候选 30 samples，单位相同、目标相同。预期可以给描述差值，但 outcome 仍为 insufficient_evidence。把候选改成 3000 bytes，则连统一口径也不满足。不要为得到绿色状态把单位字段直接改成 samples。

## 9.4 样本多也可能质量差，未知信息必须留下来

py-spy 可能得到无法解析的栈或帧。若把这些样本丢掉，剩下的已知函数会被动变得更“热”。分析器选择保留未知占位符和权重，诊断工具再投影固定质量字段，例如未知栈条数、占位符数量、未知样本权重和总权重。

`diagnosis_sampling_quality.go` 不接受一段随便自报的质量 JSON。它核对当前 task、结果 kind、py-spy 来源、samples 单位、分析 pipeline、collector provenance，以及结果 section 和 metadata 中两份计数的一致性。未知权重还必须在零与总权重之间，条数、占位符也有范围关系。

为什么要把质量也绑定来源？因为一个导入文件完全可以携带“零丢样”的漂亮 metadata。它不能因此被认证为平台现场采集。历史结果缺少这份质量数据时，源码返回未知，不补一个全零对象。`normalized=false` 只说明没有执行这种未知帧规范化，不能证明完全没有其他丢样。

这会影响结论强度：如果热点主要是 unresolved 占位符，可以说“当前无法解析的样本占比较高，需要补证”，不能给它编一个业务函数和源码行。测试 `diagnosis_sampling_quality_test.go` 有坏单位、错误来源、错误计数等负例，值得逐个看它们为什么不能进入事实链。

实际实验记录还应写工具版本、频率、采样时长、负载阶段、目标构建和已知限制。质量信息不是为了把每次测量都判失败，而是帮助你决定下一步该重采、补符号、改方法，还是已经足够回答当前的小问题。

## 9.5 基线也会过期，旧报告不能永远当证据

假设你已经完成诊断，用户随后对同一 task 重试或重新分析，结果发生变化。复测流程若只保存 task_id，就可能把旧诊断与新 RAW、甚至新的 analysis run 拼在一起。每一个片段都真的存在，拼起来却讲了一件从未发生的事。

`verificationBaseline` 按任务 ID 顺序锁住两侧 task，重新读取当前用户，核对操作权限，再读取成功诊断、最新 attempt 和当前采集/分析状态。它还调用 `ValidateDiagnosisSource` 检查诊断输入来源。只有原基线仍指向同一份有效证据，流程才继续。

读 `TestPostgresDiagnosisVerificationSourceReanalysisFence`：它先建立诊断与确认，再故意改变分析作业的 attempt，推进 verification。预期是 `STALE_ATTEMPT`，并且不生成 candidate diagnosis。这个用例不是说所有数据库 UPDATE 都该被禁止，而是验证来源变了以后，旧流程不能悄悄换材料继续。

授权同样可能过期。一个流程开始时可读，后来用户离组或权限被撤销，不能因为后台保存着 user_id 就继续做所有操作。长任务需要在关键边界重新核对当前权限，不能把启动时的鉴权当成无限期通行证。

练习答案可以只写一句很具体的话：复测绑定 task、采集 attempt、分析来源、诊断和当前用户权限。然后逐个删除这些约束，想一个会被放过的坏情景。这样比抄一串“不可变、可追踪、可审计”更容易检验理解。

## 9.6 复现与因果，相差一次真正的干预

复测又出现相同热点，说明现象在这些条件下能够复现。它没有排除另一种解释：函数占比高只是因为上游等待少了，或者流量恰好变大了。`ProfileComparison.RootCauseStatus` 初始就是 `not_tested`，代码不会因为两张 Top 表接近而给出因果确认。

更强的实验需要先写可证伪假设。例如“长查询的重复归一化消耗 CPU”，然后只改变这个机制，保留同一批输入与输出契约；改后改善、撤销后现象回归，再重复测量。若同时换机器、加缓存、降请求量，很难分清哪一项产生了效果。

```text
记录正常基线 → 实施已知干预 → 独立采集/诊断
           → 撤销干预并等待影响消退 → 再采集/诊断
```

这只是实验设计示意，不是一次已经执行的现场记录。缓存、连接池、预热、后台任务会让撤销不是立刻复原，因此需要洗脱时间与业务观测。对系统故障做干预也应在明确隔离目标上，不应为了学习随手改生产数据库或杀业务进程。

项目的 `scripts/quality/causal/v2` 保留干预、独立测量、撤销与复采这一思路，并把执行成功与语义判断分开。请求正常返回只能说明流程跑完；模型是否识别到正确机制，要按事先冻结的预期与独立审查评价。

## 9.7 源码定位必须回答：测到的真是这一版吗

现场报告出现 `foo.py:80`，本地也恰好有同名文件，不等于可以修改它。部署中的文件可能比本地旧，目标 PID 也可能已经复用。项目用管理员登记的源码 manifest 与 runtime binding 绑定项目、source_revision、目标节点、PID、UID 和启动 tick。

`investigation_code_test.go` 的 `TestDiagnosisSourceRuntimeBindingRejectsReusedOrDifferentTarget` 逐一改变 PID、starttime、UID、check_uid、Agent 和源码版本，预期全部拒绝。这些不是模型需要自己记住的限制；Go 在模型工具之外执行，不能被报告里的一句“我确认是同一版”覆盖。

源码 broker 的 `read_source` 只读登记路径，核对真实文件 SHA 与 manifest，最多返回一百六十行。SHA 在这里证明读到的是所登记字节，不证明这份程序没有 bug，也不是签名。报告位置还要来自实际原始 profile，不能从阅读过的文件里随便猜一个像热点的行号。

一个常见反例是：读完源码以后开发者又改了文件，模型仍用旧上下文提出替换。项目会拒绝 stale source，而非尽力“猜着打补丁”。安全失败比误改相似函数更可解释，用户可以明确重新登记、重新采集，再开始新一轮。

因此源码修复前的准备工作不是上传一个压缩包就结束。登记允许读取/编辑的文件、固定工具镜像、冻结测试与测量驱动，才形成一个可以回答“这次具体改了什么”的实验范围。

## 9.8 先验原 SHA，再做唯一上下文替换

以下是 `ops/code_executor.py` 的实际摘录，仅用于阅读，不要把这些函数片段当成独立脚本运行：

```python
require(name in manifest['editable_files'] and name in manifest['files'], 'PATCH_PATH_NOT_ENROLLED')
target = (base / name)
require(target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(base.resolve()), 'PATCH_PATH_ESCAPE')
require(file_hash(target) == edit['sha256'] == manifest['files'][name], 'PATCH_SOURCE_CHANGED')
```

第一行限定文件集合。第二、三行处理真实文件与路径归属，不能只用字符串 startswith 判断，因为相似目录和符号链接会绕过它。最后一行让磁盘字节、请求声明和登记摘要三者一致。只核对模型报来的 SHA，相当于让被检查者自己发合格证。

随后程序要求 old 上下文只出现一次，同文件多段不得重叠，限制段数、字节数和结果文件大小，并用 AST 检查允许导入、危险反射、缓存规模等。Saber preset 还冻结 SQL/租户相关结构，通用项目的原测试与 harness 也不能由补丁修改。

这些条件只证明“修改在允许范围内且可解析”。语法树过关不能证明逻辑正确，唯一上下文也不能证明优化有效。后面仍要跑原测试、独立回归与性能对照，避免把限制多误解为检验已经完成。

源码实验可用纸上负例：把 SHA 改一位、old 改成重复出现的片段、路径换成原测试、补丁加入无限缓存。预期分别在版本、唯一上下文、登记范围、缓存约束处拒绝；不用真的启动 Docker 才能先把这些拒绝边界解释清楚。

## 9.9 沙箱隔离的对象是什么，取消又能删谁

Go 只提供固定的 profile、read_source、test_patch 工具，模型不能选宿主命令、镜像和挂载。`code_repair.go` 要求先取得热点并读源码，最多实际测试两次补丁；参数错误与真实测试失败会被区分，不该靠不断构造坏参数耗尽所有实际测试预算。

broker 为本次 job 准备 baseline、candidate 和独立输出。下面是 `code_executor.py` 构造 Docker argv 的实际片段；它是阅读材料，学习时不要直接复制去操作现有容器：

```python
'--pull', 'never', '--network', 'none', '--user', '10001:10001', '--cap-drop', 'ALL',
'--security-opt', 'no-new-privileges:true', '--read-only', '--memory', '512m', '--memory-swap', '512m',
'--cpus', '1', '--pids-limit', '64', '--ulimit', 'fsize=1048576:1048576',
```

无网络阻止候选代码自行外联，UID 和 capability 约束降低容器权限，只读根目录与受限挂载缩小可写范围，内存/CPU/进程/文件和二百四十秒墙钟限制约束资源。固定镜像用不可变 image ID，而非让模型临时选择一个“更方便”的镜像。

控制面 API 不挂 Docker socket；root broker 承担容器创建与归属验证。取消时核对 job 标签、镜像和不可变容器 ID，再停止自己这次的容器，不能只凭一个可被复用的名称删除。调用方断连也会撤销同步任务，硬终止窗口仍由资源上限兜底，不能说容器技术消灭了所有风险。

最关键的产品事实是，候选补丁作用在隔离快照，报告含 `production_deployed=false`。输出 patch bundle 不等于已经替用户合并原仓库，更不等于生产发布通过。讲演示时应把这条边界说在结果旁边。

## 9.10 功能没坏，要用能抓住坏实现的断言证明

“测试通过”要先问测试看了什么。一个函数本该返回独立 list，补丁用缓存返回同一可变对象，普通值相等断言可能通过，第二次调用却被第一次修改污染。通用 harness 不只比 output hash，还检查类型和 mutable_output_isolated，避免以改变契约换性能。

通用 Python 协议要求另交独立新增 unittest：同一份测试在 baseline 应以断言失败复现缺陷，在 candidate 应通过。基线因为导入错误崩溃、测试被 skip、用 mock 绕过真实行为，都不能算建立缺陷。两边读取相同测试文件，不能让补丁顺便把期待值改成自己的输出。

`integrations/generic-python/harness/run_job.py` 明确记录 baseline_failed、candidate_passed、同一测试文件及其 SHA，再执行 candidate 原 build/test。旧 CRM/Saber preset 使用各自冻结验收，并不能因通用协议新增测试而宣称历史运行也执行了红绿回归。

负控还能检验验收器自身。提交一个没有复现缺陷的测试，应被拒绝；提交值相同但类型变坏的补丁，应被后续契约检查抓住。`ops/test_code_executor.py` 的两个 `test_real_harness_rejects_...` 用例就是在问：我们的门禁是不是只会给好消息。

这比“让模型写十个测试再自己判通过”更可信，因为测试输入、原回归、输出要求和接受门槛有独立来源。模型可以提出候选方案，却不能同时随意改题目、答案和评分规则。

## 9.11 六组交替 A/B，比只跑一次强在哪里

先跑原版六次、再跑新版六次，容易把 CPU 温度、缓存预热、邻居负载的时间变化误当优化。通用与 Saber harness 使用交替顺序：偶数组 baseline→candidate，奇数组 candidate→baseline。每组用匹配 seed，记录两侧 CPU 时间和输出一致性。

以下摘自通用 `run_job.py`：

```python
order = ['baseline', 'candidate'] if trial % 2 == 0 else ['candidate', 'baseline']
ratios = [pair['baseline_cpu_ns'] / pair['candidate_cpu_ns'] for pair in pairs]
speedup, negative_median = statistics.median(ratios), statistics.median(controls)
```

第一行改变先后次序，第二行先算每组配对比值，第三行取比值的中位数；不要擅自换成“两个均值相除”还说同一个指标。CPU 时间与墙钟时间也分别记录，减少 CPU 不必然改善网络等待为主的接口延迟。

通用协议另外对完全相同的冻结 baseline 再测一次作为 sham 负控。若“原版对原版”也出现大漂移，环境不稳定，补丁收益就不该被轻易采纳。它还拒绝低到难以分辨的测量，避免几十微秒噪声被算成漂亮倍数。

Saber 的实际门槛是 CPU 比值中位数至少 1.10，且六组至少五组超过 1.05；通用项目门槛来自登记 manifest，还要求负控稳定。这是工程接受规则，不是普遍显著性定理。六组改善也不能排除所有系统性偏差，更不能拿合成输入替代真实业务分布。

## 9.12 实验答案与真实结果，都把适用范围写在旁边

做一页自己的实验记录，不需要启动模型：基线热点 70%，候选 10%，两次总 CPU 分别一秒与十秒。第一题答案是函数估计 CPU 从 0.7 秒到一秒，占比下降却不代表绝对耗时下降。第二题把单位改为 bytes，答案是停止 CPU 采样比较，先澄清测量对象。

第三题给六个教学比值 `1.20, 1.15, 1.18, 1.16, 1.19, 0.98`。中位数为 1.17，五组超过 1.05，满足前述 Saber 数值门槛；但若输出 hash 不一致，整体仍不应接受。若这是通用协议，还缺稳定的 sham 结果，不能偷用另一协议的结论。

已有 Go 环境时，可先读并选择运行 `investigation_test.go` 中的 `TestDiagnosisComparisonDoesNotInventCausality`，以及 `TestDiagnosisSourceRuntimeBindingRejectsReusedOrDifferentTarget`。真实 PG 的 stale baseline 测试需要独立测试库，不能把跳过当通过。本章的算术题与源码阅读不等于实际性能实验已执行。

项目已有收据显示，历史 Saber 修复在受控 FTS5 组件基准获得 CPU 中位 1.5587 倍，通用 Python 受控项目有 8.2122 倍结果；这些都不是完整应用或生产 P95/P99。历史 BOLT CPU 比为 0.9942，未观察到收益，也应照实保留。具体版本和证据应回看 `docs/CAPABILITY_GAPS.md` 与相应发布收据，不能把数值移植给新提交。

更重要的限制是，独立六轮因果评测修复后综合仅 2/6，十二次诊断执行成功没有自动变成可靠根因识别。人类独立语义指标尚未提交的部分保持未知。一个值得学习的项目，应当让你看到好结果、坏结果以及它们各自的分母；下一章就来练怎样挑测试、记证据和交付一项能复查的改动。
