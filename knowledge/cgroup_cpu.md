# 容器 CPU 配额与节流

容器延迟升高而宿主机仍空闲时，应检查目标进程所在 cgroup 的 CPU 限额。先校验 /proc/PID/cgroup 与进程启动身份，避免读错容器。

## CPU throttling 的观测窗口

读取 cpu.max 和 cpu.stat，在等长窗口比较 nr_periods、nr_throttled、throttled_usec 的增量，同时记录请求量及进程 CPU。累计节流时间非零不证明当前故障；祖先 cgroup 的限制也可能影响目标。

## CPU 配额对照与处置边界

区分代码消耗增长、负载增长和配额变小。只有在容量允许且处置授权明确时才试验调整配额，并保存原值；复测仍使用原请求负载。缺少 cgroup 数据时应报告观测缺口，不用宿主机 CPU 空闲排除配额限制。

来源：https://docs.kernel.org/admin-guide/cgroup-v2.html 。核对日期 2026-09-19；字段语义参考上游，调查步骤为项目约定。
