# malloc/free 探针兼容边界

这里说明 `MEMORY_TRACE / category=alloc` 的内核探针接入；`mmap` 和页故障是独立事件类型，不能作为 malloc 成功的替代证据。

## 当前主机的失败原因

2026-10-20 在 82.157.81.79 的受控 `drop_io_demo` 上进行了独立、有界探测，没有重启生产 Agent 或 Helper。

- 内核为 `5.15.0-181-generic`，bpftrace 为 `0.20.2`，`uprobe_multi: no`。
- 原配置把 tracefs 整体挂载为只读。BCC 的单探针回退需要通过 `uprobe_events` 注册；原行为因 EROFS 失败。
- 单独把该文件设为可写后，Docker 默认 AppArmor 仍拒绝其写打开。没有禁用或修改 `docker-default`。
- 专用策略允许该单文件后，内核又明确报告 `Event name is too long`。bpftrace 的 `-p` 会为路径增加目标根目录前缀，BCC 用路径组成事件名，长的容器 `/proc/<pid>/root/...` 路径因此超过内核限制。
- 使用目标 `/tmp` 下的短别名，并启用专用策略和单文件挂载，独立 1 秒探测实际观察到 41 次 malloc；进程正常退出，探针注册集合前后相同。这个诊断正例不等同于平台任务已发布验收。

## 实现与权限

`AllocatorProbeAlias` 由 Helper 内部构造。它核对目标 PID、启动时间、UID、命名空间和可执行文件身份；库的设备和 inode 必须与 `/proc/<pid>/maps` 的映射一致。它持有源库描述符，短别名仍指向相同 inode，不复制库，不修改目标业务代码。

目标必须有 root 拥有的安全 `/tmp`；可被其他用户写入时必须带 sticky 位。短目录由 root 创建为 `0700`，并使用随机名称。采集前后核对目录、链接和库的身份；结束、异常和取消时通过持有的目录描述符删除自己的链接和空目录，不递归删除，不删除被替换的无关文件。缺少安全临时目录时直接失败。

回收边界：进程被 SIGKILL 时无法执行析构；构造中已创建对象但首次 inode 校验失败时，也不会删除身份未知的对象。这两种情况可能留下 root 私有小目录。旧 BCC 的 tracefs 回退若在工具执行 detach 前被硬终止，也可能留下没有活跃 perf FD 的注册条目。当前没有按目录名扫描业务 `/tmp` 或无差别删除内核探针的恢复动作；正常完成和请求取消已验证回收，SIGKILL 后的自动恢复没有纳入这次通过结论。

`malloc` 与 `free` 的符号仍须分别精确展开为一个允许的符号，采集仍使用 `-p` 和 PID 命名空间条件，事件数量、栈深度、输出大小、工具 CPU 和墙钟时间仍受限制。退出门控会先停止事件生产再排空异步栈。

可选文件 `deploy/compose.helper-uprobes.yml` 应叠加在 eBPF Helper 配置之后。它保持整个 tracefs 只读，只把 `uprobe_events` 单文件可写地挂给 Helper；没有给 Agent 新权限，没有增加 `SYS_ADMIN`，没有 Docker socket 或存储凭据。

专用 AppArmor 文件定义 Helper、Controller、Target 三种角色。在目标机器先校验、加载，再启用 overlay：

```sh
sudo apparmor_parser -Q -T deploy/apparmor/drop-collector-helper
sudo apparmor_parser -Q -T deploy/apparmor/drop-diagnostic-target
sudo apparmor_parser -Q -T deploy/apparmor/drop-agent-controller
sudo apparmor_parser -r -T deploy/apparmor/drop-collector-helper
sudo apparmor_parser -r -T deploy/apparmor/drop-diagnostic-target
sudo apparmor_parser -r -T deploy/apparmor/drop-agent-controller
```

