## 这一章要亲手交出什么

前面已经用过 folded 文件，也看过 Analyzer 的完整链路。这次先把租约、对象存储和页面都放在旁边，从零写出那个最核心的小变换：输入几行带权调用路径，输出一棵权重守恒的树，再给每个函数算出 self 与 inclusive。做完以后，你应该能用笔检查结果，而不是只相信程序没有报错。

前置条件是知道 Python 字典、循环和函数，会运行本地命令。实际练习文件是 `tutorials/labs/analysis.py`，入口为 `parse_folded`；配套行为回归在 `tutorials/labs/test_analysis.py`。它们只使用 Python 标准库和课程自己的 check helper，不访问数据库、网络、采集进程或模型。所有输入都明确是合成教学夹具。

这一点尤其重要：我们仍然围绕“联系人接口 CPU 升高”学习，但这里的 decode、render 和数字都是为了手算而取的名字与权重。执行解析器不会让这些数字变成真实 Python 测量。mini-drop 的受控自采集则会产出 cpu_ticks，也不是函数栈，不能拿它的线程计数填进这个函数案例冒充现场热点。

本章验收有四件事：普通输入能合并共同前缀，递归函数不会被重复计费，坏输入在明确边界失败，展示树与函数表分别保持自己的含义。最后再把这几个小函数映射回生产 Analyzer，理解真正复杂的地方为什么出现在解析之前和发布之后。

## 先规定一行到底表示什么

本课使用的 folded 行由路径、一个分隔空格和正十进制整数构成。例如 `main;render;decode 6` 表示这条根到叶路径承载六份权重，不能读成 decode 调用了六次，更不能自动读成六毫秒。分号分隔栈帧，最后的数字与路径之间用最后一个空格分开，因为函数名本身可能含有空格。

先不要写代码，列出几种会让程序误解的输入：没有数字、数字带负号、空函数名、非法 UTF-8、特别深的一行、总权重大到浏览器无法精确表示。格式约定不是一张好看的文档，它决定解析器什么时候应拒绝输入，以及下游能依赖哪些事实。

课程解析器采取明确的小规模上限：原始输入最多 32 KiB，最多 128 条非空路径，栈深最多 16，节点最多 256，单帧名字最多 256 字节，单行最多 4096 字节。这是为了让机制实验容易观察，不能拿这些数字覆盖生产 `folded.py / Limits`。生产文件有自己的资源与格式契约。

这里允许空行，是为了让末尾换行或编辑器保存习惯不造成失败；不允许 `main;;decode`，因为中间有一个没有名字的栈帧。允许函数名中的普通空格，不允许控制字符。每一种宽容与拒绝都应该能解释用途，而不是用一个很宽的 split 加 try/except 把所有异常都当成空结果。

## 三条路径，先用笔折出共同前缀

把下面内容写在纸上，这是合成数据，没有现场时间窗：

```text
main;render;decode 6
main;render 3
main;idle 1
```

第一条路径经过 all、main、render 和 decode，所以这四处 total 都加六；只有最后停下来的 decode，其 self 加六。第二条和第一条共享 main 与 render，不应创建两个互不相关的 render 节点；它让这些节点 total 再加三，并让 render 自己的 self 加三。第三条在 main 下增加 idle，total 与 self 都为一。

最后 all 与 main 的 total 都是十，main 的 self 为零；render 的 total 为九、self 为三；decode 为六与六；idle 为一与一。你现在已经拥有一个独立于实现的预期。如果稍后程序给出 render 的 self 为九，问题在“自身”记到了路径每一层，而不是测试期望需要跟着程序修改。

共同前缀决定树的形状：每个父节点下面，用函数名作为字典键查找或建立子节点；换了父节点，即使名字相同也可能是另一个栈位置。因此 `main;left;decode` 和 `main;right;decode` 要保留两处 decode，不能一开始就按名字合并成一个全局树节点。函数表可以聚合，调用树必须先保留路径。

## 第一个完整函数：只负责建立树

