# 基础复刻

## 2026-09-19 预算与验证缺口发布

当前发布 `20260919T141800Z`，Worker 镜像 `mini-drop-knowledge:20260919T141800Z`。增量脚本 `--runtime` 的固定清单现为九个文件，新增 deadlines、service、claim_verifier；仅替换 Diagnosis Worker，其余服务不重建，无 schema 迁移。实际 Chroma 检索门禁通过，39 块索引复用。回滚使用本版 `private/rollback.compose.json` 恢复 diagnosis-worker，确认健康后将 current 指向 `20260919T141100Z`；该首轮预算版链路成功但模型全超时，之前 133900Z 镜像也保留。本次真实 LATS 回归及 HTTP timeout 限制见 [预算改进记录](../reports/architecture/agent-deadline-20260919.md)。

## 2026-09-19 Agent Runtime 增量

当前发布 `20260919T133900Z`。`release_knowledge_cloud.py --runtime` 额外复制固定的六个 Runtime/Planner 源文件，用于三路检索和工作记忆；默认不带此参数仍只更新知识。构建、建索引和检索门禁在切换前执行。本批仅更新 Diagnosis Worker，无 schema 迁移。回滚使用本目录 `private/rollback.compose.json` 仅恢复 diagnosis-worker，健康后指针回到 `20260919T132400Z`。

## 2026-09-19 知识增量发布

最新知识发布 `20260919T132400Z` 基于上一 Python 镜像增加知识文件，不重复安装依赖。`scripts/release_knowledge_cloud.py` 在新 release 目录构建层、建立不可变 Chroma 索引、运行实际混合检索评测，再仅替换 Diagnosis Worker，健康通过后切换 current。评测使用独立一次性容器，不改生产数据。两个初始尝试因容器入口降权后无法写结果目录而停止；通过仅对评测容器指定 Python entrypoint 修复，日志保留。

回滚配置在该目录 `private/rollback.compose.json`，含凭据，不得输出或提交。仅执行 `docker compose -p mini-drop-control -f <该回滚文件> up -d --no-deps diagnosis-worker` 并验证健康，再恢复旧 current 指针。其他服务和知识快照保留，不使用 remove-orphans、down -v 或 prune。详情见 [质量记录](../reports/architecture/sre-quality-roadmap-20260919.md)。

## 2026-09-19 v5 云端运行（最新）

已发布 `20260919T123800Z`，运行入口仍为 `https://120.24.187.205/ai-diagnosis`。本批沿用旧原生/Go/存储服务，更新两个 Python Worker 和 Web，并增加内网 Chroma。当前 runtime Compose、离线依赖构建、真实验收结果、回滚命令统一见 [云端发布记录](../reports/architecture/cloud-release-20260919.md)。不要把下面本地无鉴权 Compose 用于云端，也不要清除旧卷或旧镜像。

## 2026-09-19 云端恢复后的运行选择

用户已恢复云服务器，当前使用 `https://120.24.187.205/ai-diagnosis`，不依赖 Windows Docker Desktop。云端沿用原容器部署，不把“不用本机 Docker”解释为卸载云端容器运行时。当前发布仍为 `20260914T073540Z`；本地通过的 Agent v5、Chroma 混合检索和 ReAct 选项尚未发布，见 [恢复与待发布范围](../reports/architecture/cloud-recovery-20260919.md)。

恢复时两台 Worker 的 `mini-drop-control-tunnel.service` 显示 active，但 Native Agent 无法连接且心跳离线。重启该隧道与 `mini-drop-worker-agent-1` 后，三台 Agent ONLINE；原业务服务、镜像和数据卷均保留。以后以 API 新心跳为准，不能仅凭 systemd active 判断链路健康。

## 2026-09-19 Windows 本地 SRE 环境

本机使用桌面的 Docker Desktop“修复启动”快捷方式，其脚本 `D:\DockerRuntime\Start-Docker-Desktop.ps1` 将运行目录指向 `D:\DockerRuntime\LocalAppData`。这是本机环境修复，不是通用安装路径。不要使用 factory reset 或删除数据卷排障。

入口为 `http://127.0.0.1:18080/ai-diagnosis`，仅绑定回环地址。独立项目 `mini-drop-local-sre` 包含 PostgreSQL、MinIO、Chroma、C++ Control/Agent、Go API、两个 Python Worker、Web 和 Python 受控故障服务。本地关闭 API/mTLS 鉴权，不可将此配置直接用于公网。Go/Java/C++ 演示故障服务本批未启动。

