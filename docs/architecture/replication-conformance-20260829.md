# Mini-Drop 复刻架构一致性验收

基准：`docs/architecture/replication-guide.md`。更新时间：2026-08-29。本文件把“代码已实现”和“真实环境已验收”分开记录，避免把 Compose 能解析写成系统已经上线。

## 当前结论

默认核心链路已经按四模块方向收敛，动态探索树和 Skill 演进链路具备代码与自动测试证据；但整个指南仍不能标记为 100% 完成。当前没有云端实例，本轮修改的是新的部署模板，没有做线上迁移兼容。

```text
React Web
  → Go apiserver（唯一浏览器 REST/SSE 入口）
  → C++ control-plane（Control gRPC）
  ↔ C++ native-agent（注册、心跳、快照、拉取、采集、结果上报）
  → PostgreSQL + MinIO
  → Python Analyzer / AI Worker
  → Go apiserver → React Web
```

Python Analysis Engine 位于 Go API 后方，`MINI_DROP_EMBED_GRPC=0`，默认不能接管 C++ 控制面。Python Agent 只保留为本地兼容 profile；云端模板的可选 showcase Agent 已切到 C++。

## 已有代码和测试证据

| 领域 | 状态 | 证据 |
|---|---|---|
| 四模块默认拓扑 | 已实现 | 默认、Control、Worker、Cloud 四份 Compose 均为 React → Go → C++ Control/C++ Agent → Python Analyzer |
| Agent mTLS | 已实现，未做镜像验收 | C++ Server 校验客户端证书；Go 和 C++ Agent 携带客户端证书；证书脚本生成 CA、server、client 证书 |
| 进程发现 | 已实现 | C++ Agent 心跳上报快照，C++ Control 有界持久化，Go 按指定 Agent 查询；Python 不再共享宿主 PID、提供该接口或在 NLP 解析时扫描自身 `/proc` |
| PID 复用防护 | 已实现 | Go 固化 boot/start/namespace/executable 身份，C++ 下发前对最新可信快照二次核验 |
| TaskKind 与 capability | 部分实现 | 选择 Agent 后，Go 目录与该 Agent 的实时 capability 共同驱动创建表单、默认参数和服务端校验；C++ collector 注册仍不是由同一文件生成 |
| 任务事务边界 | 已实现 | Task、初始 Event、Outbox 同事务提交；Go 再调用 C++ `Control.CreateTask` |
| 动态探索树 | 已实现 | 假设、工具、证据、报告、复测落库后生成新 revision；资源级 SSE 通知前端增量刷新，断线可按序号重放 |
| Skill 检索 | 已实现并有离线回归 | 硬过滤 + 结构化/BM25/特征向量混排 + 置信和歧义门槛；16 例冻结集报告可复跑 |
| Skill 生命周期 | 已实现 | 从真实探索轨迹沉淀成功/反证/转向/停止条件，支持版本、评测、隔离和回滚 |
| Analyzer 租约与重试 | 已实现 | 数据库领取、lease、迟到提交保护、有限重试、Artifact hash/大小/格式检查 |
| Web 实时更新 | 已实现 | 资源级 SSE、Last-Event-ID、revision 防倒退、断线轮询降级；自然语言和快速创建共用 Agent 可信进程快照 |
| 云端模板 | 配置完成，未部署 | 旧实例已不存在；Compose 语法已验证，没有在线 URL 或运行态验收 |

## 仍然存在的差距

1. C++ Control 和 Agent 镜像尚未在本机编译。本机 Docker daemon 未启动，也没有 Linux gRPC/pqxx 构建环境，因此新增 C++ 路径目前只有源码审计和结构测试，不能算镜像验收。
2. 指南定位的 Go Java Heap Analyzer 尚不存在。当前有 Python pprof/perf/py-spy 分析链路，但还没有 HPROF 支配树与泄漏路径的 Go 子进程实现。
3. Agent 到对象存储仍使用部署级 MinIO 凭据，不是按 Agent/Task 签发的最小权限短期凭据。mTLS 已解决传输身份，证书 subject 与 `agent_id` 的强绑定也还未完成。
4. `users/groups/group_members/group_agents`、OIDC 和完整资源组授权尚未全部落库；现有 Go RBAC/principal scope 能约束接口，但不是指南里的完整多用户模型。
5. AI、复合任务、评测等白名单路由仍由 Go 转发给内部 Python Engine。浏览器只有 Go 一个入口，但“Python 只做数据库 Worker”这一最严格形态尚未完全达到。
6. TaskKind 已驱动创建表单和 Go 校验，但 C++ registry、Proto 数字和结果页展示元数据还没有从同一份契约自动生成。
7. 核心采集器还没有统一的稳定错误码目录；目前只有关键控制错误（如 `TARGET_IDENTITY_CHANGED`）结构化，部分 Runner 仍返回文本原因。
8. 没有完成指南要求的 Linux 内核采样 E2E、故障注入、性能基线、安全扫描、多架构镜像、SBOM、备份恢复和证书轮换演练。

## 可复跑验收

```bash
python -m pytest -q
cd apiserver && go test ./...
cd web && npm test && npm run build
docker compose -f docker-compose.yml config --quiet
docker compose --env-file deploy/env/control.env.example -f docker-compose.control.yml config --quiet
docker compose --env-file deploy/env/worker.env.example -f docker-compose.worker.yml config --quiet
docker compose --env-file deploy/env/control.env.example -f docker-compose.cloud-control.yml config --quiet
```

C++ 镜像验收必须在 Docker daemon 可用后补跑：

```bash
docker build -f deploy/dockerfiles/native-control.Dockerfile -t mini-drop-native-control:test .
docker build -f deploy/dockerfiles/native-agent.Dockerfile -t mini-drop-native-agent:test .
```
