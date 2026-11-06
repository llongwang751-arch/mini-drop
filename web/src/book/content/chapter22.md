## 22.1 毕业作品应该让别人复现一件事，不是看完一份介绍

这一章的交付很具体：别人从仓库和你的说明出发，能编译受控采集器、启动独立 Go 控制面、在工作台创建任务、找到 Python 结果与来源摘要，再运行十二个机制实验。之后还要看到你自己完成的一项有边界的改动、能抓坏实现的回归，以及保留失败历史的证据记录。

参考链已经在 `tutorials/mini-drop`，它是可运行答案，不是一堆等待你补空的 TODO。先让答案版本通过，建立工具链基线；再在自己的学习分支或 `.local` 副本重建步骤与完成扩展。不要把复制整个目录说成“从零独立实现”，也不要为了制造工作量故意破坏主项目。

建议给自己的作品增加一项明确新功能：只在学习版加入第二种受控 workload mode。当前参考只接受 iterations，没有 mode；后文给出的 mode 设计是学生扩展任务，不是已经上线的接口。它能让你真正协调 C++、Go、Python、React，而不需要开放外部 PID 或 shell。

最后交付材料应围绕这件小工作组织：起点版本、接口与状态约定、运行说明、最终 diff、回归与负控、实际结果及限制。评阅者应该能够指出你修改了哪个边界、为什么需要它，而不必先相信你罗列的技术栈。

## 22.2 起点文件就这些，每个只承担一份职责

先打开以下文件，然后画一条实际数据箭头。`collector.cpp` 测自己的受控计算，`control/runner.go` 用 argv 启动固定程序，`analyzer.py` 校验 RAW 并生成两层树，`LabWorkbench.tsx` 从独立教学 API 读取结果。生产服务包不会被这份 mini 程序导入。

| 起点文件                                | 你需要能解释的具体行为                     |
| --------------------------------------- | ------------------------------------------ |
| `tutorials/mini-drop/collector.cpp`     | 参数有界、自身 CPU 计数、固定 JSON 输出    |
| `tutorials/mini-drop/control/server.go` | 严格请求、幂等创建、显式新轮次             |
| `tutorials/mini-drop/control/store.go`  | journal 快照、状态与事件一起写、旧轮次拒绝 |
| `tutorials/mini-drop/control/runner.go` | 固定 argv、有界输出、RAW 与结果交叉核验    |
| `tutorials/mini-drop/analyzer.py`       | schema、单位、权重与输入 SHA               |
| `web/src/book/LabWorkbench.tsx`         | 显式连接、请求重发保留键、当前结果展示     |

不要把文件数量当成架构深度。这里没有远程 Agent、PostgreSQL、MinIO、mTLS 或付费模型，所以一条短链就能看清交接；正式 Drop 的事务、租约、权限和采集工具另有实现。教学链故意减少部署前提，保留身份、单位、状态、来源等必要概念。

给自己一个最小阅读验收：指出 raw.json 是谁写的，result.json 是谁写的，SUCCESS 在哪次校验之后发布，浏览器关闭后哪个进程继续工作。若这四个问题还需要猜，先不要开始跨语言新功能。

## 22.3 从空终端准备工具链，构建产物放独立目录

下面的 PowerShell 从仓库根目录执行，适用于当前 Windows 学习环境。需要 C++17 编译器、Go 一点二三及以上、Python 三点一一及以上；mini 最低标准是十七，正式 core 仍按自己的 C++20 配置构建。命令不读取生产配置，也不要求启动整套 Docker。

```powershell
$capRoot = (Get-Location).Path
$capMini = (Resolve-Path tutorials/mini-drop).Path
$capBuild = Join-Path $capRoot '.local/tutorial-mini'
New-Item -ItemType Directory -Force -Path $capBuild | Out-Null
$capPython = (Get-Command python).Source
g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -static-libgcc -static-libstdc++ "$capMini/collector.cpp" -o "$capBuild/collector.exe"
go -C "$capMini/control" test ./...
go -C "$capMini/control" build -o "$capBuild/control.exe" .
```

编译器不在 PATH 时，使用它的完整路径并在前面加 `&`，不要换成网上随手下载的二进制。Windows 静态链接编译器运行库，是为了避免 Go 子进程继承有限环境后找不到 MinGW DLL；不是让采集器获得更多权限。

