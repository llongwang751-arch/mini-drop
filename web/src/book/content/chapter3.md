## 一次跨语言交接，最怕各自理解得都很合理

前端认为频率是“每秒采几次”，后端把它当成采集总次数；采集器输出 memray，分析器按 folded 读；页面把一个大整数 ID 转成普通数字，查询时已经不是原来的任务。每个模块都可能正常编译，整条链却无法解释。

契约就是把这些含义提前写清楚：哪些字段存在、单位是什么、缺省时怎样处理、谁有权设置、什么组合合法。它包括机器能检查的 schema、协议和 SQL，也包括需要人协调的兼容性规则。读契约的目的，是在动代码之前知道影响会沿哪条线传出去。

本章继续使用联系人服务。我们希望采 Python CPU，但先把 `PROC_CPU` 与 `PYTHON_CPU` 并排放好。你会看到同一种 folded 语法，怎样承载精度不同的观察；再把一个参数从表单追到 Go、C++ 和 Python。最后做一次纸面需求变更，练习在落笔前找全参与方。

## 四份真源，各回答一类问题

项目约定的权威来源是 `contracts/task-kinds.json`、`contracts/drop.proto`、SQL migrations 和 `docs/CONTRACT.md`。它们相互配合，不能只看某一份就宣布支持完整能力。

| 来源            | 主要回答                     | 阅读时要找什么                              |
| --------------- | ---------------------------- | ------------------------------------------- |
| task-kinds.json | 某种任务允许怎样采、怎样解析 | 参数 schema、工具、输入格式、pipeline、限制 |
| drop.proto      | Go 与 C++ 之间怎样交接       | 消息、字段编号、执行身份、上传授权          |
| db/migrations   | 哪些状态和关系能持久保存     | CHECK、唯一约束、外键、租约与历史结构       |
| CONTRACT.md     | 对外与跨服务行为怎样约定     | HTTP、权限、幂等、失败与兼容性              |

实现仍要回到代码核对。文档中说存在某个接口，不等于你的当前镜像就实现了它；历史迁移的初始角色列表，也不一定等于后续迁移后的完整角色集合。把“当前源码”“当前运行版本”“某次历史验收”分别记录，才不会让契约阅读变成版本猜谜。

修改时先找最靠近含义的真源。新增任务参数先改类型目录和对应实现；改变持久状态要考虑 SQL；调整 gRPC 消息要考虑编号与旧节点；对外行为变化还要更新契约说明。前端复制一份常量不能代替这些工作。

## PROC_CPU 和 PYTHON_CPU：格式相同，测量不同

下面是 `contracts/task-kinds.json` 两项定义的实际字段节选，省略其他字段，不能当作可直接替换的完整目录：

```json
[
  {
    "id": "PROC_CPU",
    "runner": "procfs",
    "analysis_pipeline": "folded_v1",
    "input_format": "folded",
    "raw_name": "profile.folded",
    "default_duration_seconds": 10,
    "default_frequency_hz": 10
  },
  {
    "id": "PYTHON_CPU",
    "runner": "py-spy",
    "analysis_pipeline": "python_cpu_v1",
    "input_format": "folded",
    "raw_name": "python.folded",
    "default_duration_seconds": 10,
    "default_frequency_hz": 99
  }
]
```

`id` 选择业务类型，`runner` 说明采集实现，`analysis_pipeline` 选择分析适配器。`input_format` 告诉分析器怎样解释原始字节，`raw_name` 则固定制品名。默认时长一样，并不表示实际任务一定采了十秒：调用方可以给出范围内的值，也可能使用离线导入。

完整目录里，PROC_CPU 频率范围是 1–10 Hz，PYTHON_CPU 是 1–199 Hz，最大时长都是 60 秒。前者读取线程 CPU tick 差，后者进行 Python 栈采样。folded 只描述“路径加权重”的形式；权重从哪里来，仍要看采集来源和分析语义。

这解释了一个界面问题：同样能画树，PROC_CPU 页面也不能写成“Python 函数耗时分布”。契约不只限制输入，也限定结果允许怎样被命名和解释。