```powershell
python scripts/setup_local_sre.py  # 仅首次：隐藏输入密钥，已有配置不覆盖
./scripts/local_sre.ps1 Build
./scripts/local_sre.ps1 Start
./scripts/local_sre.ps1 Index
./scripts/local_sre.ps1 Status
./scripts/local_sre.ps1 Stop       # 保留容器和卷
```

本地构建复用机器已有的 `mini-drop-python-worker:local`、`mini-drop-native-control:local`、`mini-drop-native-agent:local`、`mini-drop-apiserver:latest`、`mini-drop-web:latest` 基础镜像，不能视为全新机器的零依赖安装包。`Build` 重建当前 Web、Go API、Python Worker 和演示服务，未修改的原生组件复用现有镜像。构建中断曾留下空文件镜像，本批已无缓存重建并检查源码大小与依赖导入；数据库和对象卷没有清除。MinIO 本地健康检查直接请求 live 端点，避开缓存镜像损坏的 mc 配置。

聊天模型使用 SiliconFlow DeepSeek-V3.2，Embedding/Reranker 使用 Qwen3-4B，知识索引与 Worker 共用 Chroma。设置 `MINI_DROP_AGENT_MODEL_TIMEOUT_SEC=90`、`MINI_DROP_SILICONFLOW_ENABLE_THINKING=false`；每请求无自动重试，超时最大允许 120 秒。这是单次模型请求超时，不是诊断总墙钟保证。修改模型配置后重启 Worker。前端构建使用单个 Rayon 线程降低本机内存峰值。

```powershell
python scripts/verify_local_sre.py --output reports/local-sre/<新的报告名>.json
node scripts/verify_local_sre_browser.mjs <diagnosis_id>
```

验证脚本仅允许回环地址，临时注入 Python 源码热点，最多 300 秒自动撤销，并在 finally 再次停止。链路通过与报告证据门禁分别记录；`COMPLETED` 不代表根因已 VERIFIED，更不代表修复已验证。首次模型规划超时走规则兜底的报告保留为 `reports/local-sre/real-diagnosis-20260919-r1.json`。

## 2026-09-14 清理版本已发布

已发布 `/opt/mini-drop-releases/20260914T073540Z`，更新 Web、Diagnosis Worker、Analyzer。三个 Agent 在线，五个业务被发现；四个轻量业务网页返回 200，34 个公网静态文件与本机构建哈希一致。删除旧组件和死代码、精简文档；采集、数据库 schema 与业务数据不变。发布镜像、回滚版本和检查见 [发布记录](../reports/cleanup-release-20260914.json)。下方带日期的记录属于历史批次，21 场景严格成绩仍为 1 项通过、20 项未通过。

> 2026-09-10：21 项严格复验已全部执行，Java GC 通过，20 项未通过；随后 4 项针对性复测均未满足严格根因门禁。21 项首轮注入均清理且撤销后活动指标回落，不代表同负载代码修复。最新发布目录 `/opt/mini-drop-releases/20260910T092419Z`，Python 标签 `20260910T092419Z`、Web `20260910T091223Z`、API `20260910T073914Z`，数据库保持 `20260910_0008`。已发布目标进程匹配、JVM 事件选择、原生证据判断与真实验收入口修复。详见 [本轮验收结论](../reports/ai-diagnosis/21场景验收结论-20260910.md) 与 [严格验收协议](FAULT_PLAZA_ACCEPTANCE.md)。下面带日期的旧发布信息保留为历史记录。

## 2026-09-10 全面检查修复（已发布）

最新云端目录 `/opt/mini-drop-releases/20260910T074729Z`；后端 API 与 Python 镜像发布标签 `20260910T073914Z`，最终 Web 标签 `20260910T074729Z`。三台 Agent 新系统指标任务完成，幂等重放、独立 PostgreSQL 并发测试与浏览器检查通过。

本轮修正 Skill 离线评测与真机验收的文案边界，新增故障广场证据入口；Agent 趋势改用采样时间去重，切换机器清空历史并拒绝旧请求结果，CPU 不再裁切到 100%。离线报告字段不完整时显示错误，不生成替代分数。