Python 内存采集还需要目标允许调试器的停顿、继续和 ptrace。新 Helper 首轮生产回归中，malloc 任务 58、perf 59 和 Python CPU 60 均通过，但 Python 内存任务 61 因目标 `docker-default` 拒绝来自 Helper 的 SIGSTOP 而超时。失败后目标继续执行、TracerPid 为 0，失败记录保留在 `production-first-memray-failure.json` 和 `production-memray61-diagnostics.json`。

首次只切换三个 Python 目标后，内核审计进一步确认原 Agent 的 `docker-default` 发起侧缺少对新 Target peer 的 `ptrace read`；因此不能只修改接收侧。最终使用 `drop-agent-controller`、`drop-diagnostic-target` 和 `drop-collector-helper` 三种明确角色。Controller 和 Target 保留 Moby 默认的 mount、proc、sysfs 和网络限制；Controller 对 Target 只有 `ptrace(read)`，用于冻结身份和读取 `/proc`，没有新增信号发送或 trace 权限。Target 只允许 Controller 的 `readby`，不能反向读取 Controller 可执行文件或跟踪 Controller。Helper 可对两种角色发信号和 trace/read，双方仅增加对应的 receive/readby/tracedby；Controller 中的内置合成工作负载也沿用这一对称边界。目标仍受 PID、UID、命名空间、版本身份和固定工具模板约束。

`deploy/compose.helper-targets.yml` 给 Agent 及全部十个受控业务/合成目标设置相应角色，保留 UID10001、cap_drop=ALL、no-new-privileges、默认 seccomp、原命名空间与只读根文件系统。没有修改系统 `docker-default`，没有为新角色增加 Linux capability。

真实隔离回归 `deploy/tests/apparmor_peer_matrix_isolated.py` 把三个策略复制为随机独立名称，在自己的 PID/网络命名空间内启动新 UID10001 目标；不会重载生产策略。已验证 Controller 读取目标 exe/stat/status/ns 成功、USR1 被拒，目标反向读取 Controller exe 被拒；Helper 对目标的 QUIT、STOP/CONT、真实 ptrace attach/detach 成功，恢复后 TracerPid 为 0，且无 SYS_ADMIN。自有容器和临时策略均已清理。记录为 `docs/evidence/malloc-20261020/final-apparmor-peer-matrix.json`；它证明角色权限，生产采集回归另行记录。

策略使用写允许列表。独立容器已验证 `uprobe_events` 可写打开，而 `kprobe_events`、`trace_marker`、`events/enable` 和 `perf_event_paranoid` 都不可写；策略处于 enforce 模式，且没有 `CAP_SYS_ADMIN`。

这个接口是内核的全局注册接口，因此持有 Helper 权限的可信程序可以管理探针。请求方不能提交 bpftrace 程序、探针路径、shell 命令或任意工具参数；固定模板、进程身份校验和目标 UID 策略构成应用层边界。该配置不声称内核接口本身能逐 PID 授权。

## 回归与发布状态

需要统一构建新 Helper，编译启用 `-Wall -Wextra -Wpedantic -Werror`，运行 CTest 中的 `allocator_probe`，再执行真实隔离采集：

```sh
DROP_ALLOCATOR_BUILD=/absolute/candidate/build \
DROP_HELPER_SPOOL=/absolute/dedicated-2GiB-filesystem \
python3 core/tests/allocator_trace_isolated.py
```

该回归使用全新的合成 UID10001 进程，验证 alloc/free、实际非空栈、事件预算、取消、RAW 摘要一致、目标恢复及链接和探针清理。原始样本留在 root 私有物理有界文件系统中。还需要在正式任务中确认上传、分析和页面结果，并对其他采集器做回归，才能更新当前发布的通过结论。

初次候选 2 的新 Helper 已完成上述三项真实隔离回归，证据为 `docs/evidence/malloc-20261020/initial-candidate-isolated.json`：2 秒窗口得到 96 alloc 和 96 free；9 事件预算精确得到 5 alloc 和 4 free；取消返回 `TASK_CANCELED`。每项均核验实际用户栈、RAW 摘要、目标继续执行、临时链接删除和探针集合恢复。记录绑定 Helper SHA256，并明确它不是最终候选 3 或生产部署结论。

