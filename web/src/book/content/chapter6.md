## 采到了，为什么还没有结果

继续看那台 CPU 升高的服务。采集器已经把文件传到对象存储，任务也显示 `collection_status=COLLECTED`。如果此时页面就宣布“分析完成”，接下来下载到一份截断文件、遇到不支持的格式，或者解析进程被内存限制终止，用户都会面对一句无法解释的成功。采集确认的是原始制品已经登记，分析还要回答另一组问题：这份字节是不是登记的那份，能否解析，结果能否交给当前任务。

先把 Analyzer 当成一个接收文件的后台工人。它从数据库领取工作，把文件下载到自己的临时目录，交给解析子进程，再把结果上传回去。这里没有模型参与。把 folded 文本变成树、计算权重、检查文件摘要，都应该由确定的程序完成，不能让模型猜一个看起来合理的图。

本章跟踪 `worker.py` 中的 `run → process_job`，再走到 `repository.py` 的 `claim、renew、complete`。你读每一步时，都问两个问题：现在拿到的是什么；失败后留下了什么。暂时记不住所有配置名没关系，能指出“下载之前”和“发布之前”分别在保护什么，就已经能顺着链路排查。

这里还要保留案例的粒度。第 2 章本地 smoke 跑的是 `PROC_CPU`，测得进程和线程的 CPU tick 增量。以后接入经过授权的 `PYTHON_CPU`，才可能拿到 Python 函数栈。它们可以经过相似的分析框架，不能因此把线程结果解释成某个函数正在耗时。

## 先画出一个作业经过的七个地方

打开 `analyzer/drop_analyzer/worker.py`。`run` 先检查依赖是否就绪，然后调用 `repository.claim()`；没有可领作业就等待下一轮，有作业才进入 `process_job`。下面是帮助阅读的调用顺序，省去了计量、日志和异常分支，是伪代码，不是可以替换源文件的实现：

```text
run
  → Repository.claim                     领取并建立本轮身份
  → process_job / LeaseGuard             启动续租
  → input_artifact / storage.download    获取当前 RAW 并核验
  → pipeline.validate / analyze_isolated
  → make_result / build_outputs          生成公开结果和制品
  → storage.upload                      先上传所有输出
  → Repository.complete                 最后发布数据库引用
```

为什么不是直接扫描对象存储找新文件？因为“存着一份文件”和“当前任务需要处理这份文件”并不相同。同一个 task 可以有多轮采集，旧文件仍需要保留供审计。作业把 task、采集 attempt 和 pipeline 绑在一起，Analyzer 通过这份关系找输入，不能挑一个名字相似、修改时间较新的对象代替。

`Job` 里有两个容易混淆的数字：`collection_attempt` 是第几次采集，`attempt` 是这个分析作业第几次被领取执行。重新采样会换输入身份；下载暂时失败后的分析重试，仍可能处理同一份 RAW。排障时同时记下 task、采集 attempt、analysis job 和分析 attempt，才能知道日志中的两次“开始”是不是在争同一份工作。

这七步也给了我们一个阅读顺序。先读调用点，理解数据从哪里来、到哪里去，再钻进下载器和格式适配器。若一开始就研究 HPROF 每种记录的二进制布局，很容易把精力花在一种格式上，却还不知道结果为什么不能落库。

## 两个 Worker 为什么不能一起领走一行

假设启动了两个 Analyzer。最朴素的实现是先查询一条 pending 作业，再把它改成 running。两次查询可能恰好都看见同一行，随后两个进程各自开始下载和解析。查询时觉得“它还没开始”，不代表更新时仍然如此，所以选择与领取必须在一个事务边界内完成。

`repository.py / Repository.claim` 使用 `FOR UPDATE OF j,t SKIP LOCKED`。可以这样理解：准备领取时先锁住这项工作以及关联任务；另一位工人看见它被锁住就跳过，去找别的工作，而不是排队争同一行。这提高的是多作业并发能力，不意味着同一作业可以并行执行。

