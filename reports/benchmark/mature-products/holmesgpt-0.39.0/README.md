# HolmesGPT 0.39.0 对照运行

本目录保存一次真实的 HolmesGPT CLI 对照尝试，而不是手工填写的模拟结果。

- 输入：`input.md`，与 Mini-Drop 的 `RW-OTELPY-4224` 稳定回放使用同一组冻结观测；
- 安装：云端 Ubuntu 24.04.4，HolmesGPT 0.39.0；
- 结果：CLI 已启动，但 OpenAI-compatible 提供商预检返回 HTTP 401；
- 评测处理：记为 `EXECUTED_PROVIDER_AUTH_BLOCKED`，不生成 HolmesGPT 诊断质量分数，也不把外部认证失败算成产品能力失败。

重新执行时，只需在进程环境配置有效的 `OPENAI_API_KEY`、`OPENAI_API_BASE`、`HOLMES_MODEL`，然后调用：

```bash
scripts/run_holmesgpt_comparator.sh
```

脚本不会从命令行读取或打印密钥。
