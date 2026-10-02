# 精确 CI 主报告核对

提交 `a6a3626094b8edbb5b9a48bef317153035ccca16`，官方 [CI 36992014614](https://github.com/llongwang751-arch/mini-drop/actions/runs/36992014614) **14/14 作业成功**。

| 主报告 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| python | 2013 | 16 | 0 |
| postgres_python | 14 | 0 | 0 |
| native_ctest | 5 | 0 | 0 |
| web_vitest | 330 | 0 | 0 |
| chromium | 8 | 0 | 0 |

源码及 CI merge `d2b5cacfa5ac3bd156736d41e248c7bbc77ebecf` 的 Git tree 都是 `2ceceb162da844f0f3627e87cc50dd1fb641ecd3`，本地只读 Git 核对一致。

Web 数量来源为官方 Vitest 作业终态日志；Chromium 使用合成 API，旧成绩请求为 0。

六份 ZIP 下载 SHA 与官方 digest/size 一致，仅保留主报告原字节及十四份官方日志；辅助 XML、重复二进制、ZIP 与截图不落盘。跳过及 Python parser 子集不重复计分。

三个真实 demo 镜像的观测与清理通过；perf 源码工具链仅为 TOOLCHAIN_SMOKE，不能称现场采样或因果根因。没有运行部署、云端故障或一小时压测。
