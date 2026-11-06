## 5.1 采集 Agent 收到的不是一句命令

上一章把任务写进了数据库。这一章走到目标机器：C++ Agent 要找到一个真实进程，运行采集器，再把文件交回来。假如它收到的只是一段 shell，“让用户自己写命令”会很省开发时间，却很难限定采谁、读什么文件、运行多久，更难在崩溃以后解释到底完成了哪一步。

Drop 下发的是有类型的 `TaskDesc`：task_id、attempt_id、kind、目标 Agent、目标身份、参数、预算、截止时间、对象 key 和短期上传授权。每个字段都服务于一个判断，不是为了把请求体做大。尤其 attempt_id 会一路进入本地目录、工具恢复请求和最终结果，不能采完以后再临时补一个。

先按 `core/src/agent.cpp` 的 `run → execute_task → collected_result → complete → recover` 阅读。主循环负责心跳和取消，工作线程负责耗时采样与恢复。如果采十秒就让主线程睡十秒，控制面会误以为节点失联，取消也要等到采完才能生效。

本章所有故障情景都是帮助读源码的教学推演。练习只需要标准库与自己创建的临时文件；不用给个人电脑开放宿主 PID、不用附加别人的进程，也不用从生产环境拿证书或上传 URL。实际工具能否运行，要以目标 Linux 环境的能力探测为准。

## 5.2 先验身份证，再听它介绍能力

Agent 用 CA、客户端证书和私钥建立 gRPC mTLS 连接。Server 的 `require_agent` 不只检查“证书能通过 CA”，还把证书 common name 与请求的 Agent ID 对齐。以下是 `server.cpp` 的实际摘录：

```cpp
void require_agent(grpc::ServerContext *context, const std::string &id) {
  if (!safe_identifier(id) || peer_name(context) != "drop-agent-" + id)
    throw grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                       "Agent certificate does not match identity");
}
```

如果只验证证书有效，节点 A 可能在请求体里填节点 B 的名字。这个判断把“拿着哪张证书”和“声称自己是谁”绑起来。API 调用控制面也有单独的 `drop-api` 身份，Agent 证书不能因为同一个 CA 签发就获得 API 的所有操作能力。

注册带上协议版本、构建版本、系统架构、内核、工具版本和 capability_details。服务端对字符串、数组和 JSON 大小设限，并协商协议；旧协议不应收到自己不能解释的新任务。重新注册时数据库保留 disabled 状态，节点不能靠不断上线把管理员的停用决定冲掉。

“支持 perf”也不能仅凭磁盘上有一个 perf 文件。内核限制、工具完整性、真实探测、目标权限都可能让它不可用。页面看到的能力来自这些检查。工具刚才可用也不代表稍后一定可用，所以执行前仍须验证；能力广告减少无效派发，不能当永久承诺。

## 5.3 心跳既报平安，也领取工作和取消通知

注册响应给出心跳间隔；当前实现响应为两秒。每次心跳携带资源、有限数量节点事件和 `running_attempt_ids`。Server 更新节点记录，再拿数据库里的派发事实核对：这个轮次是否属于该 Agent、是否仍是最新轮次、任务是否已终止。需要停止的轮次写入 `cancel_attempt_ids`。

Agent 只有空闲时才从心跳响应取下一项任务。这里是 `agent.cpp` 的实际片段：

```cpp
for (auto attempt : response.cancel_attempt_ids())
  if (attempt == attempt_.load())
    canceled_ = true;
if (!attempt_.load() && !response.tasks().empty()) {
  if (worker_.joinable())
    worker_.join();
  canceled_ = false;
  attempt_ = response.tasks(0).attempt_id();
  auto task = response.tasks(0);
  worker_ = std::jthread([this, task] {
    execute_task(task);
    attempt_ = 0;
  });
}
```

前两行只取消当前正在做的轮次，旧轮次通知不能误伤新工作。随后空闲检查与服务端 Agent 行锁共同约束节点并发，不是收到几项就开几个线程。worker 结束再清空 attempt，主循环继续承担联络职责。

心跳失败也不意味着可以扔掉本地文件。Agent 会记录控制面不可用，保留执行 journal，必要时重新初始化连接。恢复也放进工作线程，使网络重试不阻塞心跳。读到 `sleep` 时，务必弄清睡的是主循环、工作线程还是一次有界退避；它们影响的取消时延不同。

## 5.4 PID 会重复使用，数字相等不代表人还是原来的人

想象页面选了 PID 4321，排队时它退出，操作系统把 4321 分给另一个程序。只调用 `kill(pid, 0)` 能说明这个编号此刻存在，不能说明它还是用户选中的进程。容器里还可能把同一个进程编号为 17，采集器所在空间却看到 4321。

