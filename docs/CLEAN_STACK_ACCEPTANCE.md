# 干净 Linux 核心平台复刻验收

本专项回答“新 checkout 能否用仓库中的 Dockerfile 和 Compose 建起核心平台，并完成一次真实采集与分析”。它使用全新 GitHub 托管 Ubuntu runner，不读取线上配置、模型密钥、镜像、数据库卷或对象存储数据。操作系统和 Docker 由 runner 预装；这是干净源码复刻，不是离线安装或操作系统安装验收。

实现入口是 `scripts/verify_clean_stack.py`，独立 workflow 为 `.github/workflows/clean-stack.yml`。主 CI 原有 14 项的数量与成绩不因这个专项改变。修改核心源码的 PR 会触发；也可以对明确分支执行 workflow dispatch。每次 report 记录实际 checkout HEAD、Git tree、canonical Compose/Dockerfile SHA 和构建所得 image ID。PR 验证的是 GitHub merge checkout，不将它冒充 PR source head。

## 实际范围与边界

从 canonical `docker-compose.yml` 选择 PostgreSQL、MinIO、一次性 migrate、C++ Control、Python Diagnosis Worker、Python Analyzer、Go API、C++ Agent、React/Nginx Web，以及默认无故障的 Python demo。9 项平台核心和 1 项采集目标各有明确角色；migrate 成功退出，8 个长驻服务健康，Agent 用连续新心跳验证。

实际干净 CI 已记录首轮取消和一次失败：6 个项目镜像构建成功，但 canonical 旧 MinIO tag 无法匿名拉取。Quay 与 DockerHub 匿名 Bearer manifest 探测均拒绝访问，不能只更换 registry 便宣称修复。单机 canonical 改为 `deploy/dockerfiles/minio-source.Dockerfile`，从原 2025-04-08 版的官方 upstream commit `d0cada583fce88f60cb276ddfb06f5cb16820069` 构建；codeload tar SHA-256 为 `989506993f138bc8092368adaa9e0d8e980aef0da3178e8649ff2d34d3a4a665`。源码下载先校验再编译，既定版本保持；旧线上镜像和数据不因复刻验收被替换。

MinIO 为本轮第一个源码构建项，唯一 image tag，禁止 pull 预制 MinIO 或覆盖 source pin。真实运行后核验 `minio --version` 的原 release tag 与完整 commit，使用 HTTP ready endpoint 健康检查；桶初始化和产物上传/读取仍由实际 S3 链路验证。这个源码构建修正尚不代表整条复刻已通过，须后续 fresh CI 报告确认。

Office、Go/Java/C++ 业务 demo、模型调用质量、perf/BPF/其他采集器、小时压测不属于这个专项。它们已有各自证据或专项，不能由一次 sys_metrics 成功推导为全部通过。没有模型密钥时显式设置 `MINI_DROP_AI_ENABLED=none`，没有用模型 fallback 冒充模型验收。

隔离配置从原 Compose 生成，保留服务命令、依赖和构建 Dockerfile。允许的覆盖包括唯一 project/image tag、私有 env/PKI 和只读源码路径、官方构建镜像源、串行 native build、内存/CPU/PID 上限、关闭重启、Web 只绑定 loopback 随机端口、仅启默认无故障 Python target。只验 sys_metrics，因此 Agent 不申请 BPF/perf capability 或 tracing/debug mount；保留 host PID 用于发现目标。禁止 privileged、host network、外部资源、固定容器名和任意 host bind。

## 必须通过的链路

1. 精确 Git 且 checkout 没有源码变更；Linux Docker 至少 2 CPU、6 GiB 内存、16 GiB 空闲磁盘。逐一从当前 Dockerfile `build --pull --no-cache`，native 编译并行度 1；不得改用生产 overlay 或预置 Mini-Drop 镜像兜底。
2. 生成独立 CA、server/API/Agent 证书、随机数据库/对象存储密码、API key 和 gRPC token。启用 gRPC mTLS、client certificate 要求、token 鉴权和 API key 鉴权。私钥与运行配置为私有临时目录中的 0600 文件；API client key 的 owner 为其实际 UID 65532，Agent key 的 owner 为 cap-free Agent 的 UID 0。文件 owner/mode 和容器内可读性均核验，不能靠扩大权限读取密钥。
3. Alembic 迁移完成，数据库 version 必须等于当前源码 migration heads；对象桶初始化成功，核心健康全部满足。
4. 新数据库只能出现本次 Agent；观察两个递增的新心跳和 authoritative/fresh process snapshot。采集 PID 必须来自本次 Python 容器 `inspect.State.Pid`，独立核验 Linux start ticks；不选宿主机上的其他业务 PID。
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

`tests/test_clean_stack_acceptance.py` 仅验证负向门禁，覆盖生产卷/镜像误接、外部网络、权限与资源扩大、鉴权削弱、PID 复用、损坏/缺失观测、分析 metadata 缺失和清理归属。这些测试不等于干净 Linux 实跑。最终完成状态以独立 workflow 的官方 run 和实际 report 为准；新增脚本或本地单测通过本身不构成复刻已完成。
