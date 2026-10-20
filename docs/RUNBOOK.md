# Drop 运维手册

当前生产：20261019.5-platform，[https://120.24.187.205](https://120.24.187.205)，16容器。稳定目录`/opt/drop-current/deploy`指向`/opt/tx-drop-20261019/source/deploy`；原`/opt/drop`与命名发布镜像保留。实际镜像、检查和已知限制以[发布说明](releases/20261019.5-platform.md)及[部署证据](evidence/upgrade-20261019/deployment-proof.json)为准。[旧运维记录](history/RUNBOOK_before_20261019_release.md)仅按其日期参考。

## 状态与健康

```sh
cd /opt/drop-current/deploy
docker compose ps
curl --fail --silent https://120.24.187.205/readyz
systemctl list-timers 'tx-drop-*'
```

匿名`/api/v1/auth/me`应为401；健康页200不能代替实际登录、采集、授权下载或恢复。禁止把展开后的Compose配置、Env、Cookie、签名URL、私钥或RAW打印到日志。

## 启动和更新

`.env`已保留完整Compose选择：base、tools、helper、helper-ebpf、https、observability、small及冻结release。更新前保存镜像ID/卷/配置并实际备份恢复，确认无活动采集和启用计划；不要覆盖任何命名发布镜像、Secret目录或现有卷。

```sh
cd /opt/drop-current/deploy
docker compose stop collector-helper
docker compose up -d --no-build --pull never --wait --wait-timeout 180 postgres storage-backend minio drop-server api analyzer agent web gateway maintenance prometheus alertmanager grafana otel-collector tempo
docker compose up -d --no-deps --no-build --pull never --force-recreate collector-helper
bash reload-proxies.sh
```

helper必须在Agent重建后加入其新PID/network namespace。Nginx静态上游名在启动时解析，重建API/S3/Web可能换IP；因此更新后刷新minio、web和gateway代理，再验证readiness/匿名401/登录/RAW下载。实际stage换IP已复现502，刷新后401，生产未变。

普通停止可用`docker compose stop`，卷与镜像保留。禁止生产`compose down -v`、删除pg/object卷或替换现有KMS。应用回退须选择已保留且迁移兼容的旧应用镜像，继续使用当前SSE存储/KMS/HTTPS配置；不要从旧目录启动整套旧Compose覆盖加密存储。证书失败回退与恢复已演练，未声称整个生产应用回退重演。

## 数据和密钥

当前PG卷`drop_pg_data`、对象卷`drop_object_data`均沿用原卷。schema5，旧成功结果可读。125个原明文对象已在停写/数据库维护栅栏内迁移SSE-S3；最终备份实际恢复并校验167个对象的类型、元数据、size/SHA及数据库引用。

```sh
cd /opt/drop-current/deploy
systemctl start tx-drop-backup.service
systemctl show tx-drop-backup.service --property=Result --property=ExecMainStatus
```

该service每日03:20加随机延迟运行，输出在root0700的`/opt/drop-backups`，包含敏感明文导出，不对外公开。备份脚本自动创建隔离数据库/桶做真实恢复，不恢复覆盖生产；手动复验为`bash restore-check.sh /absolute/backup-directory`。先检查磁盘空间，不并行执行编译、压力测试、备份和大规模采集。

KMS位于受限Secret目录，仅挂实际存储backend。另有服务器独立副本和本机`.local/recovery-keys/20261019`下的RSA-OAEP-SHA256加密恢复副本，解密需原beij.pem。私钥未放进容器，本机未保存明文KMS。数据备份不含凭据；恢复到新机必须单独取得原KMS和必要身份配置并执行权限初始化。单次恢复副本与同机备份均不称持续异地复制；异地备份展示已按用户要求移除。

## HTTPS 和内部叶证书

公网为Let's Encrypt IP证书，当前到期2026-10-26 00:24:08 UTC。`tx-drop-tls-renew.timer`每6小时检查，安装service实际执行成功。证书pair用同目录current链接发布、保留旧pair并在失败时回滚；13项事务回归通过，真实外部信任/浏览器均通过。参考[详细流程](../deploy/tls/README.md)。

内部CA不变，生产Server/API/Agent已实际换成90天新叶，当前到期2027-01-17 11:02:33 UTC。stage还故意错配API证书与私钥，实际就绪失败后恢复旧叶，重新通过helper PERF_CPU和RAW校验。生产换叶后同样通过真实PROC_CPU/PERF_CPU。`tx-drop-internal-tls.timer`每日04:40加随机延迟检查30天窗口；普通service检查新叶会直接结束。

`rotate-internal-certs.sh`固定Compose项目目录到脚本目录。stage必须使用独立副本/Secret，并在执行前后检查每个Secret挂载都来自stage目录；仅改project名不够。轮换重建Server/API/Agent和helper，再刷新代理。不要在生产故意制造错配，CA信任根轮换需要独立双CA迁移。当前脚本验收的是API就绪失败回退，不扩大为任意时刻掉电事务保证。

## 监控与资源

Prometheus9090、Grafana3000、Alertmanager9093、Tempo3200仅监听127.0.0.1，通过SSH隧道访问；Grafana用户名`drop-admin`，口令在受限`secrets/grafana_password`。7个生产scrape目标up，Grafana数据库、12个预置panel与两数据源、Tempo ready已实际核对；stage四服务trace links和实际DropServiceDown触发/恢复已有证据。合成运输探针只证明Alertmanager到鉴权审计，不代表18规则都实际触发。

`tx-drop-monitoring-status.timer`每60秒发布有限证书/备份计数。可选状态文件缺失、不可读、链接、FIFO或超限不会破坏其余metrics；Linux真实HTTP/文件系统回归通过。

小机预算明确保存在small/release覆盖：Analyzer512MiB、helper512MiB、Agent192MiB、MinIO512MiB、Grafana768MiB，队列与任务并发受限。128/384MiB Grafana和256MiB MinIO曾持续回收代码页造成未就绪/磁盘读取，已修复并复测；没有OOM不能单独证明健康。主机2vCPU/约3.42GiB，不能据短验证宣称SLA或多机HA。

## 权限与验收边界

Agent为UID10001且cap0；helper无长期凭据，仅在Agent容器namespace拥有PERFMON/SYS_PTRACE/DAC_READ_SEARCH/KILL/BPF，物理接收盘2GiB，不使用privileged、SYS_ADMIN、hostPID或Docker socket。Python内存要求目标环境含兼容memray；跨UID缺少信号权限提前拒绝。宿主systemd/K8s可选方案需要独立授权，不能套用Compose证据或自动扩大权限。

企业登录、AI模型、异地备份的界面展示已取消，后端代码保留。Analyzer/Ops高危扫描匹配尚未全部处置，最终全部语言/native现场工具覆盖、容量/ARM/物理HA仍见[剩余验收](CAPABILITY_GAPS.md)，不声明整个供应链安全门禁通过。

当前API复用rc4程序并只读挂载`../contracts/task-kinds.json`到`/app/contracts/task-kinds.json`，与Core5同一真源。回退Core3须同步恢复旧catalog配置；不要把API旧内置目录当成当前有效目录。
