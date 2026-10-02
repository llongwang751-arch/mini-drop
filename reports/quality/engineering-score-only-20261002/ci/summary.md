# 精确 CI 主报告核对

提交 `de094fff754f2d8cb7139dd99e31c7a30d5fb754`，官方 [CI 36966142203](https://github.com/llongwang751-arch/mini-drop/actions/runs/36966142203) **14/14 作业成功**。

| 主报告 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| python | 1682 | 16 | 0 |
| postgres_python | 14 | 0 | 0 |
| native_ctest | 5 | 0 | 0 |
| web_vitest | 317 | 0 | 0 |
| chromium | 8 | 0 | 0 |

源码及 CI merge `4106ef148573cc39036eb099b4ac1f7757f9f645` 的 Git tree 都是 `54ab6eddbba894b268bb5aae686118f5127d7f29`，本地只读 Git 核对一致。

Web 数量来源为官方 Vitest 作业终态日志；Chromium 使用合成 API，旧成绩请求为 0。

六份 ZIP 下载 SHA 与官方 digest/size 一致，仅保留主报告原字节及十四份官方日志；辅助 XML、重复二进制、ZIP 与截图不落盘。跳过及 Python parser 子集不重复计分。

三个真实 demo 镜像的观测与清理通过；perf 源码工具链仅为 TOOLCHAIN_SMOKE，不能称现场采样或因果根因。没有运行部署、云端故障或一小时压测。
