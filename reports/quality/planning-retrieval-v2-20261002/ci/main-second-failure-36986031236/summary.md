# 精确 CI 主报告核对

提交 `28f898ce22e24e9b071af762495fad1a5c65b205`，官方 [CI 36986031236](https://github.com/llongwang751-arch/mini-drop/actions/runs/36986031236) **13/14 成功、1 作业失败**。

| 主报告 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| python | 1970 | 16 | 0 |
| postgres_python | 14 | 0 | 0 |
| native_ctest | 5 | 0 | 0 |
| web_vitest | 329 | 0 | 1 |
| chromium | 8 | 0 | 0 |

源码及 CI merge `11143452ae4062491ba619890ddc48e579a3f364` 的 Git tree 都是 `3929c768321e835aca2af89710c17582393f01aa`，本地只读 Git 核对一致。

Web 数量来源为官方 Vitest 作业终态日志；Chromium 使用合成 API，旧成绩请求为 0。

六份 ZIP 下载 SHA 与官方 digest/size 一致，仅保留主报告原字节及十四份官方日志；辅助 XML、重复二进制、ZIP 与截图不落盘。跳过及 Python parser 子集不重复计分。

三个真实 demo 镜像的观测与清理通过；perf 源码工具链仅为 TOOLCHAIN_SMOKE，不能称现场采样或因果根因。没有运行部署、云端故障或一小时压测。
