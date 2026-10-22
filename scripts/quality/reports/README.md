# 不可变质量运行记录与展示

共享契约为 [quality-run.schema.json](../../../contracts/quality-run.schema.json)。管理员在 `/quality` 查看已发布的审阅摘要、失败明细、同套件时间线、未知指标和JSON导出。只有平台管理员可读；撤权后由API当前会话重新判断，页面导航不代替后端授权。

记录是已有实验收据的白名单投影，不是让模型自由写一段“通过”说明。发布前人工核对运行来源、计数和源码/收据SHA，明确合成输入与真实执行的区别。每项指标注明自己的单位和分母；未知人审指标必须是 `null`，不填0，也不根据结构标签推算根因准确率。

```bash
python scripts/quality/reports/publish.py \
  --record /private/reviewed-summary.json \
  --expected-sha256 <原文件SHA256> \
  --directory /private/drop-quality-records
```

发布器对大小、Schema、重复键、计数、状态、时间、指标和明显的秘密/URL字段做拒绝检查。相同ID不同字节不能覆盖；新文件以同文件系统硬链接原子发布并同步目录。该目录只由单个受控发布作业写入，API通过只读挂载读取，不提供HTTP执行或写入入口。文字过滤不是通用脱敏证明，禁止将原始日志、模型自由报告、profile或业务数据直接作为输入。

API配置 `QUALITY_REPORTS_DIR=/app/quality-records`，把上述目录只读挂载至同路径。未配置时返回空列表；配置后目录缺失、损坏、超量或存在特殊文件时失败关闭。最多128份记录，每份32KiB；达到上限时先将完整历史连同索引归档，由维护者明确选择新的展示目录，不自动删除旧失败。

`record_sha256`对应原JSON文件的确切字节。浏览器导出的响应还含`run`/`record_sha256`包装，因此不是直接对整个下载文件求同一个SHA。摘要不等于数字签名，也不证明现实中的独立审阅者身份。

```bash
python -m unittest discover -s scripts/quality/reports -v
cd api && go test ./internal/quality
```

单测只使用明确导入的合成收据。真实实验、独立人工语义审阅、实际部署和GitHub执行必须各有自己的运行证据。
