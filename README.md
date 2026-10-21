# Drop · 分布式性能诊断工作空间

基于《Drop 性能诊断系统架构与复刻指南》的独立实现，使用 C++20 gRPC Server/Agent、Go Gin/GORM API、Python Analyzer、React/TypeScript Web、PostgreSQL 和私有 MinIO。该工程不是腾讯内部源码，也不声称与其内部系统完全等同。

阅读源码可从 [中文注释阅读路线](docs/CODE_READING_GUIDE.md) 开始，先追一条采集任务，再看 AgentScope 工具循环、自动复测和隔离修复。注释重点解释上下游、设计原因和失败边界，不逐行复述代码。

## 全量实现与验收状态

当前平台已部署至 **[https://82.157.81.79](https://82.157.81.79)**，API / AgentScope runtime / Web 均为 **20261021.2**，官方 AgentScope SDK **2.0.10**、SQL **9**、**27** 个运行容器；Agent 与 Helper 保持版本3。规划、诊断和源码修复使用真实 AgentScope ReAct，Go 保留权限、状态与验收。详见[本轮诊断知识发布](docs/releases/20261021-diagnostic-knowledge.md)、[知识层说明](docs/DIAGNOSTIC_KNOWLEDGE.md)与[面试步骤](docs/INTERVIEW_DEMO.md)。旧120.24.187.205属于历史部署。

原 CRM/Saber 演示和16类 Runner/20个真实任务、BOLT、malloc证据保留。上一轮 Saber 调查11共76.4秒，修复9通过原2+冻结6测试、384例和6组fresh对照，CPU中位1.5587倍，仅限受控查询基准。本轮将这份既有结果登记为案例3，关联任务86/诊断29，没有重新执行该基准。历史见[AgentScope发布](docs/releases/20261020-agentscope.md)、[AgentScope接入](docs/AGENTSCOPE_INTEGRATION.md)与[Saber范围](docs/SABER_INTEGRATION.md)。

- 18 类任务契约、16 类 Agent Runner 与通用导入；Java/Go/Python/native 堆和 CPU、BPF I/O/内存、资源、受控脚本及 BOLT。工具、目标权限和内核能力按真实探测展示。
- mTLS 注册与版本协商；PID/UID/starttime/namespace 校验、宿主与容器目标策略；持久化双状态机、attempt/lease fencing、事务事件与 Outbox、取消重试及恢复。
- 独立分析进程与资源预算、原生格式解析、堆对象图/支配树/引用路径、类型专属结果、制品 SHA/大小校验；100,000 节点火焰树边界及 Worker 可视化。
- 资源组与共享权限、本地账户、Cron/IANA 时区、最多16节点复合DAG、版本化规则与反馈。企业登录和异地备份展示保持移除。DeepSeek Agent新增持久化调查、能力感知计划、自动复测比较、真实源码SHA/行号、隔离补丁及功能/性能对照，见[实现与演示说明](docs/DIAGNOSIS_AGENT_V2.md)。
- 浅色精密仪表台、动态参数表单、SSE、结果证据、节点工具元数据、响应式和键盘操作。
- 诊断知识：4份 `v1.0.0` Skill（CPU热点、分配/GC、锁/IO、采样质量），本人90天有效、可撤销的案例检索；`off` / `skills` / `experience` 三种模式及固定合成评测。方法和经验只作先验，不能替代当前证据；入口为[诊断知识](https://82.157.81.79/knowledge)。
- 最小权限数据库身份与审计追加权限；对象保留及完整性维护；监控告警/追踪、证书轮换、备份恢复、Kubernetes 与供应链发布流程。

本轮完成四次真实 DeepSeek 诊断运行：任务90的诊断34/35、任务91的诊断36/37，分别对照 `off` 与 `experience`；启用经验的35/37各命中案例1/2。输入是明确标注的合成导入数据，不是实时 CPU/分配采集，也没有执行复测。引用校验通过不保证解释正确：诊断37误称“唯一叶帧”，实际有70%/30%两个正权重叶帧；诊断35还将60秒/99Hz归因于未返回这些参数的E7。详见[Agent语义复审](docs/evidence/diagnostic-knowledge-20261021/agent-review.json)。这属于 `agent_review`，不是独立人工准确率；[真实调用评测](docs/evidence/diagnostic-knowledge-20261021/evaluation-live.json)的根因类别、拒答与误报等语义指标保持 `NULL`，不能据两组样例声称经验提升准确率。

已完成与待验收证据逐项见 [交付范围矩阵](docs/DELIVERY_MATRIX.md)，过程见 [开发日志](DEVELOPMENT_LOG.md)。不能把实现列表理解为所有环境验收均已通过。

剩余验收和外部配置见 [全量实现的剩余验收](docs/CAPABILITY_GAPS.md)。企业登录与异地备份展示不列为待办；AI按用户后续要求已接通。当前新机4核8GB单机，最终镜像统一安全扫描、完整容量/长期稳定性及物理多节点容灾仍有验收边界。

## 工程结构

| 目录          | 职责                                                   |
| ------------- | ------------------------------------------------------ |
| contracts     | 单一 Proto / TaskKind 真源                             |
| core          | C++ Server、Agent、安全进程执行、受控负载及测试        |
| api           | Go 身份、权限、REST/SSE、调度及测试                    |
| analyzer      | Python 解析、分析、租约、对象存储及黄金样本            |
| agent_runtime | 官方 AgentScope ReAct、DeepSeek 模型适配及私有工具回调 |
| web           | React 界面、独立 API Client、可视化、浏览器测试        |
| db/migrations | PostgreSQL schema                                      |
| deploy        | Compose、证书/Secret 初始化与运维脚本                  |
| ops           | 对象生命周期、完整性维护、监控状态与告警审计           |
| docs          | 协议、部署、验收与范围说明                             |

GitHub 当前工程以 tx-drop 为准；旧项目文件由新的完整快照替换，旧提交仍可回退。[项目上下文](docs/PROJECT_CONTEXT.md) · [文档索引](docs/README.md) · [发布方式与公开范围](docs/GITHUB_PUBLICATION.md)。

## 构建与启动

本地开发使用独立环境，不读取生产 `.env`、Secret 或数据卷。需要 Python 3.11+、Docker 的 Linux 容器环境和 Compose 2.24.4+：

```sh
python scripts/dev.py doctor
# 首次环境如缺少旧版 MinIO 缓存，先从官方同版本二进制及固定 SHA 构建依赖。
docker build -t minio/minio:RELEASE.2025-07-23T15-54-02Z -f deploy/minio/Dockerfile .
python scripts/dev.py up
python scripts/dev.py smoke
```

MinIO 原容器仓库当前不能匿名拉取；上述依赖重建使用官方固定版本二进制，详细来源、SHA 和与生产缓存镜像的区别见 [MinIO 构建说明](deploy/minio/README.md)。

打开 `http://127.0.0.1:18080`。账号文件位置由启动器输出，口令不打印。停止执行 `python scripts/dev.py stop`，保留本地数据；状态/日志分别使用 `status` / `logs`。前端热更新执行 `cd web`、`npm ci`、`npm run dev:local`。Windows也可使用 `powershell -File scripts/dev.ps1 up`。默认Agent只采集自己的受控真实进程，未开放宿主PID、Docker socket或BPF额外权限。更多说明见 [开发与验证](docs/LOCAL_DEVELOPMENT.md)。

生产已配置，使用稳定入口与冻结镜像，不重复生成或覆盖现有Secret：

```sh
cd /opt/drop-current/deploy
docker compose up -d --no-build --pull never
bash reload-proxies.sh
```

服务器`.env`已选择tools/helper/BPF、HTTPS、监控、小内存和冻结release覆盖。helper在Agent重建后须重新创建以加入其PID namespace；完整步骤见发布说明。全新环境按[运维手册](docs/RUNBOOK.md)选择覆盖并初始化，不能把旧Compose整套覆盖到已加密生产存储。小服务器串行构建和验收，保留所有命名发布镜像与数据卷。

启动顺序包含 owner migration、`permission-init`、`storage-permission-init` 和私有 bucket 初始化。API/Server/Analyzer 使用共享 `drop_app` 运行期角色，只授予项目表 DML 和序列使用权限；迁移使用独立 owner DSN。`pg_dump` 不包含全局数据库角色，迁移到新主机恢复后须重新执行 bootstrap 和 `permission-init`，见运维手册。

perf 覆盖配置向受控 Agent 增加 CAP_PERFMON 及 `perf_event_open`。全工具、BPF、监控、HTTPS 等覆盖文件的选择与额外权限见运维手册；它们不是默认全部启用。普通业务 Agent 默认不启动受控负载，Compose 示例显式开启用于验收。MinIO backend 使用 UID 10001、全部 capability 移除及只读根目录；新增 SSE-S3 主密钥须单独离线托管。首次转换既有数据卷须安排维护，不能在写入中直接递归改 owner。

下面的测试分别从项目根目录进入对应模块执行：

```bash
cd api && go test ./... && go vet ./...
cd web && npm ci && npm test && npm run build
cd analyzer && python -m unittest discover -s tests -v
```

C++ 的 Linux 严格编译与 CTest 包含在 `core/Dockerfile` 的构建阶段；真实PG/S3集成测试见各模块 README / DEVELOPMENT.md。

运行、备份、恢复、升级和回滚见 [运维手册](docs/RUNBOOK.md)。