`target.cpp` 的 `process_identity` 从 `/proc` 读取启动 tick、UID、NSpid、PID namespace 和可执行文件设备号/inode。`same_identity` 的实际判断如下：

```cpp
return a.pid == b.pid && a.uid == b.uid &&
       a.namespace_pid == b.namespace_pid &&
       a.start_time_ticks == b.start_time_ticks &&
       a.pid_namespace == b.pid_namespace && a.executable == b.executable &&
       (!a.executable_inode || (a.executable_device == b.executable_device &&
                                a.executable_inode == b.executable_inode));
```

PID 和启动 tick 组合挡住常见复用；namespace 让编号有了所属空间；UID 和可执行文件身份帮助识别采错对象。不是字段越多越安全，而是每个字段都对应一个可能变化的身份维度。采集前固定，采集后以及相关执行边界复核，才能发现中途退出、复用或换程序。

本地策略另外规定可否采宿主进程、宿主资源、容器，以及允许 UID 和精确容器白名单。Go 的权限检查回答“用户能不能操作平台登记的节点”，Agent 的检查回答“当前 OS 对象是否属于这次获准目标”。前面过了不能省后面，后面过了也不能反向授予平台权限。

一个细节值得自己追：`/proc/<pid>/stat` 的进程名可能包含空格，源码先找进程名右括号，再解析后续字段。把整行直接按空格切分，会在看似正常的目标名上读错 starttime。这类输入边界比背一遍 procfs 文档更能检验你是否读懂实现。

## 5.5 argv 把参数分开，白名单再限制参数的含义

目标核验后，`RunnerRegistry` 按 kind 选择工具。下面摘自 `runner.cpp` 的 PYTHON_CPU 分支：

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

程序决定工具、子命令、输出目录和选项名，用户只能提供通过 schema 的数值或允许选项。`process.cpp` 要求可执行文件是绝对路径，拒绝参数中 NUL，最终调用 `execve`，不经过 shell。因此参数里出现分号也只是一段参数字节，不能自动变成第二条命令。

但不要把 argv 描述成万能防护。有的工具会自己解释表达式、文件名或内嵌脚本；绝对路径也可能指向被人换掉的文件。工具受信路径、完整性校验、固定模板、输出路径约束仍有必要。这里有意没有给任务开放任意 executable 或任意附加参数。

子进程环境也没有完整继承服务端配置。`process.cpp` 构造有限 PATH 与 `LC_ALL=C`，特定嵌套工具才附带受限配置。这样解析器、采集器不会顺便拿到父进程环境里的存储口令。学习时可以画出“哪一步需要凭据”，而不是笼统写一句“所有容器都安全”。

## 5.6 预算和取消管的是采集器，不能顺手杀掉业务

`execute` 创建独立进程组，设置文件大小、CPU 时间、地址空间、文件描述符和 core dump 限制。标准输出与错误输出重定向到本次文件，返回时只读取最多 16KiB；原始 stderr 也不会直接变成对外诊断日志。输入有界而输出无限，同样能撑坏服务。

父进程每五十毫秒检查取消、墙钟截止和本次进程组的 task 数量。数量包含线程，核算失败按超限处理；超限终止该组。普通超时和取消先发 SIGTERM，给短暂收尾时间，再用 SIGKILL；工具主进程退出后也清理它所属组内的残留子进程。

为什么不用“杀掉这个 UID 的全部进程”？因为业务服务和采集器可能同 UID，那会把被诊断对象一起杀了。为什么不只设 `RLIMIT_NPROC`？它约束的范围与每任务进程组预算不同，也可能把别人的线程算进来。要先搞清限制对象，再挑机制。

五十毫秒轮询意味着短时间突发可能先发生，再被发现，所以它不是瞬时硬隔离。部署另外使用容器 pids_limit 等外层上限。也不能说一次性工具没有取消竞态：检查刚结束进程就可能启动子线程，收尾必须持续面向自己拥有的进程组，而非只记一个主 PID。

## 5.7 helper 把额外权限收进一个小窗口

perf、附加内存分析等操作可能需要额外权限。让常驻 Agent 全程 root 会扩大风险，项目支持独立 collector helper。Agent 通过 Unix socket 提交固定结构，helper 再校验实际调用方 UID、请求 token、目标身份、本地策略和预算；客户端也核验连到的 helper 身份。

`helper.cpp` 使用 `SO_PEERCRED` 读取内核提供的进程凭据，不接受请求 JSON 自报“我是 UID 10001”。工具名和参数仍来自任务契约，socket 不提供“帮我执行这段 shell”的通用能力。权限集中后，维护者才能较清楚地审核哪些入口会动到目标进程。

