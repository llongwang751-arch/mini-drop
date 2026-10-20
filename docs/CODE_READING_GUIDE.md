# 中文注释版阅读路线

这份路线按“能继续开发，也能在面试里讲清楚”来安排。源码注释重点解释模块职责、关键输入输出、上下游关系和容易出错的边界；不逐行复述语法，也不把函数名、协议字段或错误码翻译成新的标识符。

## 先分清两种 Agent

| 名称 | 主要代码 | 负责什么 |
| --- | --- | --- |
| 采集 Agent | [core/src/agent.cpp](../core/src/agent.cpp) | 常驻节点，接收任务，核对目标，调用真实采集工具，上传和恢复结果。 |
| AgentScope 智能体 | [agent_runtime/drop_agent_runtime/runner.py](../agent_runtime/drop_agent_runtime/runner.py) | 调用 DeepSeek，运行原生 ReAct 循环，根据工具观察继续诊断。 |
| Go 控制面 | [api/internal/service/agent_runtime.go](../api/internal/service/agent_runtime.go) | 判断谁有权限、哪些工具可用、预算是否超限，以及最终报告能否被采纳。 |

ReAct 指推理、行动、观察的循环，与前端 React 框架不是同一个概念。模型提出工具调用，不表示它直接获得数据库、shell 或 Docker 的权限；调用仍需经过 Go 的阶段白名单和参数校验。

## 第一遍：跟着一次普通采集走

1. 看 [web/src/views/NewTask.tsx](../web/src/views/NewTask.tsx) 和 [web/src/task-schema.ts](../web/src/task-schema.ts)：用户选类型和参数，但规则来自共享目录，不由页面随意拼命令。
2. 看 [api/internal/service/tasks.go](../api/internal/service/tasks.go)：请求经过身份、目标权限、幂等及状态约束，创建任务和本次执行，并记录后续下发所需事实。
3. 看 [core/src/server.cpp](../core/src/server.cpp)：Go 的下发请求如何进入队列，心跳如何领取任务，为什么需要先锁 Agent，再选择 Task。
4. 看 [core/src/agent.cpp](../core/src/agent.cpp) 的 `run`、`execute_task`，再看 [runner.cpp](../core/src/runner.cpp)：从类型化任务进入真实工具执行。
5. 看 [core/src/target.cpp](../core/src/target.cpp)、[process.cpp](../core/src/process.cpp) 与 [helper.cpp](../core/src/helper.cpp)：为什么只知道 PID 不够、为什么要用 argv、为什么额外权限放在独立 helper。
6. 回到 Agent 的 `complete`、`recover` 和 Server 的 `NotifyResult`：文件上传、结果确认、数据库登记、创建分析 job 是不同阶段，断网时不能简单重跑全部步骤。
7. 看 [analyzer/drop_analyzer/worker.py](../analyzer/drop_analyzer/worker.py)、[repository.py](../analyzer/drop_analyzer/repository.py) 和 [isolate.py](../analyzer/drop_analyzer/isolate.py)：领取租约、下载校验、隔离解析、上传和发布结果。
8. 最后看 [web/src/views/TaskDetail.tsx](../web/src/views/TaskDetail.tsx)、[event-stream.ts](../web/src/event-stream.ts) 和 [profile-loader.ts](../web/src/profile-loader.ts)：页面如何等待真实状态、恢复事件流，并把大制品交给 Worker。

第一遍先追数据，不要求马上理解所有采集器实现。可以先选择 Python CPU 或普通 perf 这条分支，确认完整链路，再横向比较堆、I/O、分配器和 BOLT。

## 第二遍：看 AI 调查为什么能形成闭环

