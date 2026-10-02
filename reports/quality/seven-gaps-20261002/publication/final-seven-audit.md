# 最终 7 类工程观测独立审计

状态：VERIFIED。331/331 项核对通过。

最终选中 7 类：2 类异常定位、2 类观测确认、3 类反证；共进行了 9 次真实试验，原始 2 次失败完整保留。
这不等于 7 类因果根因定位。所有报告中的因果根因和同负载修复验证均未标为 true。

| 案例 | 结局 | 原始数据独立重算 | 恢复 / 清理 / 会话结束 |
| --- | --- | --- | --- |
| source-hotspot | LOCALIZED_ANOMALY | Linux 单核 CPU 89.202883%；4452 样本 | 已核对 |
| cpp-cpu-hotspot | LOCALIZED_ANOMALY | Linux 单核 CPU 64.592106%；951 样本 | 已核对 |
| cpp-lock-contention | SUPPORTED_OBSERVATION | 16785 次获取；均值 2.976330 ms | 已核对 |
| io-write-latency | REFUTED | 696 次；均值 0.072800 ms < 10 ms | 已核对 |
| java-file-io | REFUTED | 693 次；均值 0.092458 ms < 10 ms | 已核对 |
| cpp-file-io | REFUTED | 924 次；均值 0.072760 ms < 10 ms | 已核对 |
| noisy-neighbor | SUPPORTED_OBSERVATION | target/peer 657/635 ticks；节流 123 窗 / 10088126 μs | 已核对 |

选中案例的 32 个下载产物以及全部 9 次试验的 43 个产物均重新计算 SHA-256，并与登记大小、任务和 manifest 核对。完整清单、案例文件哈希、原始计数首尾、报告标记路径见 final-seven-audit.json。

CPP 新试验的真实源码样本落在 main.cpp 第 36–39 行的 cpp_cpu_hot_function；共 951 个样本。邻居与目标同属一个 cgroup，quota/period 为 100000/100000 μs，真实内核 PID 和 start_ticks 在所有采样中稳定。

原始未通过记录保留：

| 原始案例 | 诊断 ID | 原始结局 |
| --- | --- | --- |
| cpp-cpu-hotspot | insight_bf7f769a66484989a4eade9e2bab874b | INSUFFICIENT_EVIDENCE |
| noisy-neighbor | insight_392551113a474e10a8216170f41a75c4 | INSUFFICIENT_EVIDENCE |

边界与保留事项：

- Seven accepted engineering observations are not seven causal root-cause localizations: two LOCALIZED_ANOMALY, two SUPPORTED_OBSERVATION, three REFUTED.
- The three I/O means measure successful target-application open/write/sync/close elapsed time on the deployed filesystem. They do not establish physical block-device latency or a slow-I/O fault.
- Recovery withdraws the injected workload. It is not a same-load repair, SLO validation, or causal intervention result.
- Python and C++ CPU profiles and OS CPU controls were collected in separate windows; they are not simultaneous sample-by-sample correlations.
- Python source localization includes the _sample and _publish_application_metrics instrumentation paths as well as source_hot_function; it does not prove one business function caused an incident.
- CPP I/O retains an additional perf request denied by budget. Its sys_metrics raw observation is valid, while recorded_chain_consistent and engineering_recorded_chain_verified remain false.
- The noisy-neighbor report retains a conservative legacy label about same-host peer activity. Raw Linux cgroup counters verify shared-quota activity and throttling, but do not establish a business SLO impact or a same-load causal repair.
- CPP ELF/srcline provenance was verified in the separately hashed dependency-image-proof.json; this local audit verifies retained perf.data and analyzer artifacts without re-executing perf against the binary.
- Both original insufficient-evidence trials are retained unchanged; only fresh retry trials contribute the final CPP CPU and noisy-neighbor selections.
