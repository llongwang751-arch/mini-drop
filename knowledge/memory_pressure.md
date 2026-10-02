# 容器内存压力、回收与 OOM

RSS 高不等于泄漏。区分匿名页、文件缓存、共享页和容器限制；进程视角与 cgroup 视角的统计范围不同。

## memory.events 与压力观察

记录 memory.current、memory.max、memory.high、memory.events 的同窗增量，并观察 memory.pressure。回收压力、OOM 和进程退出应对齐时间；不可用最终一次 RSS 代替退出前观测。不同内核版本字段能力需现场核对。

## 内存泄漏的对照

保持输入与并发，分预热、稳定负载和空闲阶段观察趋势，并结合运行时堆或分配采样。缓存预热、分配器保留和泄漏需要不同处置。不要把提高上限或重启后的短暂下降当成代码修复；报告应保留观测时长与尚不能排除的解释。

来源：https://docs.kernel.org/admin-guide/cgroup-v2.html 。核对日期 2026-09-19；调查和复测门槛为项目约定。
