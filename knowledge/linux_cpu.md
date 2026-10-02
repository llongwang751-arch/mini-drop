# Linux CPU 诊断

先区分用户态、系统态、等待和调度压力。用户态 CPU 高需要 Profile 证明热点位置；系统态 CPU 高需要内核栈、系统调用或网络证据。CPU 指标是现象，不能单独证明代码回归。

安全探针优先级：系统指标（R1）→ 已有 Profile → 单次 perf（R2）。缺少符号时应明确返回证据不足。

## 低 CPU、正常描述和缺测

进程或线程 CPU usage / CPU utilization 要写明同一采样窗口、CPU 时间增量、墙钟时长和百分比的单核/多核口径。线程或 worker 的低 CPU 可以反驳该窗口持续计算饱和，不能反驳锁、I/O、依赖等待，也不代表业务全局健康。只有进程身份而没有有效计数时是缺测；用户只要求描述现有输入、未要求健康判定时，可说明描述范围并停止，不制造异常假设。

Linux `/proc/<pid>/stat` 的 utime/stime 是进程用户态/系统态 CPU 时间计数，需按时钟 tick 单位和采样间隔求增量。进程重启、窗口不完整、采集失败不能当作零 CPU。知识本身不能授权新探针或成为当前证据。

来源：[Linux /proc 官方文档](https://docs.kernel.org/filesystems/proc.html)；JVM 线程测量见 [Java 线程 CPU 指南](java_thread_cpu.md)。