先在临时目录创建 `folded_step.py`。下面函数是可以直接执行的教学实现，输入只面向本章固定的小夹具；后面的严格课程模块会加完整边界，不要把这版函数作为面向外部文件的接口。

```python
def build_tree(text):
    root = {"name": "all", "value": 0, "self": 0, "children": {}}
    for line in text.splitlines():
        path, count_text = line.rsplit(" ", 1)
        count = int(count_text)
        current = root
        current["value"] += count
        for name in path.split(";"):
            children = current["children"]
            if name not in children:
                children[name] = {
                    "name": name, "value": 0, "self": 0, "children": {}
                }
            current = children[name]
            current["value"] += count
        current["self"] += count
    return root


def show(node, depth=0):
    print("  " * depth + node["name"]
          + f" total={node['value']} self={node['self']}")
    for name in sorted(node["children"]):
        show(node["children"][name], depth + 1)


show(build_tree("main;render;decode 6\nmain;render 3\nmain;idle 1"))
```

执行 `python folded_step.py` 的预期标准输出如下。排序只让文本稳定，idle 排在 render 前面不表示它先执行：

```text
all total=10 self=0
  main total=10 self=0
    idle total=1 self=1
    render total=9 self=3
      decode total=6 self=6
```

读函数时注意 current 的移动。每经过一个栈帧，它就指向该帧的节点，因此循环结束后 current 才是叶节点。把 self 更新留在循环外，是算法含义的一部分。若你只能背出代码，却不能说明这一行为什么放在这里，先删掉第二条输入，再手算一次变化。

## 用一个错误改动检验你是否真的会看树

现在故意把 `current["self"] += count` 移到遍历每一帧的循环内部。程序仍会生成很整齐的输出，但 main 的 self 也变成十，render 的 self 变成九。它们的 total 没变，于是 main 的 total 不再等于自己的 self 加子节点 total。错误控制证明：能打印、能画图，都不足以保证测量语义正确。

恢复原来的函数，再给每个节点检查 `value == self + sum(child.value)`。all 的 self 为零、子节点 main 为十；main 的 self 为零、两个子节点为一与九；render 的 self 为三、decode 为六。这条等式在每个位置都成立，比截图看上去差不多更可靠。

不要用“自动修复”的方式把不守恒的 value 改成总和。如果输入来自上游，静默补齐可能掩盖单位错误、重复聚合或截断数据；你无法知道差额应该落在哪个节点。生产 `web/src/components/flame-analysis.ts / validateFlameTree` 对坏关系报错，就是让错误停在还能追溯来源的位置。

还有一个坏改动是把 children 做成全局名字字典。两条不同父路径的 decode 会被错误合并，调用关系丢失。测试不能只断言总量仍为十，因为这种错误仍可能保住总量；还应断言不同路径下有不同节点，以及它们各自的父子关系。守恒是必要条件，不是所有正确性的总和。

## 函数表与调用树，要分开计算

用户经常想知道“哪个函数自身开销最多”，这时才需要按名字聚合。路径末尾的函数获得这条路径的 self；路径上出现过的每个不同函数获得这条路径的 inclusive。课程模块在建立树的同时维护 functions，最终按 self 降序、名字升序输出，排序稳定有利于比较测试结果。

在刚才三条数据里，decode 的 self=6、inclusive=6；render 为三与九；idle 为一与一；main 为零与十。所有函数 self 加起来恰好是总权重十，因为每条路径只把自身权重交给一个叶函数。所有 inclusive 相加却超过十，因为一条路径本来就包含多个不同函数，不应该拿这一列做全局总计。

这也解释了“main 最宽，所以先优化 main”为什么过于草率。main 作为入口包含所有下游工作，它的 total 很大是结构必然；decode 的 self 更高，说明直接落在该叶函数的权重更多。是否值得修改，还要看调用来源、数据量和业务指标，函数表只能帮你缩小阅读范围。

不要把课程代码中的 `weight` 一律改名为 `duration_ms`。对于 samples，来源是采样权重；对于 cpu_ticks，来源是计数增量；堆对象图可能是字节。相同的树操作可以复用，不表示单位可以互换。字段名与界面说明应把测量类型带过去，避免算法正确而报告在最后一步换了含义。