后端幂等冲突先释放事务再查询已创建任务；Outbox 完成/失败回写使用行锁与领取串行化，并在取得锁后检查租约时间。Go 统一限制 V2 REST/SSE：当前 RPC 只传身份、没有完整资源范围执行，因此任一 Agent/Service/Environment 范围受限的账号返回 403，直到实现端到端范围过滤；全范围账号仍按角色授权。这是明确的功能边界，不代表多租户隔离已完成。数据库保持 20260910_0008，无新增迁移。

检查结果和发布证据见 [项目全面检查与修复](../reports/ai-diagnosis/项目全面检查与修复-20260910.md)。下方发布版本属于历史记录，最新验收状态以该报告为准。

## 2026-09-10 Agent 指标与部署兼容性

三个节点均已升级自身指标采集。`agents.latest_metrics` 由 Alembic `20260910_0008` 增加为可空 JSON，旧记录保持 NULL；Go API 必须读取该列，Control 在收到有效 `self_pstats` 后持久化。不能只升级 Agent 或只改前端为 0。

发布前先在独立 PostgreSQL schema 验证迁移，再升级生产 schema，配套使用认识新 migration head 的 Python 镜像。Windows 交叉编译的 Linux Go 二进制在镜像构建前必须 `chmod 755`，并验证容器启动；单独 import 或入口健康不能替代心跳、API 和真实任务验证。

当前目录 `20260909T170846Z`；API 为 `mini-drop-apiserver:usability-executable-20260910`，Python 两个 Worker 为 `mini-drop-python-worker:20260909T165645Z`。不要使用缺执行权限的 API 候选 `mini-drop-apiserver:20260909T165645Z`。回滚保留新增列并使用兼容 schema 的镜像，不执行数据库 downgrade。两台 Worker 在原镜像上覆盖同一新版 Agent 二进制，保留既有采集工具链、配置、证书和挂载；回滚标签为 `mini-drop-native-agent:rollback-usability-20260910`。第二台当前使用第一台作为 SSH 跳板。

部署失败与恢复、三个真实采集任务、实际镜像和指标见 [截图问题修复与验收](../reports/ai-diagnosis/截图问题修复与验收-20260910.md)。这些步骤不涉及删除生产数据或对象。


## 2026-09-09 隔离恢复与 perf 质量检查

在当前 Linux Control 主机使用 `python3 scripts/verify_backup_restore.py --output /opt/mini-drop-backups/<未存在的新目录>`。脚本默认定位 `mini-drop-control-postgres-1` 和 `mini-drop-control-diagnosis-worker-1`，其他项目通过 `--postgres-container` / `--worker-container` 指定；不依赖当前目录隐式选中 Compose。PostgreSQL 使用导出快照保持 dump 与行数核对一致，恢复至新 test 数据库；MinIO 文件备份后恢复至新桶，逐个比对 SHA-256。脚本不覆盖生产数据、不清理测试资源，不把凭据写进命令行或报告。它是同主机恢复演练，不是异地容灾或定期备份策略。