Linux 对应步骤在 mini README，输出文件不带 exe，Python 使用实际 python3 路径。不要把 Windows 工具链失败解释成业务错误，也不要把旧 collector.exe 还留在目录里当作本次构建成功。每条命令的退出码、产物修改与摘要都要能对应当前源码。

本教程维护时已有独立实测记录，但你的机器、分支和改动是新的验证对象。先自己建立一份基线收据，再做扩展；旧通过可以说明步骤曾可行，不能替本次工作签字。

## 22.4 先单独观察采集器，接受真实零值

运行 `& "$capBuild/collector.exe" --iterations 1000000`，预期得到 schema_version、controlled_self 来源、cpu_ticks 单位、每秒 tick 数、total_ticks、iterations 与 checksum。不要盯着 total_ticks 必须为正，短任务受系统计账分辨率影响，零是允许的真实结果。

Windows 使用 GetProcessTimes 的 user 加 kernel 时间，单位率为每秒一千万；其他平台的 mini 使用自身进程 CPU 时钟。它们都测本程序，不接受用户传外部 PID，也没有函数调用栈。相同名字 cpu_ticks 还要结合 clock_ticks_per_second，不能跨平台直接比较原始整数。

```json
{ "source": "controlled_self", "unit": "cpu_ticks", "total_ticks": 0 }
```

上面只是摘出三个字段说明零值语义，完整 RAW 仍必须满足全部 schema。不要把这个短 JSON 作为完整输入交给 analyzer，然后因被拒绝就放宽字段检查。少字段失败恰好说明交接契约在工作。

checksum 让受控计算的输出可以核对，不证明 CPU 时间一定准确，也不能阻止编译器做合法优化。增加 iterations 可以改变工作量，但仍须保持范围；不要因为想画一条好看的非零树就篡改 RAW。真实零值比虚构采样量更能说明你尊重测量边界。

## 22.5 启动 Go 控制面，再用最小 HTTP 请求建立任务

保持构建终端，启动本地独立服务。workdir 要绝对路径，只给这个学习进程使用；同一目录不能被两个控制面同时打开。参考实现只有单 worker 和进程内锁，没有跨进程文件锁或高可用保证。

```powershell
$capWork = Join-Path $capBuild 'work'
& "$capBuild/control.exe" --listen 127.0.0.1:4330 --collector "$capBuild/collector.exe" --python "$capPython" --analyzer "$capMini/analyzer.py" --workdir "$capWork"
```

另开 PowerShell 发请求，保存实际返回 ID。创建返回的是持久任务，不是已经完成的报告；如果还在 QUEUED 或 RUNNING，稍后读取详情。task ID 为字符串，attempt 为整数，不能把页面示例编号当自己的运行记录。

```powershell
$capTask = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:4330/tasks -ContentType application/json -Headers @{'Idempotency-Key'='capstone-create-001'} -Body '{"iterations":1000000}'
$capID = $capTask.data.id
Invoke-RestMethod -Uri "http://127.0.0.1:4330/tasks/$capID"
```

同样正文与同样键重发，应得到原任务；改 iterations 却保留键，应冲突。终态以后显式 `/tasks/$capID/retry` 配新键和空对象，才开始新 attempt。网络重发与用户主动再做一次，在接口里有不同身份，毕业演示必须能说明这个区别。

## 22.6 接上 React 工作台，连接失败不要塞假数据

已有前端依赖时，在 `web` 启动 `npm run dev -- --port 4322`，打开 `/book/workbench`。页面先显示尚未连接，点击“连接本地服务”才请求教学 API；创建按钮在连接以后使用。只读教程无需后台，工作台操作则需要刚才的四三三零服务。

浏览器请求同源 `/mini-api/tasks`，Vite 开发代理转发到本地四三三零并去掉 mini-api 前缀。它与正式 `/api` 代理分开，不能因为教学服务没启动就把请求改向生产。静态预览如果没有这个代理，会拿到 HTML 或连接错误，需要按本地开发入口处理。

工作台以有限轮询读取活动任务，组件退出取消浏览器请求，不会自动取消 Go 任务。创建响应不确定时保存同一参数的 pendingCreation 键，再次提交沿用；显式 retry 另保存自己的键。这些交互状态不是后端幂等的替代，而是正确使用它的客户端部分。

页面会显示来源、单位、输入摘要、事件和限制。controlled_process 只是进程级计账节点，不是热点函数。树值零时保留零，不添加演示样本；如果把它换成一个虚构主函数，页面看起来丰富，证据反而变假了。

