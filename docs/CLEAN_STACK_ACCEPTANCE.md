# 干净 Linux 核心平台复刻验收

本专项回答“新 checkout 能否用仓库中的 Dockerfile 和 Compose 建起核心平台，并完成一次真实采集与分析”。它使用全新 GitHub 托管 Ubuntu runner，不读取线上配置、模型密钥、镜像、数据库卷或对象存储数据。操作系统和 Docker 由 runner 预装；这是干净源码复刻，不是离线安装或操作系统安装验收。

实现入口是 `scripts/verify_clean_stack.py`，独立 workflow 为 `.github/workflows/clean-stack.yml`。主 CI 原有 14 项的数量与成绩不因这个专项改变。修改核心源码的 PR 会触发；也可以对明确分支执行 workflow dispatch。每次 report 记录实际 checkout HEAD、Git tree、canonical Compose/Dockerfile SHA 和构建所得 image ID。PR 验证的是 GitHub merge checkout，不将它冒充 PR source head。

## 已验证的实际结果

2026-10-02 的 [官方 Clean CI 36975451670](https://github.com/llongwang751-arch/mini-drop/actions/runs/36975451670) 已完成并成功。验收源码为 `34b74e7b15c81a883e8a7dc2698d8ba7a356dcbf`，实际 PR merge checkout 为 `72d452542007e1a01cfb2f923c6fa72471b6fb39`；官方 commit API 与运行 report 的 Git tree 均为 `5800ff6eecea4f8e3e54b20f888e9cc403ea978f`。它证明这一精确源码树可以在新 GitHub Ubuntu runner 上复刻约定的核心链路，不将后续源码变更自动计为已验收。

实际 workflow 中 **87 条门禁测试通过，0 失败、0 跳过**；真实执行的 preflight、configuration、build、start、migration、heartbeat、task、artifacts、readonly、cleanup **10 个阶段全部 PASSED**。完整官方状态、源码树证明及下载校验见[证据汇总](../reports/quality/interview-release-20261002/clean-ci/36975451670/summary.json)，阶段及源文件 SHA 见[实际 report](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/report.json)。

- 本轮从当前 Dockerfile 构建 7 个不同的项目镜像；运行 10 个服务角色。8 个长驻服务健康，migrate 退出码为 0，Agent 有两个递增的新心跳。数据库 Alembic head 与源码同为 `20260910_0008`。MinIO 的实际运行版本为原 `RELEASE.2025-04-08T15-41-24Z` 和完整 upstream commit，见[版本证明](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/minio-source-version.json)。
- 真实 Task `task_20261002_065937_033d7efd` 达到 `DONE / COLLECTED / SUCCESS`，下载的 `sys_metrics.v2` 原始产物为 **6 个样本、5004 ms 窗口、12680 字节**，SHA-256 为 `cd5bf02fb4a51404ba996284e7d344fe61f54feeecfcb1e4c3ac2c8ac355fda2`。见[Task 最终状态](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/task-final.json)、[原始产物](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/sys_metrics.raw.json)及[产物与 Analyzer metadata](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/task-artifacts.json)。
- 在本次自有目标容器内，以 UID 0 且 effective/permitted capabilities 都为 0，只读核验目标身份。采集前后身份一致；数据库 Task binding 的 PID `30734`、start ticks `69432`、boot ID、PID namespace inode、namespace PID、executable 和 snapshot ID 全部一致；唯一 AnalysisJob 为 `SUCCEEDED / collector.sys_metrics`。见[采集前身份](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/target-identity-before.json)、[采集后身份](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/target-identity-after.json)及[数据库归属链](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/persisted-chain.json)。
- gRPC mTLS、token 和 API key 鉴权均启用；Agent 私钥可读且没有额外 capability，见[运行权限](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/agent-runtime-permissions.json)。真实 Nginx entry 及 6 个 JS/CSS 资源取得 SHA，无 key 的 API 返回 **401**，代理 API 的 database/control/diagnostic readiness 全部 healthy，见[Web HTTP 合同](../reports/quality/interview-release-20261002/clean-ci/36975451670/reports/clean-stack/web-http-contract.json)。
- 清理仅移除本次 **10 个自有容器和 1 个空网络**；3 个新数据卷保留给 runner 生命周期结束后回收。report 记录 `volumes_deleted=false`、`global_prune=false`。结束时源码仍为同一 HEAD，工作树没有修改。

官方 artifact ZIP 为 **89793 字节**，SHA-256 为 `b434274ec9182fcf8c1ed73fcec5b5802d0a11560961a682b9f5a58733f0673f`，与 GitHub 返回的 size/digest 完全一致。下载后再核对 58 个 ZIP 文件及 report 登记的 56 个证据文件的字节数和 SHA；原始 ZIP、官方 job log 与失败记录均保留。

## 实际范围与边界

从 canonical `docker-compose.yml` 选择 PostgreSQL、MinIO、一次性 migrate、C++ Control、Python Diagnosis Worker、Python Analyzer、Go API、C++ Agent、React/Nginx Web，以及默认无故障的 Python demo。9 项平台核心和 1 项采集目标各有明确角色；migrate 成功退出，8 个长驻服务健康，Agent 用连续新心跳验证。

早期实跑发现 canonical 旧 MinIO tag 无法匿名拉取：6 个项目镜像已构建成功，随后在 pull 阶段失败。Quay 与 DockerHub 匿名 Bearer manifest 探测均拒绝访问。单机 canonical 因此改为 `deploy/dockerfiles/minio-source.Dockerfile`，从原 2025-04-08 版的官方 upstream commit `d0cada583fce88f60cb276ddfb06f5cb16820069` 构建；codeload tar SHA-256 为 `989506993f138bc8092368adaa9e0d8e980aef0da3178e8649ff2d34d3a4a665`。源码下载先校验再编译，既定版本保持；旧线上镜像和数据不因复刻验收被替换。

MinIO 为本轮第一个源码构建项，唯一 image tag，禁止 pull 预制 MinIO 或覆盖 source pin。真实运行后核验 `minio --version` 的原 release tag 与完整 commit，使用 HTTP ready endpoint 健康检查；桶初始化和产物上传/读取由实际 S3 链路验证。最终 fresh CI 已通过上述范围内的完整链路。

Office、Go/Java/C++ 业务 demo、模型调用质量、perf/BPF/其他采集器、小时压测不属于这个专项。它们已有各自证据或专项，不能由一次 sys_metrics 成功推导为全部通过。没有模型密钥时显式设置 `MINI_DROP_AI_ENABLED=none`，没有用模型 fallback 冒充模型验收。

隔离配置从原 Compose 生成，保留服务命令、依赖和构建 Dockerfile。允许的覆盖包括唯一 project/image tag、私有 env/PKI 和只读源码路径、官方构建镜像源、串行 native build、内存/CPU/PID 上限、关闭重启、Web 只绑定 loopback 随机端口、仅启默认无故障 Python target。只验 sys_metrics，因此 Agent 不申请 BPF/perf capability 或 tracing/debug mount；保留 host PID 用于发现目标。禁止 privileged、host network、外部资源、固定容器名和任意 host bind。

## 必须通过的链路

1. 精确 Git 且 checkout 没有源码变更；Linux Docker 至少 2 CPU、6 GiB 内存、16 GiB 空闲磁盘。逐一从当前 Dockerfile `build --pull --no-cache`，native 编译并行度 1；不得改用生产 overlay 或预置 Mini-Drop 镜像兜底。
2. 生成独立 CA、server/API/Agent 证书、随机数据库/对象存储密码、API key 和 gRPC token。启用 gRPC mTLS、client certificate 要求、token 鉴权和 API key 鉴权。私钥与运行配置为私有临时目录中的 0600 文件；API client key 的 owner 为其实际 UID 65532，Agent key 的 owner 为 cap-free Agent 的 UID 0。文件 owner/mode 和容器内可读性均核验，不能靠扩大权限读取密钥。
3. Alembic 迁移完成，数据库 version 必须等于当前源码 migration heads；对象桶初始化成功，核心健康全部满足。
4. 新数据库只能出现本次 Agent；观察两个递增的新心跳和 authoritative/fresh process snapshot。采集 PID 必须来自本次 Python 容器 `inspect.State.Pid`，仅在该自有容器中以无 capability 的 UID 0 读取精确 PID 身份；前后再次 inspect 确认容器、运行状态、原 PID 和构建 image ID 没有变化。不选宿主机上的其他业务 PID，也不扩大 host runner 的权限读取进程命名空间。
5. 真实提交 6 秒 sys_metrics Task，等待 `DONE / COLLECTED / SUCCESS`。从 MinIO 经真实 API 下载产物，核对已登记 SHA-256、字节数、v2 schema、至少 5 个同身份且严格递增的样本，窗口至少 4 秒。缺观测、PID 复用、SHA 不符均失败。
6. Analyzer metadata 必须是 `sys_metrics_analysis.v2` 和 `collector.sys_metrics`，目标身份与样本数量一致；数据库里的该 Task 绑定和唯一 AnalysisJob `SUCCEEDED` 必须和原产物形成同一条归属链。
7. 真实 Nginx 返回构建后的 React entry、JS/CSS，代理 API 的 database/control/diagnostic readiness 都健康，无 key 的 API 返回 401。这里只验证 HTTP 合同，不称为浏览器交互测试。
8. 结束时只停止和移除带本次 project/owner 双重标记的容器、空的自有网络。**不执行 down -v、volume rm 或 prune**。本次新数据卷留给 runner 生命周期结束后回收，已有卷从未被挂载或修改。私有密钥目录不上传。

## 运行与证据

在 GitHub Actions 里执行：

```bash
python scripts/verify_clean_stack.py \
  --expected-head "$GITHUB_SHA" \
  --output output/quality/clean-stack
```

脚本拒绝在普通宿主机执行真实验收，也拒绝覆盖已有 output。失败重试必须新 artifact/run attempt，不能改旧失败为通过。workflow 上传 `report.json`、脱敏 Compose、build/runtime 日志、readiness、心跳与快照、Task admission/final、产物 metadata、原始 sys_metrics 字节、数据库链路和 Web HTTP 合同。report 为每个证据文件记录 SHA 和字节数。运行配置及私钥不进入 artifact，命令日志对随机 secret 与签名 URL 脱敏。

只有所有阶段和清理均通过、结束时源码仍为同一 HEAD/内容时，report 才是 `PASSED`。超时、资源不足、registry 失败、服务未健康、零样本或缺链路都记录 `FAILED`；没有 skip 或缩小既定范围后算通过的机制。

`tests/test_clean_stack_acceptance.py` 验证正向配置和负向门禁，覆盖生产卷/镜像误接、外部网络、权限与资源扩大、鉴权削弱、PID/boot/namespace/snapshot 身份不符、损坏/缺失观测、分析 metadata 缺失和清理归属。这些测试不等于干净 Linux 实跑。最终完成状态以独立 workflow 的官方 run 和实际 report 为准；新增脚本或本地单测通过本身不构成复刻已完成。

## 保留的取消与失败记录

首轮取消与三次实际失败分别保留，后续通过没有覆盖它们。门禁单测通过并不能把对应的失败实跑计为成功。

| 官方 run | 实际状态与原因 | 原始证据 |
| --- | --- | --- |
| [36971688615](https://github.com/llongwang751-arch/mini-drop/actions/runs/36971688615) | CANCELLED_NOT_ACCEPTANCE；构建期间发现共享 TLS 配置会让非 RPC 的 migrate/analyzer 尝试安装没有挂载的证书，取消后修正角色配置。没有实际运行失败报告，不算通过。 | [summary](../reports/quality/interview-release-20261002/clean-ci/36971688615/summary.json) |
| [36971943853](https://github.com/llongwang751-arch/mini-drop/actions/runs/36971943853) | FAILED；6 个项目镜像构建成功，旧 MinIO 预制镜像 tag 匿名 pull 被 registry 拒绝。 | [summary](../reports/quality/interview-release-20261002/clean-ci/36971943853/summary.json)、[manifest 探测](../reports/quality/interview-release-20261002/clean-ci/36971943853/minio-registry-public-probe.json) |
| [36973371776](https://github.com/llongwang751-arch/mini-drop/actions/runs/36973371776) | FAILED；验收器错误地只接受另一种 curl 字面形式，在 configuration 阶段拒绝 canonical 的有效 MinIO ready healthcheck；尚未开始源码镜像构建。修正后使用实际 canonical YAML 和 Compose 规范化配置验证等价参数。 | [summary](../reports/quality/interview-release-20261002/clean-ci/36973371776/summary.json) |
| [36973829122](https://github.com/llongwang751-arch/mini-drop/actions/runs/36973829122) | FAILED；构建、8 个健康检查、迁移、Agent、真实 Task 与产物 SHA/Analyzer metadata 已成功，随后 host runner UID 1001 读取 root 目标的 PID namespace 遭拒。独立数据库归属链与只读 HTTP 阶段未执行；修正为精确自有容器内只读身份核验，身份断言和 capability 限制保持。 | [summary](../reports/quality/interview-release-20261002/clean-ci/36973829122/summary.json)、[实际 report](../reports/quality/interview-release-20261002/clean-ci/36973829122/reports/clean-stack/report.json) |
