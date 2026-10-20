# TLS 操作与验收顺序

2026-10-19实际状态：公网受信任HTTPS及续期service通过；stage内部换叶、故意错配回滚及helper PERF_CPU通过，生产内部换叶后PROC_CPU/PERF_CPU通过，CA保持不变。4组timer/service已实际验证，见[发布证据](../../docs/evidence/upgrade-20261019/deployment-proof.json)。以下为继续运维时的步骤，不与编译、实采、备份恢复同时启动。

## 目标与输入

脚本均以自身目录确定部署根和 `secrets`。轮换脚本现显式固定Compose项目目录到自身目录；执行前后仍需检查所有Secret挂载来源。stage 必须运行 **stage 副本**中的脚本，仅设置 `COMPOSE_PROJECT_NAME` 不会把生产脚本的证书目录变成 stage。生产部署根为 `/opt/drop-current/deploy`。执行前核对同目录 `.env` 的完整 `COMPOSE_FILE`、`COMPOSE_PROJECT_NAME`、`RELEASE_TAG`、`API_RELEASE_TAG`、`WEB_RELEASE_TAG`，以及 helper 的 `DROP_HELPER_SPOOL_ROOT`。只可输出 `docker compose config --services` 和精确镜像 ID；不要输出完整展开配置、Env、Cookie 或私钥。

保留当前完整覆盖顺序，helper 在 perf/tools 之后，small 最后。公网入口增加 `compose.https.yml`；stage 需要它时必须另用 stage 端口覆盖，不能占生产80/443。使用 `--no-deps --no-build --pull never` 及明确服务名；这些选项由 [Docker Compose 官方 CLI](https://docs.docker.com/reference/cli/docker/compose/up/) 支持。不要使用无服务名的 `up` 或 `--always-recreate-deps`。

签发前必须已缓存冻结发布镜像、`nginxinc/nginx-unprivileged:1.30.6-alpine`、`certbot/certbot:v5.8.0`，不在证书操作中隐式构建或拉镜像。ACME 流量仍需要外网，公网80需能访问 challenge 路径、443需能访问正式入口；数据库/S3/gRPC不新增公网端口。

## 先在 stage 强制轮换内部叶证书

1. 确认 stage 身份、路径、CA 与生产隔离；保存 CA 公共证书 SHA、三份叶证书 serial/到期时间和相关容器 ID，不读取或输出私钥。确认无运行采集，并在该短窗口不创建新任务。
2. 在 stage deploy 目录执行 `./rotate-internal-certs.sh --force`。它使用现有 `secrets/tls/ca.crt`、受保护的 `secrets/signing/ca.key`，生成90天 Server/API/Agent 叶证书；Server CN/SAN 为 drop-server，API CN 为 drop-api，Agent CN 固定 drop-agent-aliyun-local，必须对应实际 AGENT_ID=aliyun-local。
3. 脚本只重建 `drop-server api agent`，随后重建 `collector-helper` 使其加入新 Agent 的 PID/network namespace。确认这四个 ID 按预期更换，PostgreSQL、S3、Analyzer、Web ID 不变，CA SHA 不变、叶 serial 变化。即使传 `--force`，存在采集时仍跳过，不能仅凭零退出声称轮换完成。
4. `/health/ready` 仅证明 DB/S3/API→Server 的受认证 Control.Ping。另通过已授权验收会话检查 Agent 新心跳、在线和证书到期元数据，然后执行一次短 **PERF_CPU**、核 RAW size/SHA 及分析结果。PROC_CPU 不经过 helper，不能代替此项；注册能力徽标本身也不等于实采成功。
5. API readiness 失败时脚本从私有 `secrets/rotation-*/previous` 恢复原叶并重建同一组服务；stage 必须另记录 Agent/helper 恢复情况。脚本不使用额外身份读取或扩大权限，未增加 Agent/helper 的独立脚本健康门。生产不用于故意注入错误。

## 公网 IP 受信任 HTTPS

生产的 `.env` 使用完整 HTTPS 覆盖，`PUBLIC_URL` 暂保留原值。执行 `./tls/issue-public.sh`：先仅更新 Web/gateway 为 HTTP challenge 入口，先 ACME staging 后正式签发，存储目录分离。Certbot5.8满足 IP webroot 要求；IP证书必须使用 shortlived，目前有效期为6天，见 [Let's Encrypt 官方说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)。

`public-state.sh` 校验IP、剩余至少一天和公私钥匹配。每份新pair写入 `secrets/public-tls/.bundle-*`，证书/私钥为0444、bundle和挂载根为0755，宿主外层 `secrets` 保持0700；这样只有挂载该目录的UID101网关可以读，其他服务不接收该私钥。`current` 仅允许指向同目录受控bundle，相对链接通过原子rename一次切换证书/私钥；`runtime/edge.conf` 同样原子更新。`nginx -t`、reload或本机受信任HTTPS探测失败，退出trap恢复旧current与旧edge并重载。旧bundle和原平面文件不被自动删除；本轮失败只删除本轮创建且inode匹配的bundle，未知目录不会被递归删除。相同pair的定时检查不产生重复bundle。

若连回滚检查/重载也失败，脚本报 `PUBLIC_TLS_ROLLBACK_FAILED`，保留本轮bundle和0700回滚目录供恢复，不能把该错误记作已恢复或继续发布。正常成功和正常回滚会清理本轮私有临时备份；只有双失败保留恢复文件。

本机验证成功后，再从开发机以系统信任库验证 `https://120.24.187.205/healthz`，检查HTTP跳转和浏览器无证书告警。随后把生产 `PUBLIC_URL` 改为HTTPS，仅执行：

```sh
docker compose up -d --no-deps --no-build --pull never --force-recreate api
```

实际登录仅断言 session Cookie 的 Secure/HttpOnly/SameSite 属性，不输出 Cookie 值或完整响应头。再次验证授权制品、SSE、Web及API就绪。此API重建不要求重建Server/Agent/helper。

最后安装并校验 `tx-drop-tls-renew.*`、`tx-drop-internal-tls.*`、`tx-drop-monitoring-status.*`，在串行队列空闲时启用timer。公网检查每六小时、内部叶每天检查30天窗口；监控只读取受控 `public-tls/current/fullchain.pem`。实际续期、timer状态与过期指标另存证据，不能只因文件存在就宣称通过。

## 本机回归的范围

`python -m unittest discover -s deploy/tests -p test_tls_publication.py -v` 执行真实Bash退出trap、mv/install/cmp及配置边界，OpenSSL/Docker/curl/flock使用明确的假命令和导入证书内容。Windows环境无法创建原生symlink时，只有ln/readlink用标记文件模拟；Linux上使用真实symlink。它不签发证书、不访问服务器，不能代替Linux UID101挂载读取、真实Nginx重载/回滚、内部轮换和公网受信任证书验收。