领取条件不只有 `status='pending'`。pending 或 retry 要已经到达 `available_at`；running 则必须租约过期，才允许别人接手。还要有剩余尝试次数、采集状态为 COLLECTED、分析状态仍允许处理，而且不能存在更新的采集 attempt。优先级和创建时间负责决定先拿哪项工作，资格条件负责决定能不能拿。

领取成功后，事务一起增加分析 attempt、写入 owner 和租约期限、把任务分析轴改成 RUNNING，并写 `ANALYSIS_STARTED`。这样页面不会看到一个开始事件，数据库却仍认为没有人领取。最后一次尝试已经耗尽而租约又过期的作业，也会被明确转为失败，避免永久挂在 running。

读这段 SQL 时，可以先把资格条件翻译成下面四列。它们是对真实条件的解释，不是新的数据库字段：

| 检查对象       | 要回答的问题         | 少了它会怎样          | 对照入口                 |
| -------------- | -------------------- | --------------------- | ------------------------ |
| 作业状态与时间 | 现在轮到它执行了吗   | 过早执行退避中的重试  | claim 的 available_at    |
| 剩余尝试次数   | 还能再处理一轮吗     | 坏输入反复消耗资源    | attempt 与 max_attempts  |
| 任务双状态     | 当前还需要分析吗     | 终态工作再次被领取    | collection/analysis 条件 |
| 最新采集轮次   | 输入仍代表当前任务吗 | 旧 RAW 覆盖新一轮结果 | NOT EXISTS newer         |

自己解释每一行，比记住整条查询更有用。以后新增队列筛选时，也能区分“决定先处理谁”的调度条件和“这个结果还能不能属于当前任务”的正确性条件，避免优化队列时删掉输入身份保护。

## 工作证会过期：A 卡住，B 接手，A 又醒了

行锁只管领取事务，解析文件时不能一直锁着数据库。于是系统给 Worker 一张会过期的工作证，也就是 lease。默认配置是 60 秒租约、10 秒续约；实际运行要看配置。正常情况下，后台线程不断延长有效期。进程崩溃、数据库失联或长期卡顿时，工作证会自然失效，别的 Worker 才有机会接手。

看一条教学时间线，字母和时刻都是假设，不是现场测试记录：

| 时刻 | 数据库中的事实                   | 谁还能发布 |
| ---- | -------------------------------- | ---------- |
| T0   | A 领取，分析 attempt=1，租约有效 | A          |
| T1   | A 停顿，租约到期                 | 暂时无人   |
| T2   | B 重新领取，分析 attempt=2       | B          |
| T3   | A 恢复并完成旧文件解析           | 仍然只有 B |

要拦住 T3 的 A，不能只检查“这个 job 存在”。`renew` 同时要求 owner、分析 attempt、running 状态和有效租约匹配，还要确认任务仍指向当前采集尝试。`LeaseGuard.check` 的内存标记让下载和解析尽早停工，真正落库时的 SQL 才决定有没有资格发布。

这也解释了一个看似多余的动作：明明刚续租成功，`complete` 为什么还检查？因为“刚才有权限”到“现在写结果”之间仍可能停顿。`_lock_owned` 先锁任务，再检查当前采集 attempt 和作业归属；写入后还会复核实时租约。锁保证大家按一致顺序处理共享记录，条件保证过期执行不能靠完成得晚夺回工作。

## 先限制下载，再检查 SHA

现在进入 `process_job`。它先读取当前 attempt 的 RAW 元数据，必要时再取辅助 ELF。元数据给出对象 key、登记大小和 SHA-256。key 是地址，摘要是核对字节的依据；两者都不能代替另一项。程序要知道从哪里取，也要知道取回来的内容是否与登记值相符。

容易犯的错误是先 `read()` 全部内容，再检查文件是不是过大。一个异常对象可能已经撑满内存。实际实现先检查每个输入的登记大小，再检查 RAW 与附件的合计下载预算；逐块写入前还检查临时目录预算和剩余空间。任务 budget 通过 `min(value, maximum)` 收紧平台上限，不能让请求自己申请无限资源。

