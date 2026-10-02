# 官方 CI 证据复核

源码 `63a8284dcc6eacd925cb04064305c36df3455fad`，CI [36904602187](https://github.com/llongwang751-arch/mini-drop/actions/runs/36904602187) **14/14 作业成功**。

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
| python | 793 | 77955072 | 98 | 0.115203 | 通过 |
| java | 717 | 93978624 | 99 | 0.138200 | 通过 |
| cpp | 757 | 99221504 | 131 | 0.104656 | 通过 |

C++ 锁窗新增 3592 次获取、3592 次竞争、10651.55ms 获取等待，平均 2.965354ms。
Python 共享配额 `65000 100000`；本窗实际新增 37 次节流、5477571μs 节流；peer 增长 113 ticks。

三镜像均在受限只读容器和 64MiB tmpfs 内完成真实同步 I/O，累计写入超过 64MiB、失败 0、原始首尾重新计算一致。这验证持续观测与撤销清理，不代表真实块设备瓶颈、AI 因果根因或一小时压测。

| 官方产物 | 下载 SHA256 |
|---|---|
| postgres-concurrency-bcc12e60941ac0bff3820411a2eaaa7cd823ec9b-1 | `8eb24e3f34c428353910e2d3c67a9a95ab8c8aa664675b3024dd30c60e999891` |
| observation-controls-bcc12e60941ac0bff3820411a2eaaa7cd823ec9b-1 | `643cb69d2dff23c7d3a20002855bffaaca6ce3751b258413ba99d42bb5b03547` |
| python-quality-bcc12e60941ac0bff3820411a2eaaa7cd823ec9b-1 | `044b9d7c9ad22a27f452e6ffd2305257b37cf2ad2852a27ee95542213af1fb1e` |
| web-browser-bcc12e60941ac0bff3820411a2eaaa7cd823ec9b-1 | `0e2d5f6aed34ebf7fbf6095cb9763053eb603f6275c9e8f3b5223edca1785d30` |
| hotspot-controls-bcc12e60941ac0bff3820411a2eaaa7cd823ec9b-1 | `9cc273d9220895b4d1076e265ddd5bd0f96d8213b4d439dbb6f7015fa6c85a8a` |
| native-agent-binary-bcc12e60941ac0bff3820411a2eaaa7cd823ec9b-1 | `6975307fb5f2a0aa123b04ac6e678aa1c150f3184098193f7571b3b37811eb3d` |

实际 Analyzer 依赖层在只读、无网络、非root、128MiB/0.5CPU 容器中通过真实 ELF 工具链校验：
`0000000000467530 T cpp_cpu_hot_function` → `/src/main.cpp:34`；perf version 6.12.111，Perl v5.40.1。
ELF SHA：`f652bbaa3292b44e911aae18c96b222714bd5540376c1cfbaf1850e8901235ba`。真实安装工具包含 perf、Perl、nm、addr2line、objdump；debug_info/debug_line 保留。
Perl --srcline 折叠文本来自真实 ELF 的地址/符号/源码映射，但 PID、时间和 1 个样本是明确构造的格式输入；此项只算 TOOLCHAIN_SMOKE，不算现场 perf 或根因。
26 项工具链 parser 正负测试实际通过，已包含在 Python 总数中。

发布源码 tree `05c8328181c3682f286b7921e0083d41742367fe` 与 CI merge `bcc12e60941ac0bff3820411a2eaaa7cd823ec9b` tree相同；官方 Git commit/parents 元数据另存。

原始 ZIP、解包文件、八个官方 Job 日志及逐文件 SHA 已保存在 `ci-artifacts-phase2/`。第一版成功证据与中间失败证据保留；最新同 Git tree 证明另存 `ci-phase2-source-equivalence.json`。