## 请求先归一化，再检查它是否合法

`api/internal/domain/task.go:CreateTask.Normalize` 给缺省时长和频率填目录默认值，统一 scope 与 target 的表达。这里的顺序影响幂等：同一个意图，如果一份请求省略默认值，另一份显式填写默认值，规范化后才能用一致方式计算摘要。

接着 `CreateTask.Validate` 检查类型、字段范围、导入组合等规则，`ValidateKindParameters` 使用该类型的 schema。`api/internal/domain/kinds.go` 会在加载目录时编译 schema，实际校验核心如下：

```go
if k.Compiled != nil {
    if err := k.Compiled.Validate(value); err != nil {
        return Fail("INVALID_PARAMETERS", "参数不符合TaskKind schema")
    }
}
```

`k.Compiled` 是该类型已经编译的规则；`value` 是参数对象；不符合时返回稳定的领域错误。它没有去问数据库“用户能不能操作节点”。因此，格式校验通过之后，service 还要检查角色、组、Agent 在线状态、能力和资源范围。

“能解析 JSON”“符合 schema”“这个人可以做”“这台机器实际上能做”，是逐层收紧的四个问题。把它们压成一个通用校验函数，往往会让错误含义和权限边界变得模糊。

## 从 Go 参数到 TaskDesc，信息怎样保持一致

后台派发并非照着浏览器请求原样转发。`api/internal/service/tasks.go:dispatch` 读取已持久化的 task 和 attempt，签发当前对象的短期 PUT URL，建立 `TaskDesc`；随后调用 `dispatch_v2.go:enrichDispatch` 补上目录和目标信息。

后一个函数里有这段实际代码：

```go
desc.ProtocolVersion = 2
desc.RawFormat = k.InputFormat
desc.RawName = k.RawName
desc.RawContentType = k.RawContentType
desc.AnalysisPipeline = k.Pipeline
desc.MaxFileBytes = k.MaxFileBytes
```

第一行标明派发协议，接下来各行把目录定义落实到这一轮任务。格式、文件名、内容类型与分析 pipeline 不由浏览器自行搭配；预算还会进一步收紧允许大小，不能靠客户端传一个更大的数字突破目录上限。

同一函数把 target 转成 `TargetDesc`，把校验后的 options 编码为 `ExtraParametersJson`，并为 Python 类型设置 PythonProfiler payload。这里仍然没有任意 shell 命令。最后上传授权绑定到当前 task/attempt 目录，制品归属在执行前就确定下来。

这层转换值得单独阅读：它是“用户请求”变成“机器执行描述”的边界。看见一个字段没出现在命令行时，先检查它是否用于身份、预算或分析，而不要立刻认为它被漏掉。

## Protobuf 的数字为什么不能随手调整

`contracts/drop.proto:TaskDesc` 中，下面是保留原编号的实际字段节选，中间字段在此省略：

```proto
int64 task_id = 1;
int64 attempt_id = 2;
string kind = 3;
int64 deadline_unix_ms = 6;
string raw_upload_url = 7;
string raw_object_key = 8;
TargetDesc target = 11;
ResourceBudget budget = 12;
string extra_parameters_json = 13;
string raw_format = 14;
string analysis_pipeline = 17;
```

编号用于编码识别字段，不能因为想把 target 排在前面就重新编号。`task_id` 标记业务记录，`attempt_id` 标记数据库中的执行身份，两者一起参与结果与取消校验。`deadline_unix_ms` 的单位是 Unix 毫秒；它不是“还允许采几毫秒”的相对时长。

`raw_object_key` 是稳定的对象位置，`raw_upload_url` 是带时效的操作授权。把 URL 当日志关联键既不稳定，也容易泄露授权。`extra_parameters_json` 提供有限选项扩展，并不意味着可以绕过 schema 向里面塞任意 argv。

兼容性还涉及缺省值。Proto3 普通标量缺省时通常读到零值，消费者需要知道“零表示缺省”是否可接受。如果新能力必须依赖一个旧节点从未理解的字段，就需要能力或版本协商，不能只因消息能解码而派发。新增字段使用未占用编号，旧编号也不能换含义。

