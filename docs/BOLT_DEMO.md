# BOLT 现场采集与离线优化

最终三角色 AppArmor 策略发布后，又以新 PID 完成 [任务 81](https://82.157.81.79/tasks/81)：真实 `cycles:u` 采样得到 161 个样本、3 个节点，26.4 秒完成采集、分析与优化，9 项制品实际下载的大小和 SHA-256 全部一致；随后固定输入的 6 组交替对照输出、退出码均一致，总用时 27.8 秒。CPU 中位数比为 0.9942，仍未观察到 CPU 加速；wall 比 1.0332 不作收益结论。该任务绑定已部署 Agent/Helper `20261020.3`、真实目标 ELF SHA/build-id 和完整 MMAP2 身份证明，详见[最终现场证据](evidence/demo-completion-20261020/bolt-controlled-task-81.json)与[任务 81 的六组对照](evidence/demo-completion-20261020/bolt-equivalence-task-81.json)。旧任务 57 及所有失败记录均保留。

同一最终策略下串行执行的 Go CPU/heap、Java CPU/alloc/lock/HPROF、GPERF CPU/heap、JEPROF heap、资源快照、固定脚本及 BOLT 共 12 项真实回归全部通过，用时 103.0 秒；每项按自身格式校验非空含义，逐一核对 RAW 下载 SHA/大小。资源快照验证 4 个实际读数，脚本验证固定模板成功退出，二者不把零栈权重误判为采集失败。另行只读检查确认 10 个受控目标均运行、未 OOM、无残留 tracer/停止状态；Go CPU 按纳秒、heap 按字节报告权重，不把权重解释为观测次数。见[12 项现场回执](evidence/demo-completion-20261020/final-runners.json)、[单位和目标恢复核对](evidence/demo-completion-20261020/final-runners-result-units.json)及[Agent 对 10 个受控目标的身份只读预检](evidence/demo-completion-20261020/final-agent-target-identity.json)。本轮没有导入 profile 或调用模型。

2026-10-20，已正式部署的 Agent/Helper `20261020.3` 使用专用目标完成 [任务 57](https://82.157.81.79/tasks/57)：20 秒、99Hz 的真实 cycles 采样，经严格身份检查、fdata 解析和离线优化，在 26.3 秒内完成全链。结果有 145 个样本、3 个节点，9 项制品实际下载的大小和 SHA-256 全部一致。优化 ELF 随后在无网络隔离容器中执行 6 组交替对照，固定输入的输出和退出码一致；总验收用时 27.7 秒。详见[原生采集证据](evidence/demo-completion-20261020/bolt-controlled-task-57.json)与[独立对照证据](evidence/demo-completion-20261020/bolt-equivalence-task-57.json)。

本次 CPU 中位数比值为 0.9965，未观察到 CPU 加速；wall 中位数比值 1.1860 受容器配额与调度影响，不作为业务性能收益结论。修复了采集和优化链路，不把“生成二进制”解释为必然更快。前置软件验收包含 C++ warnings-as-errors 构建及 18 项 CTest（由整合构建执行），以及[真实 Unix IPC 的 8 项测试](evidence/demo-completion-20261020/bolt-ipc-final3.json)；IPC 使用明确标记的导入 profile，原生采样证据单独来自任务 57。未上线的旧顺序候选 2 被同一 IPC 测试[负控实际拒绝](evidence/demo-completion-20261020/bolt-ipc-negative-control.json)，修正后的候选 3 通过。

任务 57 的[公网页面检查](evidence/demo-completion-20261020/bolt-collector-ui-2026-10-20T07-34-51-112Z/summary.json)复用既有登录态，只读取结果与制品：1440px 桌面、375px 手机的 BOLT 详情和 9 项制品均可见，0 页面异常、0 横向溢出，并再次核对 RAW 下载哈希。截图与回执保留在同一证据目录。

这组旧截图来自 Web `20261020.9`，保留以审计顶部曾把 no-LBR 的“分支观测/预测失败”显示为 0 的问题。Web `20261020.10` 已上线修复：basic_samples 与来源不明的数据标为“未测量”，明确说明本次没有采集分支/预测失败；实际branch模式中的正值与实测0仍正常显示。4项明确导入的SSR渲染fixture及生产构建通过，并在现有任务57上完成桌面1440/手机375真实只读复验、压缩火焰图加载、9项制品可见与RAW大小/SHA校验，0页面错误、0横向溢出；没有重新采集或执行ELF掩盖旧显示。见[修复综合回执](evidence/completion-20261020/bolt-ui-counters-fix.json)与[新版截图/现场回执](evidence/completion-20261020/collector-ui-2026-10-20T07-41-55-181Z/summary.json)。

BOLT 的输入是原生 ELF 的执行采样。Python、Java 的运行时分析应选择对应采集器；持续睡眠或主要执行系统调用的 I/O 进程也不适合演示 BOLT 的函数布局优化。

旧任务 [43](https://82.157.81.79/tasks/43) 保留为失败记录。它选择了 I/O 负载，采样与版本匹配被拒；没有进一步证明它只有一个失败原因，也没有使用 `-ignore-build-id`。2026-10-20 使用真实原生 CPU 负载新建的 [任务 49](https://82.157.81.79/tasks/49)，已完成采集、fdata 解析、上传对应 ELF 和生成优化 ELF；9 项制品下载的字节数与 SHA-256 一致。它只命中 3 个主 ELF 样本，证明链路可运行，不能证明性能收益。

专用纯整数目标的首次 [任务 54](https://82.157.81.79/tasks/54) 记录了 181 个真实 cycles 样本，仍被 build-id 校验拒绝。进一步检查确认：目标 ELF 只存在于另一个 mount namespace，perf 没有写入目标 build-id。修复使用 [Linux perf inject 官方提供的身份补充机制](https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-inject.txt)：只处理本次 Runner 自行采集的原始 perf，先核对 MMAP2 中的 PID、设备和 inode 与冻结 ELF，再从同一 `/proc/PID/exe` 读取真实 SHA/build-id；仅补缺失的身份元数据到独立副本，保留原始数据及其 SHA。已有冲突 build-id、错误 PID/设备/inode、截断或无可证明映射都直接拒绝，perf2bolt 继续严格检查。导入文件不会进入这条补充链路，不新增宿主权限，不修改采样事件。

当前身份补充器验证本机 Linux 的 little-endian `PERFILE2`、包含设备/inode 的 MMAP2 和 20 字节 build-id。其他格式返回固定失败，不猜测或放宽身份检查。原始捕获在 helper 私有目录保留到既有 ACK/过期清理，平台上传的 RAW 仍为 fdata；并未新增可公开下载的原始 perf 制品。本轮不宣称 ARM 实机验收。

专用目标 `demo-bolt` 使用仓库中的 `core/fixtures/bolt_workload.cpp`，只执行确定性的整数 CPU 工作，不产生模拟 profile。编译启用符号表、重定位、build-id，关闭 GCC 的 blocks-and-partition；符合 [LLVM BOLT 的输入要求](https://github.com/llvm/llvm-project/blob/main/bolt/README.md)。可以使用完整的 `integrations/demo-workloads/Dockerfile` 构建新的镜像标签；也可在服务器保留的 `drop-demo-workloads:20261020.1` 上使用 `Dockerfile.bolt` 只追加这个目标，避免重复编译 Go/Java。

当前 Runner 使用真实 `cycles:u` 采样和明确的 no-LBR 聚合，不采集或声称存在硬件分支历史。LLVM 支持无分支采样，但指出这种输入的优化信息弱于分支采样。结果中的样本数和热点计数不能解释为请求耗时或业务吞吐。

新错误分类只传输固定码及固定说明，不上传 perf2bolt 的私有 stderr：

| 错误码 | 操作含义 |
| --- | --- |
| `BOLT_TARGET_MAPPING_UNVERIFIED` | 采样中的目标 PID/设备/inode、原始 ELF SHA 或 build-id 无法核对；不补入、不覆盖身份，不继续聚合 |
| `BOLT_BINARY_SAMPLES_UNAVAILABLE` | build-id 无法匹配，可能是二进制不同或主 ELF 没有命中样本；检查目标/版本/用户态工作量后重新采集，保留本次失败 |
| `BOLT_PERF_CONVERSION_FAILED` | perf2bolt 调用配置的 perf 失败；检查受信工具安装及嵌套 PATH |
| `BOLT_AGGREGATION_FAILED` | 其他聚合拒绝；由运维查看本机私有诊断，页面不接收原始 stderr |

这些都是本次任务的终止失败；不会自动放宽校验或无界重试。取消、超时、进程预算错误继续保留原有分类。

优化 ELF 默认标为 `generated_requires_regression_validation`，平台不会执行用户上传的 ELF。独立脚本 `integrations/demo-workloads/test_bolt_equivalence.py` 仅用于上述自有、已核对原始 ELF 哈希的合成负载；在无网络、无 capability、只读文件系统、有 CPU/内存/PID/超时边界的容器中进行 6 组交替执行。固定 `--once` 输入的输出和退出码必须一致，同时报告 CPU 与 wall 中位数比值；没有提升时如实记录。该对照不能代替完整业务回归，也不自动发布替换运行中的二进制。
