# Analyzer 验证记录

Current freeze (2026-10-18): `release-v23-freeze.json` binds the26 installed
runtime files, Python-only overlay recipe, retained dependency image, actual
tool inventory and all new report SHA256 values. Image:
`sha256:55ae36b4e68dec8700ea722d30fc94e8eda665a756c038587f59279597e228ed`;
tags `drop-analyzer:v23-20261018-config` and `drop-analyzer:20261018.1-rc2`.
Runtime tree: `086561892d7fb844617c055e990ab0a1c2a5f5cace747a0a93f15e41239f583e`.

| Current report | Actual result | Scope |
| --- | --- | --- |
| `integration-v23-linux.json/.log` | 101/101, zero skips,59.168 seconds | Real isolated PG17.11/S3, all92 earlier regressions plus9 native configuration/logging tests |
| `release-source-fingerprint-v23.json` | All26 installed files match reviewed source | Actual candidate image; unchanged source before/after Windows preflight |
| `perf-runner-v23.json/.log` | Original Core script56920B/91 and native16032B/93 samples | Imported toy; PID/TID/comm/ns, native loss and four output hashes; no uploaded ELF execution |
| `perf-container-v23-hostconfig.json` / `perf-container-v23-state.json` | Entire512MiB container/no extra swap/network none; exit0/OOM=false | Short-lived verifier, not worker readiness or a repeat performance benchmark |
| `preflight-v23-config.json` / `integration-v23-local-final.log` | Windows101 discovered,76 passed,25 explicit skips,24.299 seconds | Local evidence, separate from the complete Linux release gate |
| `config-v23-before-snapshot-fix.log` | Startup snapshot regression failed before correction | Preserved red evidence for missing `S3_*` visible keys |

The actual0.2.2 whole-container512MiB performance baseline remains the historical
measurement below; it was not repeated for this configuration-only release.
Stage/production rollout and Ops migration remain independent gates.

## Historical 0.2.2 release

Prior freeze (2026-10-18): `release-v22-freeze.json` binds the 25 installed
runtime files, build inputs, actual tool inventory and report SHA256 values.
Final image: `sha256:326427a3996a4b2b383e4ee25d9218e6cb728bb72a49066e5836d0672ff01e31`.
Runtime tree: `36f726fdf62ee5262160ed8ea95fe8b959f61ba0e5274e02ddc44e60b92db8d9`.
Earlier pending paragraphs below are historical checkpoints, superseded by this
freeze. Production services and data were preserved.

| Prior report | Actual result | Scope |
| --- | --- | --- |
| `integration-v22-perf612-linux.json/.log` | 92/92, zero skips, 58.459 seconds | New dependency image; real PG17.11/S3; recovery, fencing and resource/security boundaries |
| `perf-runner-v22.json/.log` | Independent Core script 56920B/91 samples and native 16032B/93 samples | Actual PID/TID/comm/ns, native loss scan and four output size/SHA values; imported toy, no uploaded ELF execution |
| `benchmark-v22-512m.json` / `benchmark-v22-container.json` | 10k/50k/100k at 0.332/0.732/1.318 seconds; 100001 rejected | Entire container 512MiB, swap0, peak159076352B, OOM0; one synthetic star-tree run |
| `release-source-fingerprint-v22.json` / `tool-inventory-v22.json` | 25 installed files match; perf6.12.95/BOLT16/JFR/memray1.20/OTEL/pip check available | Actual amd64 execution; no ARM64 execution claim |

The real capture cross-test first exposed Debian perf6.1.190 rejecting the Core
6.8 capture ABI (eight extra bytes), with and without the 512MiB address-space
limit. `perf-runner-before-perf612.log` retains that failure. The image now uses
APT-authenticated Bookworm Backports `linux-perf=6.12.95-1~bpo12+1`; the complete
92 tests and the same actual RAW then passed. The older image14dd test report
is retained separately and does not replace the new dependency image proof.

所有 fixture 均为隔离 toy 或合成导入数据，不代表业务生产采样。报告只记录实际执行，不以代码存在代替运行证据。

