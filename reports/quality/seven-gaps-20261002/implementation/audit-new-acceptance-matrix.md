# 七项缺口的独立验收审计

这是对冻结失败记录的只读复盘及新验收要求，不是新的真机成绩。旧工程 14/21、路径 4/21、反证 5 条与严格 0/21 保持。原七项文件 SHA 固定在 `tests/test_seven_gap_acceptance.py`；摘要保存在同目录 `audit-old-seven.json`。

| 案例 | 旧失败的具体依据 | 最小可信新真机验收 | 不得偷换的结论 |
|---|---|---|---|
| source-hotspot | 4,451 份 Python 源码样本已有位置，但自由文本假设没有注册谓词；系统补采剩余 44.909 秒小于需要的 45 秒而被拒绝 | 在采集前持久化注册的 Python profile 与独立 OS CPU 合同；正确进程/启动时间、原始样本重新计算占比及实际文件行号，先规划两个工具并保留足够预算 | _sample 或监控发布函数是采到的路径，不能称它解释全部业务延迟；分别采集的两个窗口不能称同窗或跨窗稳定 |
| cpp-cpu-hotspot | 914 份 perf 样本及 64.758% OS CPU 已有，但文本 50% 与机器判据 0.5 冲突，perf 谓词没有注册 | 使用 C++ 注册合同；单核百分数阈值明确为 50；业务符号实际源文件/行号及样本占比重新计算；独立 /proc CPU 原始首尾计数 | 0.5 的单位为 0.5%，不能当作 50%；加载器/进程名/线程入口不是业务热点，累计帧占比不可相加 |
| cpp-lock-contention | OS 窗有 49,807.39ms 等待和 16,755 次获取，但假设为线程状态自由文本；后续 CPU perf 仅 8 样本而被限制准入 | 同一目标及窗口提供成功获取、累计等待、竞争次数的单调原始计数；平均等待≥1ms、竞争≥1、获取≥5 的完整数值合同；撤销后稳定 | 获取等待是有界观测，不能由 CPU 样本推出持锁者、锁对象或因果根因 |
| io-write-latency | Python 只有操作数/写字节；沒有目标同步操作累计耗时，宿主 eBPF 不能归属目标 | 真实 open/write/flush/fsync/close 的累计耗时和成功次数在同一锁快照中读取；窗口至少 5 次操作；平均 10ms 的原阈值保持；真实结果低于阈值为 REFUTED | 缺延迟不是 0ms；循环 sleep 与工作锁等待不包含在同步 I/O 耗时；tmpfs 不代表块设备瓶颈 |
| java-file-io | 694 次操作/90,963,968 字节实际写入；无 duration；宿主 eBPF P95 16ms 未绑定目标 | 与 Python 相同目标操作窗口要求，且使用实际 FileDescriptor.sync 测量；确认文件轮转超过 tmpfs 容量仍持续，成功/失败均留存 | 宿主设备慢不能证明该 JVM 文件路径慢；不提高有限 tmpfs 容量掩盖提前停止 |
| cpp-file-io | 923 次操作/120,979,456 字节实际写入；无 duration；宿主 eBPF P95 2ms 未绑定目标 | 与 Python 相同目标操作窗口要求，明确 fsync/close 成功判断，真实失败不得增加成功计数；受限轮转保留 | 字节量不是延迟或持久性证明；快速完整测量是有效反证而非异常定位 |
| noisy-neighbor | 同机 peer 增长 1,264 ticks，目标 CPU 0.428%；旧 detected=true 只因 peer 活动；无共享配额/节流观测 | 内核枚举独立子进程、稳定 PID/start_ticks/boot、真实同 cgroup；quota/period 稳定；同窗 target/peer CPU ticks 均增长，nr_throttled/throttled_usec 真正差值；零差值保留为反证 | peer_active、同宿主机活动、累计节流大于 0 均不能证明本窗竞争；仅描述共享配额节流，因果贡献需另有干预 |

所有新案例都应各自保留 baseline/fault/recovery、绑定前后身份、工具状态与被拒原因、Task/Attempt/Artifact/Analyzer/Evidence 引用、原始下载 SHA、会话收束和清理。仅原始控制快照中的数据不能直接插入 Agent Evidence。新 7 次实验另立目录和清单，不能重写旧失败后宣称旧 21 项已通过。

## 部署模板复核

现模板 `output/acceptance/interview-completion-20261001/release/deploy_runtime.py` 只更换 Worker、Analyzer、Web，其 Go 专用分支虽保留，但不能直接套给本轮三个 demo 或 native Agent。建议新部署模板显式更换需要的六个服务，保持另七个容器及 Office 的紧邻部署快照；公开 API 二进制保持。使用 `up --no-deps --no-build --pull never`，不得运行全项目 down 或删除卷。

- Python/C++ 镜像构建 context 分别是 `demo/python-hotspot` 和 `demo/cpp-hotspot`；Java 必须用仓库根 context，包含冻结且 SHA 校验的 async-profiler vendor 包。C++ 保留符号与与运行版本匹配的源码映射，不能重用旧进程源位置。
- 各 demo 恢复其真实 inspect 中的环境、健康检查、只读根、tmpfs 大小、CPU/内存/PID 限额、端口和网络；Compose 旧 env-file labels 失效时只能从当前 inspect 重建有界配置，不能引入 host PID、特权或额外挂载。tmpfs 数据是临时实验文件，不操作 PostgreSQL/MinIO/Office 数据。
- Native Agent 用已通过 CTest 的 builder 目标，经 `native-agent-source-overlay.Dockerfile` 只覆盖 Agent/bridge 二进制到既有工具镜像；构建前检查 proto/native/generated 来源，记录二进制 SHA 与 executable 位。
- Agent 的 mTLS 证书、Agent ID、host PID/host network、采样 capabilities、tracing/debug 只读挂载及独立 Outbox 卷按当前运行配置保留；不要打印环境凭据；新发布前确保 Task 已收束、21 注入 inactive，避免重启截断正在上传的采样。
- 每项镜像先保存旧 image ID/tag 和私有 rollback 配置，部署失败全部回滚；软链接只有全部健康后切换。新容器实际 PID/start_ticks 已变化，必须等待新的 Agent 快照并重新绑定，不复用旧验收 PID。
- `/proc` 与 cgroup 是真实观测来源。容器 cgroup namespace 下使用目标 `/proc/<pid>/root/sys/fs/cgroup`，必须确认为目标配额，不能读取 Agent 自己 cgroup 配额冒充目标。Analyzer 对 target ticks 与该 sys 样本 ticks 精确一致的要求应保持；collector 不应独立二次读 target/stat 导致两个字段在活跃时漂移。

## 独立回归

`tests/test_seven_gap_acceptance.py` 覆盖冻结七项 SHA/拒绝升级、百分比单位、跨域 Profile 不能证明锁或竞争、I/O 缺失/重置/NaN/非法计数，以及共享 cgroup 窗口的来源、身份、配额、计数、样本、时间和有效零差值。全部输入合成只用于验证器测试，不计为真机案例。初次运行 40 通过/1 失败属于测试文件内 C++ I/O SHA 抄写错误；对照原冻结文件校正后 41 通过，失败报告原样保留。
