## 先决定今天要完成哪件事

第一次面对一个多服务仓库，很容易把“能打开网页”“能启动后台”“能完成采集”当成同一个目标。它们依赖不同。只阅读这本教程，需要前端依赖；查看真实任务，需要业务 API 和数据库；完成现场采集，还需要 Agent、可用工具、允许的目标与 Analyzer。

本章给出两条入口。第一条只启动阅读器，在 `4322` 端口读书，无需 Docker、账号或模型密钥。第二条启动独立业务栈，在 `18080` 端口运行一次真实受控 `PROC_CPU` 任务。你可以先完成第一条，再逐步准备第二条，不必让所有学习都被一个镜像下载错误挡住。

这里出现的命令是供你执行的步骤，预期现象用于对照，不代表教程已经替你跑过。完成后把本机输出记入自己的实验记录。仓库历史验收和你的环境可能使用不同镜像、内核与工具，二者不能互相代替。

## 安装前，先看清 Windows 与 Linux 的边界

项目要求 Python 3.11+、Docker 的 Linux 容器环境和 Compose 2.24.4+；前端另需 Node 与 npm。首次完整构建会下载编译器、语言依赖和运行镜像，`docs/LOCAL_DEVELOPMENT.md` 建议至少有 6 GiB 可用内存，并预留足够镜像空间。资源紧张时先运行阅读器，完整栈和编译任务串行进行。

Node 版本也要核对。当前锁定依赖中的 Vite 声明 `node: ^20.19.0 || >=22.12.0`：使用 Node 20 时至少为 20.19，使用 Node 22 时至少为 22.12，Node 18 不满足。可在锁文件的 Vite 条目或安装后的 `web/node_modules/vite/package.json` 查看 engines。若安装出现引擎版本不符，先切换到满足依赖要求的运行时，再重试 `npm ci`；删除锁文件或随手降低 Vite 版本会改变复现对象，不能解决原环境的兼容问题。

安装或切换以后，重新打开终端再查看版本。电脑上同时有多个 Node 时，当前 PATH 仍可能选中旧程序；安装器显示完成，并不说明这个终端已经使用新版本。

Windows 用户可以安装 Python 和 Node 的本机版本，打开 Docker Desktop 并选择 Linux 容器。编辑器、PowerShell 和浏览器留在 Windows，C++ 采集器则在 Linux 容器内运行。无需为了编译 Agent，再在 Windows 上拼一套 Linux 内核接口。

Linux 用户需要能使用 Docker Engine 和 Compose 插件，Python 命令通常是 `python3`。本文 Windows 示例使用 `python`，Linux 对应步骤改用 `python3`。如果版本低于要求，先按所在系统的安装流程补齐；仓库不提供一条同时适用于所有发行版的系统安装命令。

先确认终端当前连接的是你准备使用的本地 Docker 环境。不要把学习用启动器对着远程共享环境执行。理解当前终端在哪台机器、Docker daemon 在哪台机器、文件路径属于哪台机器，是解决挂载和 PID 问题的第一步。

## 十分钟以内先把阅读器打开

在 Windows PowerShell 中，从本项目目录执行：

```powershell
Set-Location E:\tx-drop\web
node --version
npm --version
npm ci
npm run dev -- --port 4322 --strictPort
```

Linux 在自己的仓库根目录执行下面这组命令：

```sh
cd web
node --version
npm --version
npm ci
npm run dev -- --port 4322 --strictPort
```