请在演示中主动离开页面再回来，确认任务状态仍可读取，并指出任务生命周期在 Go。这个动作比展示动效更能证明你理解浏览器与后台工作的边界；但 mini 没有取消接口，不要把正式 Drop 的取消能力移植到这条演示描述里。

## 22.7 第一轮没跑通，按交接顺序定位

先区分服务根本没启动，还是任务已经 FAILED。启动失败常见于相对路径、不存在的程序、端口占用和不可写 workdir。服务在运行却没有 JSON，检查请求是否被错误代理到前端 HTML；不要在 request 函数中吞掉解析错误然后返回一个默认成功对象。

任务失败再看固定 error_code 与当前 attempt。TOOL_FAILED 表示受控子进程未正常完成，TOOL_OUTPUT_LIMIT 表示输出越界，INVALID_COLLECTOR_OUTPUT 表示 RAW 不符合约定，RESULT_INTEGRITY_FAILED 表示分析结果与 RAW 的来源或树关系不一致。错误阶段能缩小搜索，不需要公开全部 stderr。

Windows 下 collector 单独能运行而被 Go 启动失败，优先检查运行库与有限环境，不要让 runner 继承整个宿主环境凑成功；那会把路径与凭据污染带进子进程。Python 路径也要是真正的可执行文件，别把一个别名字符串或另一份虚拟环境目录当程序路径。

读取当前轮次目录的 raw.json 和 result.json，先核对实际字节 SHA、source、unit、计数率与树值。PowerShell 某些文本写入方式会添加 BOM、改变换行或编码，不能把屏幕复制出的文本当原始字节；正式交接由 Go 写 RAW，学习排查避免手工重存覆盖它。

遇到 journal 无法安全恢复时保留原文件，换一个全新隔离 workdir 做对照，说明新目录只是定位工具。不能清空旧失败再宣布恢复通过。你应能回答“哪个条件失败、原证据在哪里、修复后用哪个新记录验证”，而不只是说重启以后好了。

## 22.8 看一次真正的交接，再分清它没证明什么

Go 的 runAttempt 建立 task 与 attempt 目录，用固定 argv 启动 collector，校验输出后原子写 raw.json，再执行独立 Python analyzer，最后重新检查 result 的输入 SHA、单位、总量和树结构才发布 SUCCESS。四种语言都在链中有实际职责，没有靠浏览器拼出一份模拟报告。

输出被有界捕获，子进程有十秒超时，参数不能选任意程序；结果树只有 all 与 controlled_process 两层，叶值等于 total_ticks。你可以按这些条件独立核算，而不必理解正式项目所有采集器。这是最短可解释链的价值。

重启时 QUEUED 重新入队，RUNNING 转 FAILED/INTERRUPTED_RESTART，不盲目重采。成功 RAW 与结果保留在各轮目录，显式 retry 新建 attempt，旧 finish 无权覆盖当前轮次。文件 journal 同时保存状态、事件与幂等键，但它仍不是 PostgreSQL 事务的完整替身。

参考没有账户授权、远程 Agent、lease、对象存储、SSE、长期保留清理或正式 Runner 的进程组预算。十二个机制模型会解释其中一部分规则，却不意味着 mini 服务已经实现它们。毕业作品要分别标注“实链已有”“模型演示”“学生新增”“仍未实现”，避免把三份材料拼成一套不存在的系统。

这也决定测试范围：mini smoke 可证明本次受控真实交接与重启保留，不能证明生产 perf、memray、BPF、多节点或业务请求延迟。每一个通过旁边都写明它面对的输入和边界，读者才能把证据用在正确地方。

## 22.9 十二个机制实验，给每项保留一个反例

在根目录运行 `python -m tutorials.labs --all --json`。十二个 case 汇总失败时返回非零；PASS 表示该 case 的断言全部成立，里面可以包含“坏请求被正确拒绝”。其中临时 SQLite 和固定字典都是教学材料，不是生产 PostgreSQL 或真实采集记录。

第一组是生命周期：lifecycle-outbox 检查任务与待办一致，attempt-fence 检查旧取消不能改写新轮次状态，lease-handover 检查接管后的旧 worker，atomic-events 检查创建任务、轮次、事件与待办一起提交。迟到成功结果不能发布，则由 mini 的 Go 测试 `TestOldAttemptCannotPublishAfterRetry` 另行验证。请逐项找出故意插入的失败条件，而不是只抄四个 case 名。

