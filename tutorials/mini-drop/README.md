# Mini Drop：四种语言的一条本地教学链

这是独立参考实现，不导入正式服务包，不改生产契约。C++ 测自己的受控计算，Go 建任务和逐轮目录，Python 校验 RAW 并生成一棵两层树，课程页面通过本地代理读取 API。没有外部 PID、真实业务请求、模型、数据库、对象存储或生产凭据。

`source=controlled_self`、`unit=cpu_ticks`：Linux 等平台使用 `std::clock()`；Windows 用 `GetProcessTimes` 的 user+kernel CPU 时间、每秒 10,000,000 tick，因为 Windows CRT 的 `clock()` 并非净 CPU 时间。二者都可能因计时分辨率而测到零。树中的 `controlled_process` 是进程计账节点，不是函数调用栈。不同平台或不同工具的 tick 不能直接相减。

## 依赖与文件

- C++17 编译器，编译启用 `-Wall -Wextra -Wpedantic -Werror`。这是独立教学参考的最低标准，正式 core 工程仍遵循自己的 C++20 构建配置。
- Go 1.23+；control 仅用标准库，没有第三方依赖。
- Python 3.11+；analyzer 仅用标准库。
- 现有前端阅读器与教学页面由主项目管理，后端无需 Node。

`collector.cpp` 是可执行采集器，`analyzer.py` 是独立分析 CLI，`control/` 是 HTTP/文件 journal/单 worker。`.local/` 是建议的学习产物目录，不应提交或指向生产数据。一个 workdir 只允许启动一个控制面进程；本参考没有多进程锁或 HA 保证。

## Windows PowerShell

从仓库根目录执行。若 g++ 不在 PATH，把第一条编译命令中的 g++ 换为本机编译器完整路径，并在路径前加 PowerShell 的 `&` 调用符。静态链接编译器运行库可以避免子进程依赖宿主 PATH 查找 MinGW DLL。

```powershell
$miniRoot = (Resolve-Path tutorials/mini-drop).Path
$miniBuild = Join-Path $miniRoot '.local'
New-Item -ItemType Directory -Force -Path $miniBuild | Out-Null
g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror -static-libgcc -static-libstdc++ "$miniRoot/collector.cpp" -o "$miniBuild/collector.exe"
$miniPython = (Get-Command python).Source
Set-Location "$miniRoot/control"
go test ./...
go run . --listen 127.0.0.1:4330 --collector "$miniBuild/collector.exe" --python "$miniPython" --analyzer "$miniRoot/analyzer.py" --workdir "$miniBuild/work"
```

保持这个终端运行。下面的请求放到另一个 PowerShell 终端中执行：

```powershell
$miniTask = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:4330/tasks -ContentType application/json -Headers @{'Idempotency-Key'='lesson-create-001'} -Body '{"iterations":1000000}'
$miniID = $miniTask.data.id
Invoke-RestMethod -Uri "http://127.0.0.1:4330/tasks/$miniID"
Invoke-RestMethod -Uri http://127.0.0.1:4330/tasks
```