来源：`analyzer/drop_analyzer/worker.py / process_job`。下面是下载后的实际完整性条件节选：

```python
if (
    len(raw) != artifact["size"]
    or hashlib.sha256(raw).hexdigest() != artifact["sha256"].lower()
):
    raise AnalysisError(
        "ARTIFACT_INTEGRITY_ERROR",
        "Raw artifact size or SHA-256 does not match metadata",
    )
```

大小一致仍可能字节不同，摘要一致也仍需要防止下载过大，所以检查要配合使用。SHA 只能证明“与登记内容相符”，不能独自证明采集目标真实、时间窗口正确，更不能证明原始文件中的每条观测可靠。这些来源信息要从当前采集记录和 manifest 继续追溯。

## 解析放到另一个进程，是为了能把它停下来

完整性通过以后，文件仍可能包含极深调用栈、损坏压缩流或让原生解码工具消耗大量资源的数据。若直接在常驻 Worker 中解析，一个坏输入可能连带失去续租、健康检查和下一项工作。`isolate.py / analyze_isolated` 为本次输入创建一次性子进程，父进程负责监控和收尾。

子进程只继承筛选过的系统环境，临时目录、HOME 和工作目录指向本次 job；数据库和对象存储凭据留在父进程。这样解析器不需要为了读文件而拥有上传其他对象或修改任务的能力。进程分离先减少误用范围，但不应把“新开了一个进程”直接理解为完整安全沙箱。

父进程定期检查租约、墙钟、进程组 CPU、进程数、响应大小和磁盘。POSIX 上异常收尾会终止本次进程组，避免主解析器退出后，它启动的外部工具仍继续工作。轮询间隔为 50 毫秒，表示周期性观察；两次观察之间仍有窗口，底层容器与系统资源约束继续承担更硬的限制。

这层代码值得按一个故障推演：解析器已经生成一半响应，Worker 失去租约。父进程应该停止本次工作，部分响应不能被当成完整结果提交。代码中的部分分析检查点另有结构、摘要与归属校验，它可以表达“已经完成哪些解析”，不能把 analysis_status 提前升级为 SUCCESS。

## 用三行合成 folded，亲手算出一棵树

下面的三行是**合成教学夹具**，没有来自真实业务采样，也不包含时戳。分号把根到叶的路径隔开，最后的数字是这条路径的权重：

```text
main;render;decode 6
main;render 3
main;idle 1
```

拿第一行做一次手算。总根增加 6，main 增加 6，render 增加 6，decode 增加 6；只有叶节点 decode 的 self 增加 6。第二行再让 main 和 render 的 total 各增加 3，同时给 render 的 self 增加 3。第三行增加 idle 路径。最后 main 的 total 是 10、self 是 0；render 的 total 是 9、self 是 3；decode 的 total 和 self 都是 6。

在已经装好 Analyzer 依赖的环境，进入 `analyzer`，把下面代码保存为临时的 `lesson_folded.py` 后执行 `python lesson_folded.py`。此练习只调用本地纯解析函数，不启动 Worker，不连接数据库或对象存储。

```python
# Synthetic teaching fixture; not an imported production profile.
from drop_analyzer.folded import analyze_folded

raw = b"main;render;decode 6\nmain;render 3\nmain;idle 1\n"
parsed = analyze_folded(raw)
print(parsed["metadata"]["sample_count"])
print([(row["name"], row["self"], row["inclusive"])
       for row in parsed["top"]["self"]])
```

预期第一行打印 10，第二行依次是 decode 的 `(6,6)`、render 的 `(3,9)`、idle 的 `(1,1)` 和 main 的 `(0,10)`。这些预期来自手算，不是本教程已经执行过的收据。对照 `folded.py / analyze_folded` 的 `parent["self"] += count`，你会发现 self 为什么只记在最后一层。

## 实验：不用后端，自己把三行数据折成树

如果刚才的项目函数让你觉得“只是调用了一个黑盒”，再做一个只依赖 Python 标准库的实验。把下面完整代码存成任意临时目录中的 `folded_tree_lesson.py`，执行 `python folded_tree_lesson.py`。它只处理代码中固定的合成数据，不访问项目配置。不要把这段教学解析器直接放进服务接收用户文件，它刻意省去了生产实现的限额与异常处理。