第二组是数据交接：folded-tree 检查权重与路径，heap-dominators 检查对象关系与保留量，sse-frames 检查分片事件重组，contract-evolution 检查显式版本和来源。它们使用合成字节与图数据，不能冒称真实 Java dump、在线 SSE 或生产契约已经改动。

第三组就是本篇实现：tool-gate 让越权/错阶段/伪造事实失败，checkpoint-replay 让改内容与 pending 盲恢复失败，controlled-comparison 让错误输出和不稳定负控否决，evidence-gate 让旧 lease/source 与覆写历史失败。每项都包含实际执行规则，不读取一个预填 passed 标签决定结果。

只运行单项用 `--case` 加上确切名称；先 `--list` 查看当前注册表。你需要提交每项的一句解释：不变量是什么、哪个坏输入打破它、正确实现在哪里拒绝。能解释十二个失败机制，比在 README 写“有十二项实验”多了可评阅内容。

## 22.10 学生扩展任务：让第二种工作负载完整穿过四种语言

以下是尚未实现的学习任务：新增受控 mode=mix 与 mode=sum，当前旧行为明确为 mix。先为 mini 写版本二 RAW 约定，包含 mode；旧版本一若兼容，要依据已知旧行为解释为 mix，并注明版本来源，不把陌生字段静默丢掉。正式 contracts 目录不要为这项练习改编号。

C++ 只接受两个固定 mode、有限 iterations，拒绝未知值，不接受外部 PID 或命令。sum 可以用有限整数累加并产生可核对结果，正确性比运行更慢更快重要；相同迭代次数不等于两种 mode 有相同工作量，不能拿它们互相当优化基线。

Go 将 mode 纳入严格请求、任务记录、幂等语义、固定 argv 与重启校验。尤其同一个幂等键、相同 iterations 但不同 mode 必须冲突；若 keyRecord 还只记迭代次数，就会把两项不同工作当同一请求。这是扩展真正跨越的状态边界。

Python 对版本与 mode 重新校验，生成结果保留实际 mode，并把输入 SHA 继续绑定原字节；不能靠 Go 说检查过就跳过自己入口。React 增加有限选项、展示实际返回 mode，保持不支持时明确错误，不能只把界面选项改了而后台永远执行 mix。

把 TODO 拆成四次可验收提交：先契约与坏输入，再采集/分析直连，再 Go 持久化和幂等，最后 UI 与整链。每次保留一个仍然失败的下一步测试。不要一次复制所有答案后回头编一个“分四步开发”的时间线。

## 22.11 给新功能的回归，要能抓住最容易出现的偷工实现

至少构造这几个有不同失败原因的例子：未知 mode、版本二缺 mode、旧版兼容、相同键换 mode、失败后新轮次保留 mode、RAW 声称 sum 而 task 仍是 mix、结果改变输入 SHA、页面选择 sum 后实际返回 mix。每个负例只错一处，才能知道是哪层检查抓住它。

故意把 Go 幂等比较里的 mode 删除，冲突用例应该失败；把 analyzer 的 mode 校验删掉，错误 RAW 用例应该失败；让 UI 永远提交 mix，浏览器交互用例应失败。受控变异可以验证测试是否真覆盖跨语言边界，不能只检查几个函数返回结构存在。

原有 smoke 与十二 case 继续跑，但不要因为它们全通过就说新增 mode 已验证。旧 smoke 不知道新字段的语义，新功能需要自己的场景和当前二进制。若修改 smoke，先保留原基线，再明确新增覆盖，不倒填旧记录。

本地真实链可用提供的 smoke.py 独立验证。它用自选回环端口和临时 workdir，启动自己拿到的程序，结束只关闭自己拥有的子进程。参数都是现有绝对路径，输出摘要由脚本写出，避免文本管道改变 RAW 字节。

```powershell
python tutorials/mini-drop/smoke.py --control (Resolve-Path .local/tutorial-mini/control.exe).Path --collector (Resolve-Path .local/tutorial-mini/collector.exe).Path --python (Get-Command python).Source --analyzer (Resolve-Path tutorials/mini-drop/analyzer.py).Path --output .local/tutorial-mini/smoke.json
```

## 22.12 证据包绑定这份代码，失败历史只能追加