| 问题 | 建议入口 | 阅读重点 |
| --- | --- | --- |
| 自然语言如何变成可执行计划？ | [investigation.go](../api/internal/service/investigation.go) | 读取实际节点能力、生成计划、等待用户确认，再创建普通采集任务。 |
| 模型如何调用工具？ | [agent_runtime.go](../api/internal/service/agent_runtime.go)、[runner.py](../agent_runtime/drop_agent_runtime/runner.py) | Go 建立一次受限会话，SDK提出调用，Go执行并返回观察，循环有轮数、次数和总超时预算。 |
| 诊断读到的证据从哪里来？ | [diagnosis_tools.go](../api/internal/service/diagnosis_tools.go)、[diagnosis_paths.go](../api/internal/service/diagnosis_paths.go) | 读取绑定版本的实际分析结果，按受限工具查看质量、热点和调用关系，报告引用要校验。 |
| 复测后为什么还要再次诊断？ | [diagnosis_verify.go](../api/internal/service/diagnosis_verify.go) | 新采集和新诊断拥有各自身份，再与基线比较；重复出现热点不自动证明因果。 |
| 源码定位和补丁如何落地？ | [code_repair.go](../api/internal/service/code_repair.go)、[code_executor.py](../ops/code_executor.py) | 固定登记项目和源码版本，在隔离副本里定位、测试、比较性能；不直接发布原业务仓库。 |
| 断连或重启后怎么办？ | 上述文件中的状态推进、租约与恢复函数 | 先读取已经落库的事实，重放确定的执行身份；不能把不确定响应当作“肯定没执行”。 |

尤其留意这几类注释：为什么要拒绝未知工具、为什么报告引用合法仍不能保证解释正确、为什么只有功能测试通过还不够，以及为什么负控补丁也必须保留结果。

## 遇到这些词时，先对应到具体问题

| 概念 | 在这个项目里解决什么问题 |
| --- | --- |
| `task` | 一条业务采集请求，有自己的权限、类型与历史。 |
| `attempt` | 同一任务的一次实际执行。重试切换执行身份，旧结果不能覆盖新状态。 |
| 采集/分析双状态 | RAW收集完成之后，解析仍可能等待、运行、失败或恢复，不能把 `COLLECTED` 当作分析成功。 |
| 分析 `run` / 租约 | 同一输入可能重新分析或被新worker接手；旧worker失去租约后不能再发布结果。 |
| 幂等键 | 网络重试可能重复发送同一请求；相同请求重放同一结果，不同输入复用键则拒绝。 |
| Outbox | 数据库提交与网络RPC不能组成一个普通本地事务；先把待下发事实一起落库，再可靠重试发送。 |
| 栅栏检查 | 写入前确认当前attempt、run、lease仍属于自己，阻止“旧执行醒来后覆盖新结果”。 |
| 制品 SHA | 证明下载或回传的字节与登记内容一致，不证明模型解释或性能根因一定正确。 |
| helper token / ACK | 本地一次采集的确定身份与确认。确认前可在TTL内恢复；未ACK也不代表结果会永久保存。 |

这些概念需要结合 SQL 约束和写入条件一起看，不能只根据函数名猜行为。共享真源是 [drop.proto](../contracts/drop.proto)、[task-kinds.json](../contracts/task-kinds.json)、[数据库迁移](../db/migrations) 与 [实现契约](CONTRACT.md)。

## 第三遍：用失败场景检验理解

读完一段代码，尝试回答：

- RAW已上传，但确认响应丢了，会不会重复采样？
- 用户重试后，旧attempt晚到成功结果，会发生什么？
- 两个Server同时向同一Agent下发，会靠哪把锁挡住？
- 分析worker失去租约，但子进程刚好计算完，是否还能发布？
- 模型调用了不存在的工具，或引用了不属于本次结果的证据，哪里拒绝？
- 功能测试通过，但性能没有提升，为什么不能报告修复成功？

先找保护条件和对应回归用例，再看成功路径。测试文件里的“合成夹具”“工具替身”“未连接真实数据库”等中文说明，就是提醒你区分：代码边界验证、真实工具采集、真实业务效果是不同层次的证据。

## 本轮保留的英文

协议字段、函数/类名、错误码、日志与界面字符串、测试输入、第三方许可证、构建/静态检查指令保持原样。生成的 Protobuf、Kubernetes 清单及冻结历史证据也不手动改写；这些内容不能当作普通说明文字批量替换。源码字节会随注释变化，旧源码SHA和发布收据仍属于各自原版本。