```python
# Synthetic teaching fixture; no production measurement is represented.
raw = "main;render;decode 6\nmain;render 3\nmain;idle 1\n"

def node(name):
    return {"name": name, "total": 0, "self": 0, "children": {}}

root = node("all")
for line in raw.splitlines():
    path, weight_text = line.rsplit(" ", 1)
    weight = int(weight_text)
    root["total"] += weight
    current = root
    for name in path.split(";"):
        current = current["children"].setdefault(name, node(name))
        current["total"] += weight
    current["self"] += weight

def show(current, depth=0):
    print("  " * depth + current["name"]
          + f" total={current['total']} self={current['self']}")
    for name in sorted(current["children"]):
        show(current["children"][name], depth + 1)

show(root)
```

这段程序的预期标准输出完整如下，缩进用两个空格表示一层：

```text
all total=10 self=0
  main total=10 self=0
    idle total=1 self=1
    render total=9 self=3
      decode total=6 self=6
```

别急着往下读，先删掉输入第二行，自己预测变化：总计应该变成 7，render 的 total 变成 6、self 变成 0，decode 仍是 6。再把第二行权重改成 5，render 的 total 变成 11、self 变成 5，整棵树总计变成 12。你会看到，树宽来自累计权重，而不是函数名字、出现顺序或者行数。

接着把 `current["self"] += weight` 故意挪进遍历每个栈帧的循环。main 会同时拥有非零 self 和同样大的子节点，`total = self + children total` 被破坏。这就是一个能检验理解的坏改动：程序仍能打印很漂亮的树，数学关系却已经错了。真实 `validateFlameTree` 正是要拒绝这类输入，而不是相信上游总能算对。

最后比较教学代码与 `folded.py`。生产实现多出来的 UTF-8 检查、正权重、空帧拒绝、行长、栈深、节点数和 JSON 安全整数上限，各自防的是不同输入。只加一个“文件不能太大”挡不住很深的单行，也挡不住总权重超过浏览器精度。学会手算以后，再看这些限制就能知道它们守住的具体对象。

## 同一棵树的外形，不保证同一种测量

解析器能把很多输入转成树，但不能把它们都叫 CPU 样本。`PROC_CPU` 的 `cpu_ticks` 是线程计数增量；Python CPU 的 folded 通常承载栈采样；堆分析可能用 bytes；I/O 结果还带自己的 section。图形组件可以复用，单位和解释必须跟着结果一起保留。

`make_result` 把 `kind、summary.unit、summary.source、input_format` 和 limitations 放进公开结果。对于导入数据，平台现场采集的时长和频率保持未知；文件自带的工具元数据可以保留，却不能因此改称“平台刚在该主机验证过”。未知丢样也不能填零，零会变成一个过强的测量结论。

递归还带来一个细节。路径 `main;walk;walk 4` 在火焰树中保留两个 walk 栈帧，才能表示调用深度；函数 inclusive TopN 则对一条采样里的同名函数只累计一次，避免把同一份权重算成两份。读树时看位置，读按函数聚合的表时看聚合规则，两者回答的问题不同。

如果 render 占比很高，我们目前只能说“这份输入中的 render 路径承载较多权重”。是否拖慢接口，还需要请求耗时、并发、负载和对应时间窗口。即使后面规则给出建议，它也只是进一步调查的方向，不会把一个比例自动变成因果根因。

## HPROF 里的 retained，到底“留住”了什么

把 CPU 案例暂时换成 Java 内存问题。某个缓存对象自己只有很少字段，却指向一整批联系人摘要。只看对象自己的大小会低估它的影响。`hprof.py / analyze_hprof` 先解析对象、字段引用和 GC roots，再构建强引用图，分析从根到各对象的路径。

