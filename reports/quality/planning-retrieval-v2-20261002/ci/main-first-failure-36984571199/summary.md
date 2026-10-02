# 精确 CI 主报告核对

提交 `dd3a359f07947a4f0b1ef00b06d2119a08103ad2`，官方 [CI 36984571199](https://github.com/llongwang751-arch/mini-drop/actions/runs/36984571199) **13/14 成功、1 作业失败**。

| 主报告 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| python | 1965 | 16 | 1 |
| postgres_python | 14 | 0 | 0 |
| native_ctest | 5 | 0 | 0 |
| web_vitest | 330 | 0 | 0 |
| chromium | 8 | 0 | 0 |

源码及 CI merge `56788a921762abb5a409f608089d6d04ff1e4031` 的 Git tree 都是 `a879e20f9f037c58dd79ee0a89b7b9f0015cfea7`，本地只读 Git 核对一致。

Web 数量来源为官方 Vitest 作业终态日志；Chromium 使用合成 API，旧成绩请求为 0。

六份 ZIP 下载 SHA 与官方 digest/size 一致，仅保留主报告原字节及十四份官方日志；辅助 XML、重复二进制、ZIP 与截图不落盘。跳过及 Python parser 子集不重复计分。

三个真实 demo 镜像的观测与清理通过；perf 源码工具链仅为 TOOLCHAIN_SMOKE，不能称现场采样或因果根因。没有运行部署、云端故障或一小时压测。
