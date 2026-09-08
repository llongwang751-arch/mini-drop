# Kubernetes 多副本部署基线

该目录把 Mini-Drop 的无状态控制面和租约型 Worker 部署为多副本，并将原生采集 Agent 部署为每节点一个 Pod。它提供滚动升级、拓扑分散、PodDisruptionBudget、资源边界和 HPA，但不把单集群清单表述为已经验证的生产高可用。

## 前置条件

- Kubernetes 1.27 或更高版本；
- 可用的 Metrics Server（HPA 需要）；
- 外部高可用 PostgreSQL 和 S3/MinIO；
- 已推送到集群 Registry 的 Mini-Drop 镜像；
- Linux 节点支持所需的 perf/eBPF 能力。托管 Kubernetes 可能禁止 Agent 所需 capability，需要单独的节点池与准入策略。

PostgreSQL 和 MinIO 不放进这个 base，是因为单副本 StatefulSet 不能提供真实 HA。生产环境应使用云托管服务或经过故障切换验证的 Operator。

## 部署

先复制 `base/secret.example.yaml` 到仓库外部，填入真实值并创建 Secret：

```bash
kubectl apply -f /secure/path/mini-drop-runtime-secret.yaml
kubectl apply -k deploy/k8s/base
kubectl -n mini-drop wait --for=condition=complete job/mini-drop-migrate --timeout=300s
kubectl -n mini-drop rollout status deployment/apiserver
kubectl -n mini-drop rollout status daemonset/native-agent
```

在实际部署前用 Kustomize overlay 替换镜像、外部服务地址、资源规格、Ingress、TLS 与节点选择器。迁移 Job 必须先成功，应用 Deployment 才允许接收真实流量；生产流水线应显式编排这一顺序。

## 已具备与未证明

已具备：Go API、Control、Diagnosis Worker、Analyzer、Web 的双副本基线；数据库锁/租约支撑多 Worker；Agent DaemonSet；滚动升级不同时下掉全部副本；HPA 与中断预算。

尚未证明：跨可用区故障切换、数据库与对象存储 RPO/RTO、Ingress/SSE 长连接重连、节点内核能力差异、证书轮换以及 6 小时以上容量与混沌测试。没有这些运行证据时，只能称为“HA-ready 部署基线”，不能称为“生产 HA 已完成”。
