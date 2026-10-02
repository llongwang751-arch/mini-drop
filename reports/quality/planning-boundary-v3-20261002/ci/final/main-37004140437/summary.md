# 精确 CI 主报告核对

提交 `cfea6744fc2395d5392440b2404ee275ada0a69f`，官方 [CI 37004140437](https://github.com/llongwang751-arch/mini-drop/actions/runs/37004140437) **14/14 作业成功**。

| 主报告 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| python | 2163 | 16 | 0 |
| postgres_python | 14 | 0 | 0 |
| native_ctest | 5 | 0 | 0 |
| web_vitest | 330 | 0 | 0 |
| chromium | 8 | 0 | 0 |

源码及 CI merge `7abe089ae9bfc9705b56c73a5b1dd19dd3bad7ea` 的 Git tree 都是 `df22dc2228f4f826405c1f5842b751c9d8af84dc`，本地只读 Git 核对一致。

Web 数量来源为官方 Vitest 作业终态日志；Chromium 使用合成 API，旧成绩请求为 0。

六份 ZIP 下载 SHA 与官方 digest/size 一致，完整 ZIP 仅保留在本地 stage；公开证据选择主报告原字节、十四份官方日志及 Git 来源证明，排除辅助 XML、重复二进制、ZIP 字节与截图。跳过及 Python parser 子集不重复计分。

三个真实 demo 镜像的观测与清理通过；perf 源码工具链仅为 TOOLCHAIN_SMOKE，不能称现场采样或因果根因。没有运行部署、云端故障或一小时压测。