## C++ 只把受控选项变成固定工具调用

`core/src/runner.cpp:build_runner_argv` 的 PYTHON_CPU 分支有以下实际节选：

```cpp
std::vector<std::string> result{
    tool("py-spy"), "record",
    "--format",     "raw",
    "--output",     output,
    "--pid",        pid,
    "--duration",   std::to_string(c.duration_seconds),
    "--rate",       std::to_string(c.frequency_hz)};
if (c.options.value("native", false))
  result.push_back("--native");
return result;
```

`tool("py-spy")` 取受检查工具的路径；固定字符串决定执行哪个子命令；output 由任务目录生成；pid 来自已核实的目标。时长和频率从类型化上下文转成工具参数，native 是允许的布尔选项。参数最终由 `core/src/process.cpp:execute` 作为 argv 交给 execve。

这能避免把用户字符串拼成 shell 语句，但安全并不止于“用了数组”。路径、工具自身的参数语义、进程预算和目标身份仍要检查。比如工具支持读取某个脚本文件，也不能因此让用户任意指定脚本。已有 SCRIPT 类型使用受控模板，不是通用远程命令入口。

实际执行前后，Agent 还会核对 PID 启动时刻、UID、namespace 等信息。平台有资源权限，不能让已经复用的 PID 自动继承原目标的采集资格。这部分会在 [第 5 章](/book/chapter5/) 展开。

## Python 按 kind、pipeline、format 三者一起检查

采集完成后，Server 登记 RAW，并建立分析 job。`analyzer/drop_analyzer/worker.py:process_job` 根据 job 的 pipeline 找适配器，核对下载输入与预算，再进入隔离分析流程。分析器不能仅看文件扩展名就猜格式。

`analyzer/drop_analyzer/registry.py:Pipeline.validate` 的实际判断是：

```python
if context["kind"] not in self.kinds or context["format"] not in self.formats:
    raise AnalysisError(
        "UNSUPPORTED_FORMAT", "Task kind, pipeline and raw format do not match"
    )
```

每个适配器声明接受哪些任务类型和格式；两者任一不匹配就拒绝。后续还检查文件存在、大小等边界。这样可以挡住“标签叫 Python CPU，实际却交了一份不相容格式”的组合错误。

即使进入 folded 分支，代码仍会根据可信来源决定使用 py-spy 专用处理还是通用 folded 解析。来源有影响，不能随便伪造一个标记来获得更强解释。成功解析后再生成结果，并不表示文件里的每个函数名都是真实业务，更不表示模型推断已经被证实。

这里再次用到契约的两层作用：先保证字节可以按所选规则处理，再保留这些字节代表什么测量。后一层决定页面和诊断应该怎样描述结果。

## React 读取规则，让错误尽早出现

类型目录经 API 暴露给前端，`web/src/task-schema.ts:fieldDefinitions` 从 schema 提取类型专属选项。实际入口节选是：

```ts
const root = kind?.parameter_schema?.properties || kind?.parameters || {};
const properties = root.options?.properties || root;
```

第一行优先使用新 schema 的属性，也保留旧参数表达的兼容入口；第二行优先展开 options。PID、target、budget 等通用部分由专门组件处理，避免同一个参数在不同位置输入两遍。`initialOptions` 读取默认值，`validateOptions` 提前检查类型、枚举和范围。

前端检查的价值是立即反馈，不必等用户提交后才知道频率超界。它不承担最终授权，也不能取代 Go schema：浏览器请求可被手工构造，表单之外的客户端也存在。

再看反向传播：Analyzer 结果里的单位和来源必须传到展示；task 的权限由服务端计算，按钮还要结合状态决定是否可用。契约贯穿输入和输出，React 不是一张可以随意重新解释后台字段的贴图层。

## 数据的单位和来历，要跟着制品一起走