同一次 helper 工作有随机 token。Agent 发请求前原子保存 `helper-request.json`；helper 将完成结果持久化。响应丢失时，Agent 用原 token 只请求恢复既有工作，不能改 token 当成新采集。等 Agent 已写下自己的结果，才 ACK helper，让它释放缓存或转成已确认记录。

这与 Server 的 NotifyResult ACK 是不同层次：helper ACK 表示本机工作已被 Agent 接住，远端 ACK 表示控制面已接受结果。两者混成一个“采集完成”会出现一边删文件，另一边还没有可恢复记录的空档。helper 缓存也有 TTL、磁盘预算和状态校验，不是永久保险箱。

## 5.8 三种 CPU 采集，看到的不是同一种东西

PROC_CPU 读取 `/proc` 里的线程 CPU tick 差值，结果适合回答“哪个线程消耗 CPU”。它没有函数调用栈，图上即使出现 `process;thread-name`，也不能把线程名说成业务函数。平台保留 `cpu_ticks` 单位和线程级限制，是为了防止一张像火焰图的图误导读者。

PYTHON_CPU 调用 py-spy，观察 Python 栈并保留采样质量信息。某些栈无法解析时，分析器保留未知占位符与权重，不应直接丢掉让已知函数占比变得漂亮。`native` 选项还取决于权限、符号和目标环境，不等于所有扩展库都能自动定位到源码行。

PERF_CPU 使用 perf record，再执行 perf script。当前协议 2 把 `perf.script` 作为 RAW，格式为 `perf_script`，交给分析端处理；兼容协议 1 才在采集端折叠为 folded。它与 procfs 计账的测量机制不同，内核权限也不同。看到 perf 文件存在，不能声称真实采样已通过；看到了线程 tick，更不能在介绍项目时换个名字说成 perf 栈采样。

所以采集类型不是下拉框里的装饰。它决定 RAW 格式、分析 pipeline、单位、前提条件和结论范围。对照实验若从 PROC_CPU 换成 py-spy，即使图形很像，也不能直接按“样本总数下降”宣布优化成功。第九章会把这个比较陷阱展开。

## 5.9 PUT、NotifyResult、ACK 是三个不同的完成点

工具结束后，Agent 计算 RAW 大小与 SHA，用当前 attempt 的短期 URL 上传指定对象，再处理获准附件与采集 manifest。它不需要长期 S3 密钥。manifest 记录实际时间、版本、大小和摘要，ETag 只作为对象存储返回的 opaque receipt，不能冒充 SHA。

Server 收到 `NotifyResult` 后，才在事务里登记 RAW、更新 COLLECTED、结束 attempt、创建分析作业和事件。上传完成只能证明字节到了存储；Notify 提交成功证明平台登记好了；Agent 收到 ACK，才知道可以清理或转入受限保留。它们之间都可能崩溃。

```text
工具完成 → 原子写 result.pb → PUT RAW → 保存上传回执
         → PUT 固定 manifest → NotifyResult → 收到 ACK → 清理/保留
```

这段是阅读用阶段图，实际 `complete` 还处理安全日志与失败分支。比如 RAW 已提交、日志暂时没传上，系统可以短期补日志；不能为了补一份 LOG 重采 RAW，也不能把日志失败包装成一份假的成功制品。

`notify` 对暂时不可用等错误有界重试。若 Server 告诉它旧 attempt 已不存在，Agent 可以结束这份过时记录，不会改 task_id 再试。服务器仍是最终的发布裁决者，客户端“我觉得成功”不能覆盖取消或新轮次。

## 5.10 恢复目录里每个文件都在回答一个问题

`execute_task` 先创建 `attempt-<attempt_id>`，检查目录不能通过符号链接逃出工作区，权限设为 0700，再原子保存 `task.pb`。`persist_result` 写 `result.pb`。每个制品还有 `uploaded-<name>` 摘要标记、`receipt-<name>.json` 和可能的 `retry-<name>.json`；名称来自源码，不需要学习者自己去伪造生产 journal。

恢复时原 task 告诉程序“这是谁的哪轮任务”，result 告诉它“已经得到什么”，标记与回执告诉它“哪些 PUT 完成了”，重试文件保存退避进度。manifest 已生成后复用相同字节，不能恢复时换一个结束时间，再为同一轮造一份含义不同的清单。