先记录 git 提交及工作区 diff。HEAD 只代表已提交内容，不能把一份有未提交改动的二进制说成完全属于该 SHA。再核对 collector、control、analyzer 和前端静态资源的实际摘要；固定镜像若参与，也记录不可变 ID，而非仅一个可以重打的 tag。

用 `Get-FileHash -Algorithm SHA256` 可查看具体源码或产物摘要。命令日志、依赖版本、输入参数、实际退出码、检查名称、通过/失败/跳过和范围一并保存。smoke.json 的通过计数只是该脚本定义的检查，不能把模型实验断言数加上去叫生产测试总数。

每轮使用新 run_id 或新证据文件；修复后成功另写一份，旧失败保留。evidence-gate 的内存模型演示相同 ID 不能换字节、skip 不能写成 passed、旧 lease/source 不能发布。真实不可变发布还需要受控文件系统或数据库事务，不是给 JSON 加个 hash 就自动拥有。

对于当前教学实链，允许实际 tick 为零，人审质量未知也保持未知。不要把零改成正数，不要把未做的模型调用填成成功，不要把被跳过的真实 PG 场景算进通过分母。别人能复算的有限证据，比看起来完整却不知来源的全绿页更可靠。

证据里也不放生产配置、凭据、原始业务 profile 或任意长 stderr。mini 的受控输入适合公开教学，但后续接真实目标以后材料边界会变化，不能沿用“教程目录都能上传”的习惯。保存足够复查的白名单摘要，私有材料留在明确受控位置。

## 22.13 回滚是一项操作设计，不是把所有文件删掉

学习改动失败，先停止自己启动的 mini 服务，保留 workdir 与失败收据，切回已记录版本后重新构建到另一个产物目录。不要拿旧二进制配新 analyzer 再宣布源码回滚完成；参与交接的程序与契约要成套对应。

若新版本 journal 不能被旧版本读取，明确保留旧文件并使用新的隔离目录建立对照。真正兼容迁移要有独立设计与测试，不能只改 schema_version 数字。这里的新目录用于防止误写和定位，不能拿它证明原数据已经成功恢复。

提交前用 git diff 检查是否遗留故意删除的 fence、放宽输出限制、硬编码成功和新增秘密。只撤销自己本次变更，不覆盖别人同时进行的工作。反例测试可以保留，变异实现必须恢复；“为了演示先关鉴权”不应成为最终补丁。

这一毕业项目没有生产部署步骤，也不需要连接两台已授权生产主机。学习版目录、端口和凭据与生产分开，不运行生产 compose down，更不能删除数据卷来获得干净环境。能在本地完整复现已经足够验收当前课程目标，发布属于另外一项有明确版本和验证的工作。

最后试一次把重现说明交给另一个人：他能否判断该开哪个终端、哪里执行、失败看什么、如何停服、旧文件如何保留。把这些遗漏补齐，也是工程交付，不是代码之外可随便省略的文档劳动。

## 22.14 评阅时看四项实物，不凭自信程度打分

第一项是可运行链：从构建到页面有真实控制流，RAW 与结果可核对，来源/单位/零值不被伪造。若只展示静态页面或导入结果，不能得到这一项；如果没有工作台环境，可以提交 CLI 实链并明确前端待验收，不能借参考截图冒充自己执行。

第二项是独立改动：mode 等小功能必须有契约、四端对应修改和至少一种会失败的坏实现。字段只在页面出现、幂等未纳入、结果丢掉来源，都是尚未完成的具体问题。评语要指出哪一段行为缺失，不用“架构不够高级”这种无法行动的话。

第三项是测试解释：十二实验各能指出不变量和负例，新功能回归能在定向变异下报警；第四项是证据与边界：版本绑定、旧失败、未运行项、停止与回滚说明齐全。更多测试数量不能补偿来源不明，更多服务数量也不能补偿一条坏轮次覆盖当前结果。

面试演示可以先用一分钟创建真实任务，再选“同键换参数冲突”或“旧 attempt 不能发布”讲透一处，最后展示你新增回归如何抓住坏实现。不要在十分钟里逐个解释所有依赖，把真正的工程判断挤没了。

完成这份作品，你证明的是自己能够构建和解释一条受控采集链、设计失败边界并交付可复查改动。正式 Drop 仍有单机容量、长期稳定性、多节点和 AI 根因质量限制；教学成果不会自动消除它们。下一次学习就从你已经保留的一个失败或未知出发，继续提出可验证的小问题。