`duration_seconds` 用秒，`frequency_hz` 是每秒次数，`max_file_bytes` 是字节，`start_time_ticks` 是 Linux 启动时刻 tick，`deadline_unix_ms` 是毫秒时间戳。名字相似也不能直接换算：启动 tick 不是 Unix 时间；采样频率乘时长也不保证一定得到那个数量的有效样本。

对外 REST 的 bigint ID 和事件序号使用十进制字符串，避免 JavaScript 数值精度丢失。界面可以格式化显示时间，但记录与排序仍应遵循约定；不能拿本地显示文字当成统一时间格式保存。

一份结果的来历至少需要关联 task、attempt、RAW 的对象 key/大小/SHA、采集来源、分析版本与对应执行。SHA 表示登记和读取的字节是否一致，不能证明采集对象选对了，也不能证明解释正确。manifest 补充版本与采集信息，仍要和任务身份一起读。

离线导入尤其要分清。17 种非 COMPOSITE 类型支持通用导入，导入路径不会派发 Agent；服务端写 `params.source=import`，清除现场目标、时长和频率，结果保留导入标记。文件内部出现时间和频率，也不能变成平台现场采集的承诺。教学 folded 样例只能说明解析和展示行为。

## 纸面变更：加一个“有效样本下限”需要改哪里

现在提出一个**尚未实现的教学需求**：给 PYTHON_CPU 增加可选 `options.min_samples`，低于该下限时明确报告质量不足。这个字段不是现有能力，不要把它提交给当前 API，也不要直接照抄下表改生产契约。

先讨论语义：它统计哪一种有效样本，重复或被过滤样本算不算，省略时保持什么旧行为，对导入数据是否适用？这些没有定下来，“添加一个整数输入框”并不能完成需求。

| 层              | 纸面上要讨论的变更           | 必须保护的行为                        |
| --------------- | ---------------------------- | ------------------------------------- |
| TaskKind schema | 正整数、上限、可选性与描述   | 旧请求省略后行为不变，非法类型拒绝    |
| Go 校验与幂等   | 归一化方式、选项持久化       | 不同阈值请求不能混成同一个意图        |
| Proto/派发      | 是否由已有 options JSON 承载 | 不乱加重复字段，不改变已有编号        |
| C++             | 是否影响采集或只透传语义     | 不把分析阈值误变成未经支持的工具 argv |
| Python          | 有效样本定义和失败/限制表达  | 阈值真正被执行，不能只在报告里显示    |
| React           | 输入说明、错误和质量结果     | 数字单位清楚，低质量不画成已验证根因  |
| 发布协作        | 消费者版本与旧数据兼容       | 旧 Analyzer 不能静默忽略新保证        |

已有 options 可以承载字段，不意味着新语义自动兼容。尤其“必须满足下限”是一项新保证，旧分析器如果不检查就返回成功，会制造看似可用的错误结果。发布策略必须在实现前约定；需要什么能力检查，也要明确设计，不能假定当前系统已有。

## 为这次变更设计回归，再回到源码

给纸面需求列五个输入：省略下限、合法下限、字符串、超界数，以及恰好等于阈值的样本。再加上现场 py-spy 来源与明确标记的导入样例，检查它们是否拥有相同或不同的质量语义。测试要验证用户可见保证，不是机械重复内部赋值。

应有的回归还包括旧请求保持行为、前后端边界一致、结果来源不变、幂等冲突可解释，以及旧消费者不会假装执行新约束。涉及解析时，用标明“导入/合成”的夹具测试边界；要宣称真实采集能力，另做有目标、工具和运行条件的集成验证。纸面练习本身不算测试通过。

最后回答三个自测。**为什么表单允许输入仍可能被 Go 拒绝？** 前端只是提前反馈，后端独立校验当前契约与权限。**为什么两个 folded 文件不能任意互换解释？** 相同语法不保证测量来源、单位和精度相同。**新增字段为何不能只看新服务？** 交接链里可能仍有旧 Agent 或 Analyzer，能读消息不等于理解新增保证。

带着这种变更思路进入 [第 4 章：点下创建以后，Go 做了什么](/book/chapter4/)。我们会进一步看：输入已经合法，为什么任务、执行、事件和派发待办还必须一起提交。
