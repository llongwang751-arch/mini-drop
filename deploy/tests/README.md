# 发布快照与存储回归

## 只读发布快照

`capture-release-evidence.py` 只读取指定 Compose 项目的现有容器，输出白名单字段：本地镜像 ID/已知发布引用、容器状态/健康/启动时间/重启数/OOM、配置用户、能力、只读/特权/PID 模式、seccomp分类及原option哈希、no-new-privileges、项目网络名/共享网络模式、资源上限、命名卷与映射端口。它不输出 Env、Labels、命令、健康日志、宿主绑定路径、网络别名、原始SecurityOpt或任意异常原文，也不会启动、构建、重启或删除服务。未知镜像引用或项目外网络会明确拒绝；新增平台镜像须先审查白名单。

在已有部署的服务器上运行，使用该目录现有 `.env` 与 Compose 文件组合：

```sh
python3 /opt/drop/deploy/capture-release-evidence.py \
  --deploy-dir /opt/drop/deploy --project drop --release 20261018.1 \
  --public-url http://120.24.187.205 --include-database > release-evidence.json
```

不带 `--include-database` 时不查询数据库。带此参数时只在已运行的 `postgres` 容器内读取挂载的密码文件，执行有 5 秒语句超时和 1 秒锁超时的只读事务，输出固定表白名单中的 schema 版本与精确行数。不会输出行内容或密码。两次查询之间若 schema 改变会失败；各表行数来自同一条 SELECT 的快照，容器状态则是顺序读取。配置用户和资源上限不等于实测 UID、CPU 或内存，也不证明公网功能、TLS 或备份通过。

脱敏、范围校验、只读 SQL、输出上限与超时回归全部使用明确标记的导入 fixture，只有输出上限/超时检查运行本机短生命周期 Python 子进程，不访问 Docker 或网络：

```sh
python -m unittest discover -s deploy/tests -p test_capture_release_evidence.py -v
```

CLI 使用 `ps --all --no-trunc --orphans=false --format json`，支持 Compose 的 JSON 数组及逐行 JSON，并复核 inspect 的项目/服务标签。字段解析依据 [Docker Compose ps 官方说明](https://github.com/docker/compose/blob/main/docs/reference/compose_ps.md)。本机 fixture 回归不能替代后续服务器实际运行检查。

## 存储隔离网关回归

在项目根目录执行（服务 `minio` 网关已 healthy，使用部署环境内挂载的 Secret）：

```sh
docker compose -f deploy/compose.yml run --rm --no-deps \
  --volume "$(pwd)/deploy/tests/test_storage_gateway.py:/app/gateway_regression.py:ro" \
  analyzer python /app/gateway_regression.py
```

测试只创建随机 `drop-gateway-test-<uuid>` bucket；不查询或改动业务 bucket。通过后删除测试对象与 bucket。运行日志不打印 Secret 或签名 URL。

八项检查：签名 PUT / GET / HEAD 与 SHA-256 metadata、预签名 PUT / GET、unsigned-trailer 拒绝、query STS 拒绝、form POST STS 拒绝、管理接口拒绝、复制请求标记拒绝、复制加密 header 不能污染自己的 fixture 对象。

这组测试验证当前网关缓解措施及应用兼容性，不表示旧 MinIO 二进制已升级或所有第三方漏洞已消除。当前 backend 仅连接独立 `storage` 网络，只有网关同时连接 `control` 与 `storage`；控制面 / Agent 访问 `minio:9000` 网关，无 backend 直连。

2026-10-16 实测：初次7/8通过，form POST STS 仍到达后端并返回400。主线程新增精确根路径规则（只允许GET/HEAD）并reload后，8/8全部通过，0.117秒。随机fixture已清理。

上游依据：[unsigned-trailer 公告](https://github.com/minio/minio/security/advisories/GHSA-hv4r-mvr4-25vw)、[复制加密元数据公告](https://github.com/minio/minio/security/advisories/GHSA-3rh2-v3gr-35p9)。后续可配置已修补的 AIStor 或经过验收的 COS 存储。