## 递归为什么不能把同一份采样算两次

现在换成另一个合成输入：`main;walk;walk 4` 与 `main;walk 6`。第一条表示 walk 内又有一层 walk，第二条停在外层。树中外层 walk 的 total=10、self=6；内层的 total=self=4。保留两个节点是必要的，否则递归深度和调用位置会消失。

如果直接遍历树，把同名节点的 total 相加，walk 会得到十四。问题是内层那四份权重已经包含在外层十份里，第二次相加只是重复记账。函数 inclusive 应表示“含有这个函数的路径权重”，对于同一条路径中的重复名字，只能算一次。课程实现因此遍历 `set(frames)`，生产 folded 解析器也明确处理了这个问题。

反过来，self 可以按同名叶节点累计：外层六加内层四等于十。它们来自不同路径的终点，没有重复。同样叫“按函数聚合”，两种列的算法却不同；不要为了少写一个循环而统一成“把所有节点字段相加”。代码简短不等于统计口径一致。

运行课程的 folded-tree case，会同时记录正确的 walk inclusive=10 与错误控制 naive_recursive_inclusive=14。这个错误控制不是声称生产有此缺陷，而是在合成输入上主动演示错误算法的后果。看到差异后，再到 `analyzer/drop_analyzer/folded.py / analyze_folded` 找 `for frame in set(frames)`，这一行就不再是神秘的去重技巧。

## 从教学函数换到有边界的解析入口

回到项目根目录，打开课程模块的 `parse_folded`。它接收 bytes，先限制原始长度，再严格按 UTF-8 解码；对非空行计数，用最后一个空格分割路径与数字，拒绝非十进制正整数，并在累计前检查安全整数总量。只有这些检查通过，才进入共同前缀合并。

为什么不直接 `int(count_text)`？Python 的 int 能接受一些你未必想纳入协议的表达方式，而跨语言消费者可能采用另一套规则。这里明确限制 ASCII 数字长度，让一行在 Python、Go 与浏览器讨论中有一致含义。Python 的任意精度整数能算出来，也不代表 JSON 到 JavaScript 后还能精确表示。

树构建时也要检查新增节点数，不能只检查输入行数。每行都可能创建很多新前缀；共享前缀很少时，节点增长更快。freeze 将内部字典转换成按名字稳定排序的 children 数组，深度已被限制，所以这一步不会接收无界递归。边界要在消费资源的地方检查，不能等构建完十万层再说输入太深。

严格函数出错时抛出固定类别，不返回一棵空树伪装成功。空输入、非法编码、深度超限和权重错误分别有明确原因；调用方才能决定展示错误、保留原制品或让用户修正。原始正文不应作为公共错误信息整体输出，课程中的打印只针对代码里写明的合成夹具。

还要拒绝一种貌似友好的恢复：前两行合法、第三行坏掉，就返回前两行的树并标成成功。它改变了输入覆盖范围，却没有告诉用户丢了什么。课程函数把整次解析视为完整结果或明确失败；生产若要保留部分检查点，应使用单独结构、输入摘要和未完成标记，不能复用成功结果的身份。

## 跑一组有答案的本地实验

在项目根目录执行以下入口。它不会启动 mini-drop，不需要模型密钥，也不会读取生产账号：

```powershell
python -m tutorials.labs --case folded-tree --json
python -m unittest tutorials.labs.test_analysis -v
```

第一条应报告该 case 的检查通过；查看 observations 时，预期 total_weight 为十，函数表第一项为 decode、自身六，recursive_walk 的 inclusive 与 self 都为十，naive_recursive_inclusive 为十四。这里给的是稳定机制值，不是 CLI 外层排版约定；命令的实际运行结果才是你的本地验证收据。

接着用一个小文件直接调用真实课程函数，验证不是只会运行预设 case。以下代码从项目根目录可导入模块：

```python
from tutorials.labs.analysis import parse_folded

result = parse_folded(b"main;render;decode 6\nmain;render 3\nmain;idle 1\n")
print(result["total_weight"])
print([(row["name"], row["self"], row["inclusive"])
       for row in result["functions"]])
```

