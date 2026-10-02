# Linux 同步写低延迟与有界反证

fsync / fdatasync 是文件同步接口；fsync 同步文件数据与所需元数据，fdatasync 可省略不影响后续数据读取的元数据。成功与失败必须分别记录，失败或缺测不能作为慢同步写的反证。syncwrite、sync_write、synchronous write 和同步落盘在此描述操作类型，不代表数据库引擎内部机制已被观测。

记录当前 PID、文件与挂载/块设备归属、窗口起止、操作类型、成功/失败次数及延迟分布。若同窗有效操作低于已声明慢写阈值，可反驳“该窗口持续慢同步写”这一候选；阈值、样本覆盖和报告来源仍走证据门禁，不能只读到这篇指南就生成 REFUTED 或根因结论。

tmpfs、缓存路径、虚拟磁盘映射和不同采样窗口限制结论范围。低同步写延迟不是物理磁盘全局正常，也不是业务全局健康；块设备等待与共享宿主争用另需设备队列、目标及邻居同窗证据。未知存储协议或数据库引擎的专属语义不能由通用同步写指南替代。

来源：[Linux man-pages fsync/fdatasync 官方项目文档](https://man7.org/linux/man-pages/man2/fsync.2.html)；[Linux 块层统计官方文档](https://docs.kernel.org/block/stat.html)。