若状态还在 QUEUED/RUNNING，稍后再读取。终态后可以显式重试：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:4330/tasks/$miniID/retry" -ContentType application/json -Headers @{'Idempotency-Key'='lesson-retry-001'} -Body '{}'
```

主动新建或新增轮次使用新键；同一 HTTP 请求的网络重发保留原键。示例键可重复观察幂等，但下一项真正的新任务要换键。

## Linux

从仓库根目录执行，使用本机已安装的 Python、Go 与编译器：

```sh
mini_root="$(pwd)/tutorials/mini-drop"
mkdir -p "$mini_root/.local"
g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror "$mini_root/collector.cpp" -o "$mini_root/.local/collector"
mini_python="$(command -v python3)"
cd "$mini_root/control"
go test ./...
go run . --listen 127.0.0.1:4330 --collector "$mini_root/.local/collector" --python "$mini_python" --analyzer "$mini_root/analyzer.py" --workdir "$mini_root/.local/work"
```

另开终端请求：

```sh
curl -sS -H 'Content-Type: application/json' -H 'Idempotency-Key: lesson-create-001' --data '{"iterations":1000000}' http://127.0.0.1:4330/tasks
curl -sS http://127.0.0.1:4330/tasks
```

从返回值读取实际 id，再请求 `/tasks/实际id`。不要把教学编号当成自己的运行记录。

## 固定接口

| 请求 | 输入 | 行为 |
| --- | --- | --- |
| GET /tasks | 无 | `{data:[task,...]}`，最新 ID 在前，最多保存 1,000 项 |
| POST /tasks | `Idempotency-Key`，`{"iterations":N}` | N 为 1,000–10,000,000 的整数，201 新建/200 重放 |
| GET /tasks/{id} | 字符串 ID | `{data:task}`，找不到为 404 |
| POST /tasks/{id}/retry | 新键，`{}` 或空正文 | 仅 SUCCESS/FAILED 可新增 attempt，最多 100 轮 |

任务包含 `id:string`、`attempt:int`、`status`、`iterations`、UTC 时间、`events:[{kind,attempt,at}]`，成功时含 `result`，失败含 `error_code`。状态为 QUEUED/RUNNING/SUCCESS/FAILED。错误格式为 `{data:null,error:{code,message}}`，不回传子进程 stderr、命令或宿主配置。

请求正文最多 1 KiB；未知字段、重复键、错误类型、尾随 JSON 拒绝。只监听数字回环地址，也拒绝非回环 Host。无 Origin 的本机 CLI 可访问；有 Origin 时只接受同源、`http://127.0.0.1:4322` 或 `http://127.0.0.1:4193`。浏览器课程经 `/mini-api` 本地代理读取，不开放跨域凭据或任意工具路径。

## 文件、恢复和限制

```text
work/
  journal.json
  tasks/1/attempt-1/raw.json
  tasks/1/attempt-1/result.json
  tasks/1/attempt-2/...
```

journal 同时保存状态、事件和幂等键：先写同目录临时文件、sync、rename，再发布进程内新快照。磁盘写失败时不会先把内存变成成功。采集开始前记录 RUNNING；结果必须通过 RAW SHA、单位、计数与树关系核对才能发布 SUCCESS。旧 attempt 的 finish 会被拒绝。

重启后 QUEUED 可以重新入队；RUNNING 变为 `FAILED/INTERRUPTED_RESTART`，不盲目重采。已经完成的文件保留，各轮不会覆盖。若磁盘故障导致 RUNNING 写入失败，worker 不执行采集，这项已持久 QUEUED 工作需要解决磁盘问题后重启恢复。文件写入失败不会被包装成成功。

单 worker、队列 100 项、每个子进程十秒超时、stdout 32 KiB/stderr 8 KiB、RAW/RESULT 各 32 KiB、journal 8 MiB/最多 5,000 个幂等键。输出被有界捕获但不公开 stderr。程序不提供取消、保留期清理、跨进程租约、数据库事务、SSE、鉴权账户或生产隔离。`exec.CommandContext` 约束本参考直接启动的固定程序，不等于正式 Runner 的进程组预算与嵌套子进程回收。

同目录 rename 与文件 sync 是教学持久化方法，不代表所有文件系统和断电条件都拥有 PostgreSQL 的提交保证。不要多个进程共享 workdir，不要把它挂到生产数据目录。停止使用服务终端的 Ctrl+C；保留学习文件以便复查，无需递归删除目录。

## 课程对应与验证记录

第 11–14 章逐步解释交接、采集、控制面与故障。源码中的 Go 测试覆盖重复创建/冲突、严格输入/Origin、重启、不重采、旧轮次发布以及 SHA/单位复核。上述命令是可运行步骤，是否在某台机器实际通过，要以独立的运行记录为准。

正式工程的对照入口是 `core/src/{agent,runner,process,target}.cpp`、`api/internal/{service/tasks.go,repository/postgres.go}` 和 `analyzer/drop_analyzer/{worker,registry}.py`。这份参考只用于看清最短链路；更换文件存储、加一个重试循环，不能自动获得正式工程的并发、权限与恢复能力。