预期输出第一行是 `10`，第二行依次为 `('decode',6,6)、('render',3,9)、('idle',1,1)、('main',0,10)`，实际 Python 会在逗号后打印空格。再将第二条权重三改成五：总量应为十二，render 的 self 变成五、inclusive 变成十一，decode 仍为六。修改前先写答案，才能用实验检验理解。

## 坏输入不是附录，是功能的一部分

逐项构造反例，先预测错误类别再执行。`main 0` 应被拒绝为 INVALID_WEIGHT；`main;;decode 1` 是 INVALID_FRAME；含非法 UTF-8 的字节应得到 INVALID_UTF8；十七层路径超过课程深度，应报 DEPTH_LIMIT。每次只改一处，才能知道失败来自哪个边界，而不是一份输入同时触发很多限制。

还有一个特别值得保留的回归：第一行权重已经是 JSON 安全整数上限，再来一份权重一。单看每行都像合法数字，总和却无法精确传给浏览器。`test_analysis.py` 检查解析器在返回树之前就拒绝这次累计，而不是等前端某处出现不可解释的四舍五入。

错误控制也要能抓住错误。暂时在自己的临时副本删除总量检查，再运行这项回归，它应失败；若测试仍绿，说明它可能只检查了“函数能调用”，没有检查真正的风险。随后恢复代码，不要把故意删除保护的版本当作实验成果提交。

课程上限和生产上限不同，所以不能把“十七层课程拒绝”写成“Drop 不能处理十七层”。实验验证的是这份教学实现按其边界工作。生产解析的回归要在 `analyzer/tests/test_folded.py` 中读对应 Limits，并按 Analyzer 的依赖说明运行；两种测试都应记录实际命令与环境。

## 把整数检查写成一个可以单独验证的函数

现在把最容易被一行 int 隐藏的规则抽出来。下面函数接收一段数字文本和此前总量，返回本行权重；它可以放进刚才的临时教学文件。输入规模已经很小，所以这里的重点是让协议决定解析，而不是让 Python 默认转换规则决定协议。

```python
def parse_weight(text, total):
    limit = 2**53 - 1
    if not isinstance(text, str) or not 1 <= len(text) <= 16:
        raise ValueError("INVALID_WEIGHT")
    if not text.isascii() or not text.isdecimal():
        raise ValueError("INVALID_WEIGHT")
    weight = int(text)
    if weight <= 0 or total + weight > limit:
        raise ValueError("INVALID_WEIGHT")
    return weight


print(parse_weight("6", 0))
try:
    parse_weight("1", 2**53 - 1)
except ValueError as error:
    print(error)
```

预期输出先是六，再是 INVALID_WEIGHT。接着分别测试 `+6、6.0、1e3` 与非 ASCII 数字，这些文本都不属于本章约定的正十进制表示。某种语言碰巧能转换，不代表其他服务同意接收；协议要先于实现习惯。负例越具体，跨语言评审越容易讨论。

再问一个不太直觉的问题：为什么在加法前检查 `total + weight`，而不只是限制每个 weight？因为两条都很大的合法行可以合成不安全总量。Python 仍能精确累加，浏览器却可能丢失整数精度，这种错误往往到火焰图比较时才显现。解析入口提前拒绝，可以保住后续所有消费者的共同前提。

这份完整函数并不替代行格式检查。它不知道路径是否为空，也不知道栈深或节点数；单一职责让它容易验证，但调用方必须把其他检查接齐。不要因为“每个小函数都严格”就省略整体流程测试，少调用一次验证函数，整个保护仍然不存在。

## 输入字节少，不代表解析开销一定小

想象两份大小相近的文件。一份反复使用同一条三层路径，另一份每行都有完全不同的长前缀。前者复用大部分节点，后者会建立大量新字典。文件字节上限只能限制输入，节点数上限限制的是构建后的结构；两种限额观察的对象不同，不能用其中一个代替另一个。

