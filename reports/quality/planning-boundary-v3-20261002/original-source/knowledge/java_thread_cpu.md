# Java JVM 线程 CPU 与正常／缺测边界

ThreadMXBean 可报告平台线程累计 CPU 时间；先检查 isThreadCpuTimeSupported 和 isThreadCpuTimeEnabled。getThreadCpuTime 返回 -1、线程已结束、测量未启用或实现不支持都属于无有效观测，不能解释成零 CPU。Java 21 的接口不提供虚拟线程 CPU 归属；线程 ID 只在该线程生命周期内有效。

用同一 JVM/PID、同一存活线程、同一时间窗两端的有效 CPU 时间差除以墙钟时间描述线程 CPU usage。纳秒精度不保证纳秒准确度。低 CPU 或 normal CPU 只能描述该窗口，不能据此说业务全局健康，也不能反驳锁、同步写、GC 或依赖等待。

只描述输入范围且未提出异常/健康判定时，可以有界停止；要求“确认当前健康”却缺少有效观测时应明确缺测。知识是规划先验，不是当前 Evidence，不提供额外工具权限；若连接器不支持 ThreadMXBean，列出缺口而不要伪造数据或自由执行 JVM 命令。

来源：[Oracle Java SE 21 ThreadMXBean 官方 API](https://docs.oracle.com/en/java/javase/21/docs/api/java.management/java/lang/management/ThreadMXBean.html)。
