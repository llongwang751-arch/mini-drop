# 官方 CI 证据复核

源码 `35f21b82997349b2f8e8010ae834e27a25fafc8f`，CI [36907241773](https://github.com/llongwang751-arch/mini-drop/actions/runs/36907241773) **14/14 作业成功**。

| 专项 | 实际通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| python-quality | 1675 | 16 | 0 |
| postgres-concurrency | 14 | 0 | 0 |
| native-agent-binary | 5 | 0 | 0 |
| web-react | 313 | 0 | 0 |
| web-chromium | 7 | 0 | 0 |

Python 临时测试目录中的负向/历史 XML 均保留，但没有加入当前执行总数。真实 Chromium 使用合成 API 数据，不能算云端诊断。

| 真实镜像 | 同步写累计次数 | 写入字节 | 本窗次数 | 平均操作耗时 ms | 清理 |
|---|---:|---:|---:|---:|---|
| python | 793 | 77955072 | 99 | 0.127378 | 通过 |
| java | 715 | 93716480 | 99 | 0.149346 | 通过 |
| cpp | 757 | 99221504 | 131 | 0.100076 | 通过 |

C++ 锁窗新增 3596 次获取、3592 次竞争、10650.43ms 获取等待，平均 2.961744ms。
Python 共享配额 `65000 100000`；本窗实际新增 37 次节流、5218486μs 节流；peer 增长 121 ticks。

三镜像均在受限只读容器和 64MiB tmpfs 内完成真实同步 I/O，累计写入超过 64MiB、失败 0、原始首尾重新计算一致。这验证持续观测与撤销清理，不代表真实块设备瓶颈、AI 因果根因或一小时压测。

| 官方产物 | 下载 SHA256 |
|---|---|
| hotspot-controls-06226c703a14dba5999bcc3115633a8be95b2c25-1 | `333d73e0b376a01028ef4e3e54dddbfd872c1fb6d19be5751fe76ff71bb353c3` |
| python-quality-06226c703a14dba5999bcc3115633a8be95b2c25-1 | `555a5f0c2ae9977083fab4ff54e55ac0b3b5e93d3ec72d53e35cf3f6b87a6239` |
| postgres-concurrency-06226c703a14dba5999bcc3115633a8be95b2c25-1 | `7deecb5da8d286430dd90c5279a9eb6b26faec2cafba1b2b6a0f5eefc71f8c3f` |
| web-browser-06226c703a14dba5999bcc3115633a8be95b2c25-1 | `7dfbf312aeb67a277f8e67537169b9b9d7f70d4371d40772bebd5b66581eac4b` |
| native-agent-binary-06226c703a14dba5999bcc3115633a8be95b2c25-1 | `a30600dd5ddc5bbdf5ab8e3971b4a29de3ce2b426e84a2d09946d478ff87d902` |
| observation-controls-06226c703a14dba5999bcc3115633a8be95b2c25-1 | `f2bc27b12156b34a4d2fae2466a9d60bf75cf55718d28efb35d3100690c91f98` |

实际 Analyzer 依赖层在只读、无网络、非root、128MiB/0.5CPU 容器中通过真实 ELF 工具链校验：
`0000000000467530 T cpp_cpu_hot_function` → `/src/main.cpp:34`；perf version 6.12.111，Perl v5.40.1。
ELF SHA：`f652bbaa3292b44e911aae18c96b222714bd5540376c1cfbaf1850e8901235ba`。真实安装工具包含 perf、Perl、nm、addr2line、objdump；debug_info/debug_line 保留。
Perl --srcline 折叠文本来自真实 ELF 的地址/符号/源码映射，但 PID、时间和 1 个样本是明确构造的格式输入；此项只算 TOOLCHAIN_SMOKE，不算现场 perf 或根因。
26 项工具链 parser 正负测试实际通过，已包含在 Python 总数中。

发布源码 tree `cfe95e1c841537f561c9822899d2f2f300b0fe11` 与 CI merge `06226c703a14dba5999bcc3115633a8be95b2c25` tree相同；官方 Git commit/parents 元数据另存。

六个 ZIP 的 SHA256 和大小均与 GitHub 官方 API digest/size 一致；1167 个解包文件及十四份 job 日志再次核对 SHA256。

原始 ZIP、解包文件、十四个官方 Job 日志及逐文件 SHA 已保存在 `ci-artifacts-final/`。前两轮成功证据与中间失败证据保留；最终同 Git tree 证明另存 `ci-final-source-equivalence.json`。