用一个合成图理解支配关系：root 只能经过 cache 到达一块数组，那么所有到数组的路径都经过 cache，cache 就支配这块数组。cache 的 retained 权重会包含它。若 root 还能经过另一个独立对象到达数组，去掉 cache 后数组仍可达，那块数组就不能全部算作 cache 独占保留的内容。

实现通过 SQLite 索引保存解析阶段，再用直接支配节点算法计算支配树，沿树累计 retained。索引按输入摘要绑定，能恢复的是同一份输入的工作进度。这里值得找两个位置：`notify("dominator")` 前的强引用图，以及 `retained[dominator[node]] += retained[node]` 的累计方向。

这不是 JVM 堆占用的逐字节真值。结果明确采用 HPROF 字段和数组载荷权重，不含 VM 对象头、对齐及压缩引用差异；`Reference.referent` 不作为强引用。不同类的 retained 可能重叠，不能随意相加。一个大 retention candidate 也还不是泄漏结论，是否持续增长、是否符合生命周期，要靠跨时间堆对比继续检验。

## 对象先上传，数据库最后发布

解析完成后，`make_result` 生成公开结构，`build_outputs` 组装 `flamegraph.json.gz、top.json、report.json、manifest.json`。Worker 逐个上传输出，全部成功才调用 `Repository.complete`。complete 在一个数据库事务里登记制品、规则建议、job/task 状态和事件，页面才能获得一致的成功记录。

反过来先写 SUCCESS 再上传，会出现“数据库说有图，对象却不存在”的坏状态。现在的顺序接受另一种较容易收拾的情况：对象上传了，但数据库发布没有成功，会留下没有被引用的对象。MinIO 和 PostgreSQL 没有因为这一段代码变成分布式事务，清理策略仍要处理孤立对象。

| 故障位置                | 可能留下什么             | 可以说分析成功吗       |
| ----------------------- | ------------------------ | ---------------------- |
| RAW 下载或摘要核验失败  | 原始登记与失败证据       | 不可以                 |
| 输出上传到一半失败      | 部分未引用对象           | 不可以                 |
| 全部上传后数据库不可用  | 完整未引用对象           | 不可以                 |
| 发布时发现 owner 已过期 | 旧分析轮次的对象         | 不可以                 |
| complete 事务提交成功   | 制品引用、结果与成功事件 | 可以，仍按结果局限解释 |

输出 key 同时含采集轮次和分析轮次，所以旧 A 上传的对象也不会覆盖新 B 的文件。数据库不可用时，外层失败写入也可能失败；`run` 会保留租约到期后的恢复路径，而不是在内存里伪造一个终态。恢复处理的是执行资格和未完成工作，并不保证所有故障都无需人工排查。

## 让错误输入停在正确的位置

现在回到本地合成练习。把输入改成 `main 0`，它应该报 `INVALID_INPUT`，因为权重必须为正。再试带负号的权重、非法 UTF-8 或超过深度上限的路径。重点不是背错误文案，而是预测哪个边界应先拒绝；未知异常不应让原始 profile 正文泄进日志。

进入 `analyzer` 后，可以运行已有回归，命令仍是练习入口，本章没有执行这些后端测试：

```powershell
python -m unittest discover -s tests -p test_folded.py -v
python -m unittest discover -s tests -p test_worker.py -v
python -m unittest discover -s tests -p test_hprof_native.py -v
```

读 `test_worker.py` 时找三种行为：合计下载量超限，下载不应开始；存储上传失败，complete 不应发布；owner 失效，即使已经上传也不能提交。读数据库租约测试还要看 `test_repository_integration.py` 的环境要求，使用可销毁的独立测试数据库。缺条件而跳过，不能记录为真实事务验证通过。

最后回答三个问题。A 曾经续租成功，为什么仍不能保证发布？因为资格会在停顿期间过期。RAW 已采到但解析失败，要不要重采？先检查原始输入与解析错误，完整 RAW 可能只需重分析。retained 很大能不能直接说泄漏？不能，它说明这份快照中的保留关系，还缺时间变化与生命周期证据。能把答案沿函数调用和数据关系讲出来，再进入前端阅读会轻松很多。