perf 采样文件是否有样本由实际 `PERF_RECORD_SAMPLE` 记录判断，不再使用 16 KiB 阈值。空闲/阻塞进程可能确实没有 CPU 样本；默认 cycles 可补采一次 cpu-clock，仍为空则显式失败，并建议使用 wall/lock 或系统指标。文件结构依据 [Linux perf 文件头](https://raw.githubusercontent.com/torvalds/linux/master/tools/perf/util/header.h) 和 [perf 事件 ABI](https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/perf_event.h)，完整分析与证据质量门禁仍由 Analyzer 负责。


## 角色

- Control：运行 PostgreSQL、MinIO、Go API、C++ Control、Analyzer、Diagnosis Worker 与 Web。
- Worker：运行 C++ Agent，通过 mTLS 与 Control 建立连接。
- 浏览器：只连接 Control 的 HTTPS 地址。

## Control

```bash
cp deploy/env/control.env.example deploy/env/control.env
# 填写 CHANGE_ME、域名/IP、证书路径和模型密钥
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml up -d --build
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml ps
```

2 核或 4GB 级别的云机不要并行重建全部原生镜像。`native-control.Dockerfile` 与 `native-agent.Dockerfile` 默认使用 `NATIVE_BUILD_JOBS=1`，发布时还应设置 `COMPOSE_PARALLEL_LIMIT=1`，按 `control-plane → demo-agent → 其他服务` 顺序构建。更大的构建机可以显式提高 `NATIVE_BUILD_JOBS`，但上线前仍要观察内存和公网健康，不能让构建抢占正在演示的服务。

生产环境至少配置：数据库密码、MinIO 密钥、API Key、gRPC Token、Control 证书和模型 API Key。`MINI_DROP_AI_ENABLED=full` 时建议启用 PostgreSQL Checkpoint。

### 面试现场故障环境

`interview-demo` 是显式启用的 Control profile。它包含默认空闲的 Python、Go、Java、C++ 四个故障实验服务和一个只观察 Control 宿主机的 `control-interview-demo-agent`。故障服务不映射公网端口；专用 Agent 使用现有 CA 签发的独立证书，不能复用两台 Worker 的证书。

首次启用时，在 Control 上签发一次证书（已有证书时跳过，绝不重建 CA）：

```bash
cd /opt/mini-drop
test -f deploy/certs/control/ca.key
test -f deploy/certs/control/ca.crt
if ! test -f deploy/certs/control/agents/control-interview-demo-agent/agent.crt; then
  bash deploy/scripts/issue-agent-cert.sh \
    control-interview-demo-agent deploy/certs/control
fi
```

面试环境必须同时加载两个环境文件：先加载保存密钥和公共配置的 `deploy/env/control.env`，再加载保存四类故障实验服务覆盖项的 `deploy/env/interview-demo.env`。三机 SSH 隧道方案要求每台 Agent 都能在自己的回环地址访问 `127.0.0.1:19000`：

```dotenv
MINIO_AGENT_ENDPOINT=127.0.0.1:19000
INTERVIEW_DEMO_MINIO_PORT=19000
MINI_DROP_FAULT_LAB_URL=http://python-hotspot:8081
MINI_DROP_FAULT_LAB_GO_URL=http://go-hotspot:6060
MINI_DROP_FAULT_LAB_JAVA_URL=http://java-hotspot:8082
MINI_DROP_FAULT_LAB_CPP_URL=http://cpp-hotspot:8084
INTERVIEW_DEMO_AGENT_ID=control-interview-demo-agent
INTERVIEW_DEMO_AGENT_CERT_DIR=./deploy/certs/control/agents/control-interview-demo-agent
```

这里不仅要让 Compose 读取 env 文件，还必须把 `MINIO_AGENT_ENDPOINT` 显式传进 `diagnosis-worker` 容器。Diagnosis Worker 为受控诊断创建 Task/上传授权；如果它仍使用 Compose 内部地址 `minio:9000`，采用 host network 的 demo Agent 无法访问该地址，任务会卡在上传阶段。部署后可检查展开配置中 `diagnosis-worker.environment.MINIO_AGENT_ENDPOINT` 是否为 `127.0.0.1:19000`，但不要打印同一 env 文件里的密钥。

先验证展开后的编排，再只重建读取了新配置的 Diagnosis Worker 和两个 demo 服务：

```bash
docker compose --env-file deploy/env/control.env \
  --env-file deploy/env/interview-demo.env \
  -f docker-compose.control.yml --profile interview-demo config --quiet
docker compose --env-file deploy/env/control.env \
  --env-file deploy/env/interview-demo.env \
  -f docker-compose.control.yml up -d --no-deps --force-recreate diagnosis-worker
docker compose --env-file deploy/env/control.env \
  --env-file deploy/env/interview-demo.env \
  -f docker-compose.control.yml --profile interview-demo \
  up -d --build python-hotspot go-hotspot java-hotspot cpp-hotspot demo-agent
docker compose --env-file deploy/env/control.env \
  --env-file deploy/env/interview-demo.env \
  -f docker-compose.control.yml --profile interview-demo \
  ps python-hotspot go-hotspot java-hotspot cpp-hotspot demo-agent diagnosis-worker
```

不向宿主机暴露 8081，因此从 Diagnosis Worker 内验证目标：

```bash
docker compose --env-file deploy/env/control.env \
  --env-file deploy/env/interview-demo.env \
  -f docker-compose.control.yml exec -T diagnosis-worker \
  python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://python-hotspot:8081/snapshot', timeout=2)))"
```

输出应显示所有故障状态为未激活。页面点击“启动并诊断”后必须产生新的 Diagnosis/Task/Artifact ID，并在超时后恢复空闲；这才是现场复现，不是历史回放。不要执行 `docker compose down -v`，也不要删除 `pgdata`、`miniodata`、环境文件或 PKI。

### 故障标签发布与回滚（20260909T134853Z）

当前目录为 `/opt/mini-drop-releases/20260909T134853Z`，来源为 `20260909T111410Z`。本轮先在原部署上重新完成其余 17 项真实全链路验收，再使用 `python-worker-source-overlay.Dockerfile` 从原运行镜像构建单文件源码叠加层，只替换 `server/app/drop_insight/fault_plaza.py` 的验收元数据。未改动 Collector、Planner、证据门禁或数据库合同。

新镜像：`mini-drop-python-worker:20260909T134853Z`；摘要：`sha256:e62c6a92a42ffd9de4bcdb44f550db980d8de80367c003ecf012db80a1bee042`。只滚动替换 `diagnosis-worker`，其余服务容器 ID 不变。公网 21 个验收标签、17 条新诊断跨重启读取及全部故障停止均已核对，详见 [故障广场其余17场景全链路复验-20260909.md](../reports/ai-diagnosis/故障广场其余17场景全链路复验-20260909.md)。

首次发布因健康响应封装解析错误回滚，修正读取 `data.dependencies` 后发布成功；不要将该校验脚本问题记为故障场景验收失败。

需要回滚时同时恢复 Python 镜像、版本软链接和 Diagnosis Worker 容器；禁止仅改链接后声称容器已回滚：

```bash
set -eu
test "$(readlink -f /opt/mini-drop-current)" = /opt/mini-drop-releases/20260909T134853Z
docker image inspect mini-drop-python-worker:rollback-pre-20260909T134853Z >/dev/null
docker tag mini-drop-python-worker:rollback-pre-20260909T134853Z mini-drop-python-worker:local
ln -s /opt/mini-drop-releases/20260909T111410Z /opt/mini-drop-labels-rollback-20260909T134853Z
mv -Tf /opt/mini-drop-labels-rollback-20260909T134853Z /opt/mini-drop-current
cd /opt/mini-drop-current
COMPOSE_PARALLEL_LIMIT=1 docker compose -p mini-drop-control \
  --env-file deploy/env/control.env --env-file deploy/env/interview-demo.env \
  -f docker-compose.control.yml up -d --no-deps --no-build --pull never --force-recreate diagnosis-worker
curl --fail --cacert deploy/certs/control/ca.crt https://120.24.187.205/api/healthz
```

### 前端发布基线（2026-09-09，保留原始记录）

- 版本目录：`/opt/mini-drop-releases/20260909T111410Z`。从 `20260908T094648Z` 复制完整运行基线与配置后覆盖本轮 Web 源码和本地已测试的 `web/dist`；新首屏、报告摘要、紧凑驾驶舱、拓扑优先和失败刷新保留已上线。
- 当前软链接：`/opt/mini-drop-current` 指向上述版本；本次只滚动替换 Web。部署前后其余 Control Compose 容器 ID 全部相同，数据库、MinIO、后端与 Agent 均未重建。
- 页面入口：`https://120.24.187.205/ai-diagnosis`。
- 专用 Agent：`control-interview-demo-agent` 为 `ONLINE`。PID 会在容器或主机重启后变化，新的现场诊断应重新发现目标并使用该 Diagnosis 内的不可变绑定。
- 故障广场目标版本：21 个服务端白名单场景，覆盖 Python、Go、Java、C++ 以及 CPU、内存、I/O、网络等方向；最终在线数量以本次发布验收报告为准。
- Agent Runtime：`HEALTHY`，请求和实际 Checkpoint 后端均为 PostgreSQL；Python Worker 镜像包含 `knowledge/`，容器内 RAG 已验证可命中。

可用以下只读命令确认当前版本，切勿用删除版本目录或数据卷代替回滚：

```bash
readlink -f /opt/mini-drop-current
```

#### 本轮前端发布与回滚

本地先通过 Web 33 文件/137 tests、`npm run build:check --prefix web` 和合成数据浏览器回归，再以受控文件清单上传前端源码及构建产物。发布包不包含本地密钥或环境文件，SHA-256 为 `690f2a5aeafa1ba930b5cb66b14a0821970162e9ca6032205fced34acd8029a7`。服务器从上一运行目录继承配置和 PKI，不生成新 CA。

`deploy/dockerfiles/web-prebuilt.Dockerfile` 的构建上下文严格限定为 `web/dist`，`WEB_RUNTIME_IMAGE` 指定已运行 Web 的镜像摘要或本轮专用回滚标签。它保留 nginx 运行环境与旧哈希资产，使已打开页面仍能读取旧 lazy chunks；不会在小型 Control 主机安装 npm 依赖。普通源码复刻仍使用 `web.Dockerfile`。

本轮镜像标签为 `mini-drop-web:20260909T111410Z`，摘要为 `sha256:83de5069e2616327221ee9e51bc3ff7803c006ed3b7b8cfdfab046faf234262e`。构建及 Compose 静态校验成功后才原子切换软链接，并运行 `up -d --no-deps --no-build --pull never --force-recreate web`。发布记录为 `reports/ai-diagnosis/frontend-deploy-20260909T111410Z.json`；公网 45 个文件与本地逐字节匹配，记录为 `frontend-assets-20260909T111410Z.json`。真实健康端点 `/api/healthz` 的三个依赖均为 healthy；这些检查不代表重新运行了历史 21 场 live Campaign。

若该版本需要回滚，在 Control 执行以下命令。旧目录和镜像必须一起恢复，仅修改软链接不会更新容器：

```bash
set -eu
test "$(readlink -f /opt/mini-drop-current)" = /opt/mini-drop-releases/20260909T111410Z
test -d /opt/mini-drop-releases/20260908T094648Z
docker image inspect mini-drop-web:rollback-pre-20260909T111410Z >/dev/null
docker tag mini-drop-web:rollback-pre-20260909T111410Z mini-drop-web:local
ln -s /opt/mini-drop-releases/20260908T094648Z /opt/mini-drop-rollback-20260909T111410Z
mv -Tf /opt/mini-drop-rollback-20260909T111410Z /opt/mini-drop-current
cd /opt/mini-drop-current
COMPOSE_PARALLEL_LIMIT=1 docker compose -p mini-drop-control \
  --env-file deploy/env/control.env --env-file deploy/env/interview-demo.env \
  -f docker-compose.control.yml up -d --no-deps --no-build --pull never --force-recreate web
curl --fail --cacert deploy/certs/control/ca.crt https://120.24.187.205/api/healthz
```

以下场景记录保留其原始验收时间与版本，不作为本轮前端发布的新诊断结果。

线上验收对 `source-hotspot` 做了两次隔离的顺序重放：`DISABLED` 诊断 `insight_e581f5ac05704a2e96ba8a899a9786bd` 与 `AUTO` 诊断 `insight_85bff86fb8fc4c9abd72e6e26496b77b` 均为 `COMPLETED`，各有 4 个有报告执行轮次、4 次真实工具调用和 16 条 Evidence。两组 py-spy 火焰图根样本均为 2968，并都找到 `source_hot_function`；关闭组 Skill 激活为 0，开启组激活内置 `python-runtime` Skill 一次。两组实际轮次均为 `[1,2,3,4]`，LATS 精确重复事件为 0，收尾时故障为未激活。

验收报告：`reports/ai-diagnosis/interview-demo-live-acceptance-20260906T142517Z.json`；SHA-256：`9A8EA9DB4DF645B6F7CFD34C8A707D8E03E96FC719C604B09E320B1AC7146D3B`。这是同一不可变目标上的顺序受控重放，不是严格相同时间窗的性能基准；它证明可重复造新故障和生成全新服务端链路，不证明 Skill 的通用准确率或耗时收益。

### FULL_LATS 冻结回放发布验收

冻结回放和上述 `interview-demo` 真实故障是两条独立链路。它使用镜像内服务端白名单 fixture，不连接 Agent、不调用 Collector，也不需要 `MINI_DROP_FAULT_LAB_URL`；故障广场服务暂不可达时，冻结回放仍应可运行。公开 API 合同是：

```text
GET  /api/v2/showcases/lats-replays
POST /api/v2/showcases/lats-replays/{scenario_id}/runs
     body: {"client_run_id":"浏览器或调用方生成的唯一幂等键"}
```

POST 返回新 Diagnosis 的 `diagnosis_id/status/version/created/execution_mode/snapshot`，并在 `showcase.stream` 与 `showcase.exploration_tree` 给出已有事件流和树投影地址。相同用户、场景和 `client_run_id` 的重试必须返回同一 Diagnosis；新的 key 必须创建新的 Diagnosis 和节点 namespace。服务端先落盘不可变 manifest/hash，再由 Diagnosis Worker 每个 tick 推进一个可见搜索 frame；Worker 重启后从 manifest、已持久事件和 effect key 继续。

页面入口位于 **AI 诊断 → 验证与 A/B → 故障广场**顶部的“完整 LATS 冻结回放”。点击 **新建回放会话** 后应立即跳到新会话，随后 SSE 逐帧显示 UCT 选择、扩展、评估、冻结 simulation、观察、反思、回传、剪枝和停止。验收时必须看到 `FULL_LATS / FROZEN_REPLAY`、稳定 snapshot digest、逐 rollout reset proof、不同运行的 namespace，以及 `used_tool_calls=0`；冻结会话从首帧前就应为只读，不能出现 Tool Call、Task、Artifact、Evidence、Report、人工干预或 Skill 沉淀入口。

发布后运行只读/新建会话验收脚本。API Key 只放环境变量，脚本不会从命令行接收、打印或写入报告：

```bash
export MINI_DROP_API_KEY='控制台使用的 API Key'
python scripts/verify_lats_replay_showcase.py \
  --base-url https://你的控制机地址 \
  --output reports/live/lats-frozen-replay.json
```

脚本会创建两条 fresh replay Diagnosis，检查相同 snapshot digest、不同 session namespace、必需 `lats.*` 事件、树投影与终止状态。本次已部署 `/opt/mini-drop-releases/20260906T071335Z`，并从公网地址 `https://120.24.187.205` 通过双会话验收：两条运行各观察到 6 个树 revision 和 4 次冻结 simulation，snapshot digest 相同、节点集合互不相交。报告为 `reports/ai-diagnosis/lats-replay-acceptance-public-20260906T071335Z.json`，SHA-256 为 `D337B15661524799D72EB94AEB7588C624D27DFE34549EF4ADAF4EC408D2D9F6`。本地单元测试不能代替该发布验收。

## Worker

```bash
cp deploy/env/worker.env.example deploy/env/worker.env
# 填写唯一 Agent ID、Control 地址、Token 和该 Worker 的客户端证书
docker compose --env-file deploy/env/worker.env -f docker-compose.worker.yml up -d --build
```

每台 Worker 必须使用不同的 Agent ID 和客户端证书。Control 应能看到持续更新的心跳与进程快照。

Worker 上线前先生成只读兼容报告：

```bash
python scripts/check_worker_compatibility.py --target-uid "$(id -u)" \
  --output /tmp/mini-drop-worker-compatibility.json
```

它读取内核版本、`perf_event_paranoid`、`ptrace_scope`、BTF 和本机工具，不会修改 sysctl 或 capability。`perf_cpu`/eBPF 不可用时，系统仍可用 `sys_metrics` 做低开销初筛，但不能声称拿到了函数级调用栈。

## 最小验收

1. `docker compose ... ps` 中所有长期服务为 `running/healthy`。
2. Web 输入 API Key 后顶部显示实时连接，不再长期显示“轮询兜底中”。
3. 新建自主诊断时无需手填 Agent/PID；系统自动发现、绑定并开始取证。
4. 报告中的结论带有本次诊断的 Evidence 引用；证据不足时明确拒绝下结论。
5. `continuous_perf` 至少跑两个窗口并在任务详情看到窗口级火焰图/TopN/调用图。
6. 在真实控制机运行 `scripts/run_live_skill_ab_campaign.py`，保留真实 Skill A/B 的服务端 ID 链路。
7. 对每台云 Worker 运行真实多节点验收，而不是只截取“ONLINE”页面：

```bash
export MINI_DROP_API_KEY='控制台使用的 API Key'
python scripts/run_multi_cloud_acceptance.py \
  --base-url https://你的控制机地址 \
  --agent worker-tencent-1 \
  --agent worker-aliyun-1 \
  --output reports/live/multi-cloud-acceptance.json
```

脚本会在每台指定 Agent 上选择最新可信进程候选、创建一次真实 `sys_metrics` Task，并要求状态为 `DONE` 且存在 `sys_metrics` Artifact。报告只保留 Task/Event/Attempt/Artifact 的服务端 ID、hash 和状态，不保存 API Key 或产物正文。

8. 在验证中心新建两次 FULL_LATS 冻结回放，或执行 `scripts/verify_lats_replay_showcase.py`；确认同快照、不同 namespace、SSE 逐帧与重启恢复，同时确认没有真实 Task/Evidence 被伪造。

详细字段以 [`contracts/openapi.v1.json`](contracts/openapi.v1.json) 和部署示例文件为准。