2026-10-18 恢复本机预检：`preflight-v22-2026-10-18.json` 记录25文件冻结指纹未变、91项测试发现及20份fixture的size/SHA逐项一致。集成壳运行器4项Fake-Docker进程回归通过，覆盖同名容器/网络拒绝、仅清理本轮label资源和失败退出码保留；未启动Docker daemon，不能算作91项Linux验收。远端阶段仍等待Core串行队列交接。

| 报告 | 实际验证 | 限定范围 |
| --- | --- | --- |
| `integration-full-final.json` / `.log` | 0.2.0，63/63，零 skip，83.658 秒 | 非 root Linux、真实 PostgreSQL/S3、17 种任务、HPROF/worker SIGKILL 恢复、lease/attempt fencing |
| `release-source-fingerprint.json` | 0.2.0 镜像内 22 个 Python 文件与测试源码逐项 SHA 相同 | 不包含其后 0.2.1 或 100k 改动 |
| `integration-v21-final.json` / `.log` | 0.2.1，70/70，零 skip，115.438 秒 | 新增 HPROF 五阶段事件和数据库 fencing、资源字段、原生格式截断拒绝；仍为 50k 上限快照 |
| `integration-v21-before-startup-bound.log` | 首次 70 项中 69 通过、1 失败 | 保留 5 秒启动等待过短的失败记录；修改为有界 15 秒后完整重跑，未跳过测试 |
| `native-format-results.json` | 17 种任务和额外 JFR，共 18 份真实格式 | IO/memory 来自 toy 用户态操作测时，不是内核 BPF 验证 |
| `runner-format-results.json` | 实际 Core Runner 生成的 13 份 RAW，逐项 size/SHA 和 Parser 解析通过 | 导入验证保留 `source=import`，不是公网生产任务 |
| `bolt-toy-equivalence.json` | toy 真实插桩、优化；3 组原/优化 checksum 与退出码一致 | 只执行脚本自身编译的 toy；短计时不推导业务优化收益 |
| `arm64-bolt-pins.json` | 两个 ARM64 LLVM19 包与官方 HTTPS 索引 version/size/SHA 匹配 | 供应链元数据检查；不等同于 ARM 镜像执行 |

100k 改动：节点限制包含虚拟 root；100,000 接受、100,001 拒绝。本机 10 项 folded 回归、全目录 Ruff E9/F 与格式检查、compileall 已通过。新增真实 Linux 512 MiB 子进程边界和 10k/50k/100k 完整产物编码基线等待远端串行执行；先前并发构建造成 I/O 压力，主动终止新任务，没有把未完成运行记录为成功。该轮23文件指纹 `3323ddeb6d5e34db914abf7c73aa414290b5263f9c5b9cb02a679c48814dd49f` 已被0.2.2磁盘及perf扩展取代，不作为当前最终镜像依据。

0.2.2当前证据：`integration-v22-offline.json` / `.log`，实际Windows精选53项、48通过、5个Linux/POSIX条件skip、0失败，5.014秒；源码25文件指纹 `a05d3d81f9797acc08f64adebbd78bed02d32084db2429ebc4dd34e2790e2983`。当前全量发现91项，待Linux真实PG/S3、POSIX安全路径、perf工具和实际Runner新RAW、100k容器基线后，再生成新Linux报告并构建校验镜像。旧63/70通过和未完成72项不能混作新版本验收。

2026-10-18 精确对象字节修复后：实际urllib3 gzip回归先复现透明HTTP解压引发长度不符，下载显式`decode_content=False`后通过。当前0.2.2的25文件指纹改为 `36f726fdf62ee5262160ed8ea95fe8b959f61ba0e5274e02ddc44e60b92db8d9`；`integration-v22-encoded-offline.json/.log` 记录54项、49通过/5个POSIX skip/0失败、4.579358秒。新Linux发现92项，仍待实际执行；旧91发现及旧SHA预检保留为历史，不用于新镜像冻结。