`npm ci` 按锁文件安装依赖，适合先复现仓库给定的依赖组合；已有相同依赖时可以略过。终端启动成功后，访问 [本项目第一章](http://127.0.0.1:4322/book/chapter1/)。参考教程使用 `4321`，本教程使用 `4322`，两者可以同时存在。

`--strictPort` 的作用是端口被占用时明确失败，避免服务悄悄换端口，你却继续打开旧页面。若已有自己启动的阅读器，先用它所属终端的 Ctrl+C 关闭；或者明确换成 `18081` 并打开对应地址。不要随意结束不认识的进程。

阅读页本身不请求业务 API。你可以翻章、搜索章节目录、调整字号和主题，而后台尚未启动。业务页面报连接错误与阅读器是否正常，是两个问题。Vite 终端需要保持运行，关闭它后此开发地址也会停止服务。

## doctor 检查的是运行前提

下面开始完整业务栈。另开终端，回到项目根目录。Windows 执行：

```powershell
Set-Location E:\tx-drop
python --version
docker version
docker compose version
python scripts/dev.py doctor
```

Linux 在仓库根目录使用 `python3 --version` 和 `python3 scripts/dev.py doctor`，Docker 命令相同。若 `docker version` 只有客户端信息而无法连接服务端，优先启动 Docker 或检查本地 daemon，暂时不要排查 Go 依赖。

`scripts/dev.py:doctor` 的实际代码节选如下：

```python
kind = run(["docker", "info", "--format", "{{.OSType}}"], capture=True, timeout=15)
if kind.strip() != "linux":
    raise DevError("Linux containers are required.")
```

第一行询问 Docker 的容器系统，最多等十五秒；第二行明确要求 Linux。后续再检查 Compose 版本是否至少为 2.24.4，因为启动器使用列表覆盖来收紧端口。doctor 通过说明这些前提成立，还没有创建真实任务，也没有验证任何 Python 业务进程可被附加。

## plan 帮你确认操作会落到哪里

继续执行：

```sh
python scripts/dev.py plan
```

输出会包含 project、URL、workspace、images 和 `production_access`。默认 project 是 `txdrop-dev-local`，URL 是 `http://127.0.0.1:18080`，实例工作目录位于仓库的 `.local/dev/local`。plan 展示选择，不会启动完整栈；没有 Docker 时也可以先读这份计划。

独立 project 名只是第一层隔离。启动器还为实例准备私有配置，移除继承的发布变量，验证最终 Compose 模型，拒绝外部共享卷、实例目录之外的 Secret、宿主 PID、特权容器和非回环监听。检查合并后的结果，比只看某一个覆盖文件更有用：Docker 真正执行的是合并模型。

需要第二个实验环境时，可在首次创建前使用 `--instance lesson --port 18081`。此后的命令都带相同实例名。已有实例会沿用保存的端口和镜像选择，不能想当然地认为每次传一个新端口就会改动现有配置。

第一次学习建议先保留默认 `local`。后面的 `dev:local` 脚本固定读取这个实例的 settings；使用自定义实例时，完整 Web 地址仍按该实例端口访问，不要误以为热更新脚本会自动发现所有实例。

## up 怎样把程序和数据准备好

在项目根目录执行：

```sh
python scripts/dev.py up
python scripts/dev.py status
```

Windows 也可以通过 `powershell -File scripts/dev.ps1 up` 调用相同入口。up 会准备本地文件、检查配置、生成必要凭据，构建镜像，再等待服务就绪。迁移与对象存储初始化也在栈内完成。启动成功后，它检查并重载代理，使代理能够重新解析后端地址。

第一次构建耗时往往集中在下载和编译，终端暂时没有显示“ready”不等于业务代码卡死。观察失败发生在哪个步骤：获取基础镜像、安装依赖、编译，还是等待容器健康。只有走到后面的阶段，才值得把注意力移到数据库迁移或服务配置上。

启动器会打印本地账号文件的位置，口令不会直接显示。自己在本机读取这些文件，用浏览器打开 `18080` 登录。它们属于这个本地实例；不需要复制任何生产配置来“补齐环境”。账号文件、Cookie、CSRF token 都不应放进学习笔记或公开截图。

## 缓存和下载失败，分别怎样处理

本仓库有一个容易遇到的依赖边界：指定旧版本 MinIO 容器镜像曾无法匿名拉取。`deploy/minio/README.md` 给出了使用官方同版本二进制、固定大小和 SHA 构建本地测试镜像的方式。AMD64 本地 Docker 环境按该文件可执行：

```sh
docker build --platform linux/amd64 --build-arg TARGETARCH=amd64 -t minio/minio:RELEASE.2025-07-23T15-54-02Z -f deploy/minio/Dockerfile .
```

它是在新的本地开发/测试环境中准备缓存的重打包镜像，不是原容器镜像的逐层复现。ARM64 使用说明中的对应 platform 和 TARGETARCH；支持构建参数本身也不等于你已经完成了 ARM64 运行验证。

| 失败发生处             | 先查什么                              | 合理的下一步               |
| ---------------------- | ------------------------------------- | -------------------------- |
| Docker 不响应          | Desktop/daemon、容器系统、当前连接    | 让 doctor 通过，再启动     |
| 镜像获取失败           | 网络、镜像标签、是否为上述 MinIO 版本 | 按固定构建说明准备依赖     |
| npm 或语言依赖下载失败 | 下载错误、代理、磁盘空间              | 修复获取问题后重试原命令   |
| 编译进程被终止         | 可用内存、其他并行任务、错误输出      | 串行运行，先减少资源争用   |
| 旧镜像能启动但接口不符 | 镜像集与当前契约/迁移是否匹配         | 使用匹配版本，重新核对链路 |

启动器也支持 `--image-set` 配合 `--no-build` 选择已准备的镜像集，具体五个键见本地开发文档。它适合受控缓存复用；随手指定一组旧镜像，只能验证那组镜像，不能据此宣布当前源码已经通过。

## smoke 比“能登录”多检查了什么

完整栈启动后执行：

```sh
python scripts/dev.py smoke
```

smoke 首先核对 Web 容器的 Compose project 标签和精确回环端口，确认正在访问当前实例。然后登录，选择 ID 为 `aliyun-local` 且公布 `PROC_CPU` 能力的 Agent，从它公布的受控进程中选择目标，创建两秒、10 Hz 的任务，最多等待 120 秒。

下面是 `scripts/dev.py:smoke` 的实际成功条件节选：

```python
if task["collection_status"] == "COLLECTED" and task["analysis_status"] == "SUCCESS":
    return {"project":config["project"], "task_id":task_id, "collection_status":"COLLECTED", "analysis_status":"SUCCESS", "source":"real controlled PROC_CPU; not synthetic folded data"}
```

两个状态必须一起满足。只看端口能连、首页能打开或登录成功，都没有覆盖 Agent 领取、真实计数、文件上传和 Analyzer 发布结果。smoke 成功后，返回的 task ID 才是这次实验的入口；不要拿页面已有的历史成功任务充当自己的运行结果。

这条任务读取真实受控进程的 `/proc` 线程 CPU 计数，没有伪造函数栈。但它不能说明 py-spy 已安装、业务 Python 目标已授权或某个 Python 函数很热。工具和目标变化后，需要单独验证新分支。

## 在浏览器里看懂自己的第一条任务

打开本地 `18080`，找到 smoke 返回的 task ID，依次查看任务类型、采集/分析状态、attempt、事件和制品。短任务的中间状态可能一闪而过，持久事件更适合回看。先核对创建时间和名称，确认你看的确实是本次运行。

接着区分 RAW 与结果制品。前者保留原始采集字节，后者由分析生成。查看名称、格式、大小、SHA 和归属即可开始理解数据流；无需为了学习把性能文件正文贴到聊天或日志中。即使受控数据通常简单，也应养成按来源和敏感性处理的习惯。

下载按钮会在当前权限下换取短期 URL。它可能包含签名信息，复制完整浏览器请求或截图时容易带出去。笔记保留 artifact ID、SHA 和大小，不保留 URL、Cookie 或 token。短地址过期后重新从页面申请，不能据此认定制品已丢失。

想验证下载一致性，可以对实际下载文件计算 SHA256，和页面登记值比较。Windows 使用 `Get-FileHash -Algorithm SHA256 -LiteralPath "实际下载文件路径"`，Linux 使用 `sha256sum` 加实际路径。哈希一致说明字节对应得上，不说明分析解释和根因一定正确。

## 学生实验：留下一个可以复查的结果

实验前提是 doctor 通过，默认 local 栈已成功 up，当前页面为本地开发环境。目标是完成一次真实 PROC_CPU，保留足够信息让第二天的自己解释发生了什么。它不要求模型密钥，也不要求提升宿主采集权限。

先执行 status 确认栈状态，再执行一次 smoke。成功后在浏览器找到任务，把下面这份记录填完；空白项保留为空，失败项记录失败，不用另造一组看起来完整的结果：

```text
运行日期与本地实例：
代码版本或检出位置：
smoke 返回的 task ID：
kind / attempt：
collection_status / analysis_status：
RAW 名称、大小、SHA：
来源：本地受控 PROC_CPU / 其他（说明）
异常阶段与错误码：
```

然后执行 `python scripts/dev.py stop`，再 up，检查那条任务是否仍可查看。预期数据保留，账号也沿用；这个观察帮助你把“容器运行”与“持久数据存在”分开。如果观察不符，保留原输出并检查是不是换了 instance，而非先删除环境。

最后用两句话解释实验范围：“我验证了这组环境的普通受控采集到分析结果链路”“我还没有验证 Python 函数采样、BPF 或 AI 诊断”。能准确表达范围，也是完成实验的一部分。

## 如果失败，按最后一个已确认事实往下找

先执行 `python scripts/dev.py status` 和 `python scripts/dev.py logs`。logs 读取近期有界服务日志；继续定位时保留时间、task ID、阶段和安全错误码。不要通过打印整份环境配置或签名 URL 来扩大诊断信息。

| 已确认事实                     | 下一段应检查什么                               |
| ------------------------------ | ---------------------------------------------- |
| 页面打不开                     | Web 是否运行、端口是否正确、访问的是否为本实例 |
| 能登录，smoke 找不到 Agent     | 注册、心跳、Agent 公布能力与受控进程           |
| 已建 task，一直 CREATED        | Outbox 派发、Core 通信、队列接受               |
| 已 DELIVERED 或 RUNNING 后失败 | 目标身份、工具、预算及采集错误证据             |
| 已 UPLOADING 后停住            | 对象上传与结果确认，不先假设工具未执行         |
| 已 COLLECTED，分析失败         | RAW 可读性、格式/哈希、Analyzer 与分析错误     |

超时也要保持这个顺序。smoke 超时只说明它未在窗口内观察到成功，不代表任务不存在。脚本保留任务和证据，先找回 task，再决定是否需要重试。每遇到一个不确定状态就新建任务，会让历史更难解释。

## 日常改代码和环境能力的边界

修改前端时可以保留完整栈，在 `web` 中执行 `npm run dev:local`。它读取 `.local/dev/local/settings.json` 的端口，通过代理访问默认实例后端，并显示“本地开发”。阅读器和业务热更新可以共用前端代码，但只有业务操作需要 API。

学习结束时，根目录的 `python scripts/dev.py stop` 保留本地数据、镜像与任务记录；Ctrl+C 关闭自己启动的 Vite。保持这种温和的停止方式，后续做恢复练习才有历史可看。

自测：doctor 通过而 smoke 失败，能否归咎于采集器实现？**参考思路：**还不能。先按阶段区分工具前提、服务连接、契约版本与实际执行。再问：smoke 成功，能否支持“Python 函数热点定位已完成”？**参考思路：**不能，PROC_CPU 的线程计数没有那样的观察能力。区分环境不具备、实现不支持、这轮没验证，才能让下一步动作准确。

进入 [第 3 章：四种语言，怎样说同一种话](/book/chapter3/)。你已经拿到一条具体任务，接下来用它核对参数、文件和状态究竟由谁定义。
