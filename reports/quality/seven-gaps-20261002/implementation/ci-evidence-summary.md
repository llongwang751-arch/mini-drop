# 官方 CI 证据复核

源码 `b7a545abd9d5a64e8899f8508941d46ce47e4605`，CI [36897365758](https://github.com/llongwang751-arch/mini-drop/actions/runs/36897365758) **14/14 作业成功**。

| 专项 | 实际通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| python-quality | 1589 | 16 | 0 |
| postgres-concurrency | 14 | 0 | 0 |
| native-agent-binary | 5 | 0 | 0 |
| web-react | 309 | 0 | 0 |
| web-chromium | 7 | 0 | 0 |

Python 临时测试目录中的负向/历史 XML 均保留，但没有加入当前执行总数。真实 Chromium 使用合成 API 数据，不能算云端诊断。

| 真实镜像 | 同步写累计次数 | 写入字节 | 本窗次数 | 平均操作耗时 ms | 清理 |
|---|---:|---:|---:|---:|---|
| python | 794 | 78053376 | 99 | 0.108301 | 通过 |
| java | 717 | 93978624 | 98 | 0.129728 | 通过 |
| cpp | 757 | 99221504 | 131 | 0.103359 | 通过 |

C++ 锁窗新增 3592 次获取、3591 次竞争、10637.91ms 获取等待，平均 2.961556ms。
Python 共享配额 `65000 100000`；本窗实际新增 38 次节流、5411365μs 节流；peer 增长 119 ticks。

三镜像均在受限只读容器和 64MiB tmpfs 内完成真实同步 I/O，累计写入超过 64MiB、失败 0、原始首尾重新计算一致。这验证持续观测与撤销清理，不代表真实块设备瓶颈、AI 因果根因或一小时压测。

| 官方产物 | 下载 SHA256 |
|---|---|
| hotspot-controls-2e7db027048535fb70b4d2db7e0986b17225cb2c-1 | `3bad4a0649c5c57c3db04a9d538f2fab4df0b0cad78b1939176e8eae5c59e406` |
| observation-controls-2e7db027048535fb70b4d2db7e0986b17225cb2c-1 | `642447525f052772b3cbe19d9af780f5cadc9be48a316688372ea3010a74080f` |
| python-quality-2e7db027048535fb70b4d2db7e0986b17225cb2c-1 | `3b7ec0e99ed22c6c03ccf523b7e0caba2be22fc9203236911fbba4db94f16923` |
| web-browser-2e7db027048535fb70b4d2db7e0986b17225cb2c-1 | `12b88558e3a03954ac1cba3c106252b7e53af983c70927de9d77000ff535042e` |
| native-agent-binary-2e7db027048535fb70b4d2db7e0986b17225cb2c-1 | `27faa5b4f00bfdb33aaceb16a146cd9798e9ffe63ac1ccaf48952dc1c41a011f` |
| postgres-concurrency-2e7db027048535fb70b4d2db7e0986b17225cb2c-1 | `ad6e731e214f2b2fdd726aae8c63f87ae905240fd1a98a683b5595584e9f2150` |

原始 ZIP、解包文件、八个官方 Job 日志及逐文件 SHA 已保存在 `ci-artifacts/`。现有 Native artifact/proof 保持；同 Git tree 的 CI merge 与发布源码证明另存 `ci-source-equivalence.json`。
