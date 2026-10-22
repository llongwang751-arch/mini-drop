# 性能平台与采集扰动验证

这里执行真实HTTP计算和真实py-spy采样，采样对象只能是脚本自己新建的合成进程。脚本不接受现有业务PID、用户URL或自由采样命令；运行容器无网络、无生产凭据和数据卷，只增加本容器内必要的SYS_PTRACE。

## 测量设计

- 同一固定CPU工作负载；每次请求都核对独立固定checksum。
- 不采样、49Hz、99Hz三种条件，每种重复三次，预先固定交错顺序。
- 各窗口记录成功请求的nearest-rank P50/P95/P99、吞吐、独立失败计数、目标CPU/RSS、独立profiler CPU/RSS。
- 另执行1/2/4并发阶梯和默认30秒持续窗口。它是有界持续测量，不能称为长期稳定性验证。
- raw采样文件只在临时目录存在，报告仅保留实际权重和未知栈数。py-spy没有提供丢样计数时使用null，不能用频率乘时长猜丢样。

固定合成回归门槛为：每窗口至少10个成功请求且无错误、目标峰值RSS不超过128MiB、采样有真实结果、配对P95比的中位数不超过2且吞吐比中位数不少于0.5。这些是此工具的退化门槛，不是产品SLO；即使通过也必须报告实际扰动。

```bash
python3 scripts/quality/performance/run_isolated.py \
  --image drop-agent:20261020.3 --output /opt/drop-quality/new-run
```

默认最多300秒，容器384MiB/0.75CPU/64 PID、只读根文件系统、独立PID空间。目标和profiler都在本次容器内，不修改运行中的Agent。正常及异常路径均核对所有权标签后清理；查询失败或归属变化时保留资源并判失败。

输出`performance.json`记录测量，`isolation.json`记录实际镜像摘要、资源限制、退出/OOM/清理和源码SHA。后者原子写入，超时和控制失败也保留固定失败枚举。私有驱动日志不直接公开。

独立的 [Core心跳基准](../../benchmarks/README.md)使用真实mTLS身份、注册/心跳RPC和一次性PostgreSQL，不能把这里的profiler开销当作整个Drop Agent开销，也不能把逻辑Agent数量当成同数量的业务采集器。