同样，栈深很大时，后面的冻结、校验与界面布局都可能受到影响；权重特别大时，文件不一定大，数值精度却先越界。课程函数因此分别检查字节、行、帧、深度、节点和总量。你可以把它们写成一张“输入特征→消耗对象”的对应表，看看是否还有只看总文件大小漏掉的维度。

做一个小型受控负例：构造十七个名字相同的帧，字节很少，仍应触发深度限制；再构造很多不同前缀，观察它们可能先遇到节点或行数边界。不要使用百万级生成量“压一压电脑”，课程上限已经足够演示边界先后。负向实验应有确定的停止条件。

生产 Worker 还在这外面限制下载合计、临时磁盘、子进程 CPU 与墙钟。解析器知道树规模，父进程知道作业整体资源，它们看到的风险层次不同。一个解析函数通过小夹具，不等于整条分析任务已经有足够资源隔离，这也是模块单测不能替代系统门禁的原因。

## 用变形关系检查一批新输入

固定一份答案之外，还有一种很适合解析器的检查方式：改变输入写法，但提前说明哪些结果应该不变。将三行交换顺序，树权重与函数表应该相同；把 `main;render;decode 6` 拆成相同路径的两行二与四，权重仍相同，但输入行数会增加。后一个例子提醒你不要把整份 metadata 都要求完全一样。

从仓库根目录保存并执行下面的小脚本，它调用真实课程函数，预期只打印 True、True。数据仍为合成，不触发任何采集：

```python
from tutorials.labs.analysis import parse_folded

a = parse_folded(b"main;render;decode 6\nmain;render 3\nmain;idle 1\n")
b = parse_folded(b"main;idle 1\nmain;render 3\nmain;render;decode 6\n")
c = parse_folded(b"main;render;decode 2\nmain;render;decode 4\nmain;render 3\nmain;idle 1\n")
print(a["tree"] == b["tree"] and a["functions"] == b["functions"])
print(a["tree"] == c["tree"] and c["stack_count"] == a["stack_count"] + 1)
```

再加一条新的正权重路径，根总量应该恰好增加这份权重，原有不相关分支的 self 不应变化。若程序错误地清空了某个前缀、把整个 total 加到每个叶，单份固定截图未必容易发现，变形关系却能直接指出不该变化的部分发生了变化。

这种检查不是把实现抄到测试里再算一遍，而是使用独立的代数性质：加法可交换、同路径可拆分、不同路径保留结构。它不能代替所有坏输入回归，却能给你一批更广的正确性约束。把“预期哪些量不变、哪些量应变化”写清楚，测试才不会因为过强断言误拒绝合理元数据变化。

## 把今天的函数放回生产 Analyzer

生产入口不是 HTTP 请求直接调用这段函数。`worker.py / process_job` 从当前分析 job 找 RAW，先检查登记大小、合计下载预算与 SHA，再经 pipeline 和一次性解析子进程进入具体格式适配。`folded.py / analyze_folded` 只负责其中的解析与聚合，结果发布仍要经过租约与当前 attempt 校验。

解析完成以后，`result.py / make_result` 补充 kind、source、unit 和 limitations；`pipeline.py / build_outputs` 生成压缩树、热点表、报告与 manifest。课程返回的 tree/functions 是机制结果，生产对外 JSON 还有自己的字段结构，不能把课程字典直接上传并声称兼容完整报告。

把这个映射写进笔记：输入验证对应下载和格式层，树聚合对应纯解析层，来源与单位对应公开结果层，成功可见性对应发布事务层。以后增加一种 folded 来源时，先判断它到底只是语法相同，还是测量语义也相同。PROC_CPU 和 Python 栈采样正是“语法可相近、语义要分开”的例子。

毕业自测有三个答案。为什么 self 总和应等于总量？每条路径只交给一个叶节点。为什么 inclusive 总和可能更大？同一路径经过多个不同函数。为什么递归 walk 不该是十四？同一条路径中的相同函数只代表同一份权重。若你还能用一个反例让错误实现失败，再说明生产结果何时才允许发布，这一章就真正完成了。
