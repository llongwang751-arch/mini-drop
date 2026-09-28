# 双机负载与一小时资源观测

执行器 `scripts/run_distributed_endurance.py` 在发压机运行，通过 SSH 启动目标机上的一次性 SQLite FTS5 检索样例，仅监听目标机回环地址。发压请求经 SSH 端口转发；公网、SSH 隧道、排队及服务处理均计入端到端延迟。P95 200ms 门槛保持不变，不能拿此结果声称生产服务容量。

## 环境与部署文件

两端需要 Python 3.10+、SQLite FTS5、`psutil>=7.2,<8`。目标机只需原始字节复制以下文件，保留目录结构：

- `scripts/run_distributed_endurance.py`
- `scripts/run_load_endurance.py`
- `scripts/process_resource_monitor.py`
- `demo/rag_service/app.py`

目标机可用隔离 venv；无需启动 Docker，无需数据库或业务数据目录。通过 `--remote-python` 指定绝对解释器路径。SSH 采用已有可信 known_hosts、BatchMode 和已有密钥；不得为省略握手取消主机身份校验。两端文件 SHA 必须逐字节一致，传输时不要转换换行。

目标必须是用户授权的独立测试主机。不要与同一目标上的故障注入、构建或其他压测同时执行。记录其他常驻进程和资源限额，才能解释剩余环境影响。机器指纹来自 Windows MachineGuid 或 Linux machine-id 的 SHA-256；不同 OS 身份不能证明不同物理硬件或独占宿主机。

## 先验证链路，再运行一小时

以下 HOST、路径和密钥由部署负责人填入已经核实的目标。每次两端均使用新证据目录，拒绝覆盖。

```powershell
python scripts/run_distributed_endurance.py --host USER@HOST --identity C:/keys/test.key --remote-root /opt/mini-drop-load/SNAPSHOT --remote-python /opt/mini-drop-load/venv/bin/python --remote-output /var/tmp/mini-drop-load-quick-UNIQUE --output output/quality/distributed-quick-UNIQUE --rates 5 10 --step-seconds 6 --soak-seconds 30

python scripts/run_distributed_endurance.py --host USER@HOST --identity C:/keys/test.key --remote-root /opt/mini-drop-load/SNAPSHOT --remote-python /opt/mini-drop-load/venv/bin/python --remote-output /var/tmp/mini-drop-load-hour-UNIQUE --output output/quality/distributed-hour-UNIQUE --rates 5 20 40 60 --step-seconds 15 --soak-seconds 3600 --concurrency 128
```

短版约 57 秒，验证双机身份、远端完整阶段、请求正确性、资源采样与清理，不是小时级验收。一小时命令持续阶段 18,000 个请求，额外阶梯 1,875 次与恢复 75 次，共 19,950 个计划请求；120 个 30 秒持续窗口必须分别达标。某个高档发生服务 SLO 超限可界定测试区间；发压饱和或调度落后则使整轮 INVALID，不能解释为服务容量。

## 证据与清理

原始请求、资源记录、双方系统身份、PID/create_time、版本与源码指纹、阶段资源观测窗口、SSH 错误日志及完成报告都保存。控制通道只有阶段变更和结束两类命令，不开放远程故障接口。目标机资源仅观测本次 fixture PID，首次 CPU 无基准时保留 null。远端阶段先确认、发压端再执行；各阶段资源窗口必须连续且覆盖完整发压时长。

执行完会关闭远端 HTTP 服务与线程并等候 SSH 进程返回 0，才记录 `remote_cleanup_confirmed=true`。发压端中断关闭控制 stdin 请求清理；目标机额外有 4,500 秒硬退出上限。退出不删除任何证据目录。硬 TTL 或失联导致中断时，远端已刷盘 JSONL 仍在，但不伪造完成报告。

先执行双机专用复核，再生成图表：

```powershell
python -c "from scripts.run_distributed_endurance import verify_distributed; print(verify_distributed('output/quality/distributed-hour-UNIQUE/report.json'))"
python scripts/render_load_report.py output/quality/distributed-hour-UNIQUE/report.json --output output/quality/distributed-hour-UNIQUE/report.html
```

复核会重算每个原始请求与分窗、校验远端机器/进程/源码及清理结果。有效记录可以是 INVALID 或 FAILED；需要验收通过时使用 `verify_distributed(path, require_pass=True)`。数据摘要用 SHA 发现损坏，不是对人为整套伪造证据的密码学防护。

截至新增代码的本地检查，真实 fixture 协议、正常退出和 stdin EOF 清理已通过回归；双机实测与一小时结果以单独归档记录为准，本说明不宣称已经实跑。