| 崩溃窗口               | 已有本地事实            | 恢复应该做什么                                       |
| ---------------------- | ----------------------- | ---------------------------------------------------- |
| 采样中途退出           | 只有 task.pb            | 尝试既有 helper token 恢复，否则明确 AGENT_RESTARTED |
| 工具完成、尚未 PUT     | task.pb、result.pb、RAW | 上传同一 RAW，不重新执行采样                         |
| PUT 成功、ACK 未到     | result 与上传标记/回执  | 继续 Notify，避免重复已确认 PUT                      |
| PUT 成功、标记尚未落盘 | 对象可能存在，标记缺失  | 同一对象可能再次 PUT；不能声称绝无重复上传           |
| 任务取消或已换轮次     | 本地仍有旧 RAW          | 由 Server 拒绝发布，按终态和保留策略收尾             |

`recover` 没有凭一份半成品目录盲目调用 `execute_task`。没有结果且不能恢复 helper 时，它生成明确失败，文字说明没有开始重复采样。这个选择牺牲了“看起来自动成功”的机会，却保住测量窗口与目标负担的真实性。

目录扫描、文件数、磁盘和保留期限都有界。断网太久、磁盘损坏或记录过期，不属于“必然恢复成功”的承诺。可靠性要把这些边界讲出来；一直重试到磁盘写满，会让单次故障扩散成整台机器故障。

## 5.11 实验：让相同 PID 的冒名者被拒绝

把下面的教学模型保存为临时目录中的 `identity_lab.py`，运行 `python identity_lab.py`。它没有读取真实 `/proc`、没有执行外部工具。输入故意由你提供，目的是先看懂判定，再回源码定位对应字段；本章不把预期输出当成已经执行的验收。

```python
from dataclasses import dataclass, replace

@dataclass(frozen=True)
class Identity:
    pid: int
    start: int
    uid: int
    namespace: str

def same(expected, actual):
    return expected == actual

original = Identity(4321, 800, 10001, "pid:[42]")
cases = {
    "unchanged": original,
    "reused_pid": replace(original, start=900),
    "wrong_uid": replace(original, uid=10002),
    "wrong_namespace": replace(original, namespace="pid:[43]"),
}
for name, actual in cases.items():
    accepted = same(original, actual)
    assert accepted == (name == "unchanged")
    print(f"{name}: {'accept' if accepted else 'reject'}")
```

精确预期输出如下；这里的 accept 只表示教学模型的身份相等，不表示获得真实采集授权：

```text
unchanged: accept
reused_pid: reject
wrong_uid: reject
wrong_namespace: reject
```

把 `same` 暂时改成只比较 pid。第一个负例应触发断言失败，再改回完整比较。随后增加“可执行文件路径相同但 inode 变化”的字段与用例，并去 `same_identity` 查项目为什么还保留 executable_device。不要把新增模型字段误写成对真实 C++ 的覆盖证明。

另做一道纸面负例：参数字符串为 `4321; echo BAD`。它首先应被类型化整数校验拒绝；即便一个普通 argv 参数带分号，execve 也不应把它解释成 shell。若你只能解释后半句，就遗漏了前面的任务 schema 边界。

## 5.12 用三次故障推演检查你是否真正读懂

第一问：py-spy 已退出，RAW 已上传，控制面响应丢失。参考答案是保留原 result 与上传回执，继续确认；不能再采十秒。第二次采样看到的是另一个时间窗口，可能把短暂峰值“修没了”，也给业务额外压力，成功外观反而掩盖了事实变化。

第二问：Agent 重启时，helper 可能已经完成，但 Agent 尚未写 result。参考答案是用已持久 token 做 replay-only 恢复；找不到既有工作就明确失败，不能用同一恢复路径静默新开一次。请沿 `resume_helper → collect(request, true)` 到 helper 的缓存/ACKED 检查验证，不要只看函数名猜行为。

第三问：任务已经取消，上传 URL 仍未过期，PUT 返回成功。参考答案是对象存储和任务发布是两道边界；Server 的当前 attempt 与状态检查拒绝把它变成成功任务。后续维护可以处理孤立对象，页面不能仅凭某个 key 存在就展示“采集成功”。

本仓库的 `core/tests/process_test.cpp`、`target_test.cpp`、`helper_test.cpp` 分别帮助检查执行预算、目标策略和 helper 协议，CMake 对相关 C++ 目标启用警告即错误。Linux 依赖和测试环境准备好后再运行对应 CTest；在 Windows 看懂测试或运行标准库模型，不等于真实 perf、memray 或 helper 的权限路径已验收。

至此你应该能讲清一条具体链路：不是“用了 gRPC 和 C++”，而是证书绑定节点、心跳领取当前轮次、固定身份、受限 argv 执行、持久结果、短期上传和最终确认。下一章由 Analyzer 接棒，它面对的是不可信字节和可能过期的作业租约。