最终候选 `20261020.3` 又独立重跑上述三项，分别在 3.004 秒、1.981 秒和 0.644 秒通过，Helper SHA256 为 `fa4f2daf655fbf8b78d7276c636d4350b85aee6a91e3e7ea03f6a0f1cd30b4f4`。记录在 `docs/evidence/malloc-20261020/final-candidate-isolated.json`，仍与生产 API 验收分开。

最终发布已经完成：服务器启用了 `drop-agent:20261020.3`、上述 Helper 二进制及三角色策略，十个受控目标和 Helper 均跟随新 Agent 的 PID 命名空间。最终策略摘要为 Controller `6305cb9b…8748`、Target `f502c6cc…8c39`、Helper `ceaeb5c9…b855`，完整 SHA256 见验收证据。数据服务和已有卷保留。

最终生产回归为任务 62–69，记录在 `docs/evidence/malloc-20261020/production-final-regression.json`：

| 任务 | 采集器 | 实际结果 |
| --- | --- | --- |
| 62 | PYTHON_MEMORY | memray attach 成功，870605 字节，19 个节点，7.97 秒 |
| 63 | MEMORY_TRACE / alloc | 实际 100 malloc、100 free，200 条非空用户栈，7.84 秒 |
| 64 | PERF_CPU | 61 个样本，20 个节点 |
| 65 | PYTHON_CPU | 192 个样本，5 个节点 |
| 66 | MEMORY_TRACE / pagefault | 实际 200 次页故障 |
| 67 | MEMORY_TRACE / mmap | 实际映射事件，5570560 字节 |
| 68 | IO_TRACE / syscalls | 实际 I/O 事件，704512 字节 |
| 69 | PROC_CPU | 64 个采样计数，3 个节点 |

八项均经过真实采集、上传、分析和 RAW 下载 SHA256/大小校验；每次检查目标身份不变、TracerPid 为 0，且 CPU 或 I/O 继续推进。完成后共享 PID 命名空间内没有遗留 gdb、bpftrace、perf 或 memray 进程。页故障的 `total_samples` 单位是 bytes，因此任务 66 合法为 0；验收依据是 `sections.memory.pagefaults.total=200`，不把它误写为采集失败。初始通用验收器误用 bytes>0 的记录保留于 `production-final-initial-checker.json`，修正口径后重验同一任务 66 的结果和原始文件，没有补造或替换采样。

最终生产策略下另用新合成进程重跑三项隔离回归：持续窗口得到 97 malloc/97 free（2.973 秒）；9 事件预算得到 5 malloc/4 free（1.991 秒）；取消返回 `TASK_CANCELED`（0.644 秒）。三项均核验非空用户栈、完整原始文件摘要、目标恢复、短别名删除和全局探针注册集合恢复，自有容器已删除。证据 `final-deployed-policy-isolated.json` 绑定最终 Helper、镜像、测试程序及三个策略的 SHA256。最终写权限负控见 `final-policy-negative.json`，仍只有 `uprobe_events` 单文件可写，其他测试接口为 EROFS，未授予 SYS_ADMIN。上述通过结论继续受前述 SIGKILL/构造失败恢复边界限制。

## 上游依据

- [Docker AppArmor 文档](https://docs.docker.com/engine/security/apparmor/)
- [Moby 默认策略模板](https://github.com/moby/profiles/blob/main/apparmor/template.go)：默认 sysfs 写限制。
- [BCC 0.29.1 探针实现](https://github.com/iovisor/bcc/blob/v0.29.1/src/cc/libbpf.c)：先尝试 perf PMU，再通过 tracefs 注册，并按自己的事件名清理。
- [Linux 5.15 uprobe 实现](https://github.com/torvalds/linux/blob/v5.15/kernel/trace/trace_uprobe.c)：注册路径、普通文件和事件名称校验。
