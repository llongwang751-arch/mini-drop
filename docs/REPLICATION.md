# 基础复刻

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

### 当前云端发布基线（2026-09-08，UTC+8）

- 版本目录：`/opt/mini-drop-releases/20260907T181512Z`。这是从已验收运行版本继承出的兼容修正发布：Web 展示证据导向的根因正文并兼容 `sys_metrics.v1`，Diagnosis Worker 生成具体根因，Analyzer 使用不误报契约版本的状态文案。
- 当前软链接：`/opt/mini-drop-current` 指向上述版本；本次只滚动重建 Web、Diagnosis Worker 与 Analyzer，不重建数据库、MinIO 或 Agent。
- 页面入口：`https://120.24.187.205/ai-diagnosis`。
- 专用 Agent：`control-interview-demo-agent` 为 `ONLINE`。PID 会在容器或主机重启后变化，新的现场诊断应重新发现目标并使用该 Diagnosis 内的不可变绑定。
- 故障广场目标版本：21 个服务端白名单场景，覆盖 Python、Go、Java、C++ 以及 CPU、内存、I/O、网络等方向；最终在线数量以本次发布验收报告为准。
- Agent Runtime：`HEALTHY`，请求和实际 Checkpoint 后端均为 PostgreSQL；Python Worker 镜像包含 `knowledge/`，容器内 RAG 已验证可命中。

可用以下只读命令确认当前版本，切勿用删除版本目录或数据卷代替回滚：

```bash
readlink -f /opt/mini-drop-current
```

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
