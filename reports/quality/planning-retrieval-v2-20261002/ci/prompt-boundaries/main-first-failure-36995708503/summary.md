# 精确 CI 主报告核对

提交 `f2cdbe7d711a58e2042bf4ef2cc01a0fcb051cf0`，官方 [CI 36995708503](https://github.com/llongwang751-arch/mini-drop/actions/runs/36995708503) **13/14 成功、1 作业失败**。

| 主报告 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| python | 2018 | 16 | 1 |
| postgres_python | 14 | 0 | 0 |
| native_ctest | 5 | 0 | 0 |
| web_vitest | 330 | 0 | 0 |
| chromium | 8 | 0 | 0 |

源码及 CI merge `4b09a6ec0f593cd54027f0980993dda3969d5cfc` 的 Git tree 都是 `ef85f10a50eb9998b5283a9ee7926b5cd41c5201`，本地只读 Git 核对一致。

Web 数量来源为官方 Vitest 作业终态日志；Chromium 使用合成 API，旧成绩请求为 0。

六份 ZIP 下载 SHA 与官方 digest/size 一致，仅保留主报告原字节及十四份官方日志；辅助 XML、重复二进制、ZIP 与截图不落盘。跳过及 Python parser 子集不重复计分。

三个真实 demo 镜像的观测与清理通过；perf 源码工具链仅为 TOOLCHAIN_SMOKE，不能称现场采样或因果根因。没有运行部署、云端故障或一小时压测。
