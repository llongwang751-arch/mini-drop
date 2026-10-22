# JD补强：受控Agent质量与注入回归

此目录补充可重复的效果记录，沿用 `scripts/evaluation/evaluate_diagnostics.py` 的摘要、来源类型、引用与比例算法，并调用真实Analyzer重放固定输入核对分析摘要。它不替代既有24次评估，不把作者可见的回归集改称盲测。

`dataset/model_inputs.json` 只有8个独立的导入文本和问题：计算热点、等量正常对照、少样本、符号注入、直接指令注入及非法权重。`oracle.json` 保存作者预设标签；`manifest.json` 冻结两者规范化SHA。runner只把合成文本及问题发送普通API，绝不把oracle或预期答案发给模型。修改已发布集时应新增版本，不能改清单后沿用历史分数。

`controlled_fixture_hint_top3` 的分母是预注册的全部可诊断试验，含失败、超时、未执行；仅按 `hypotheses.kind` 的前3项精确匹配受控情境标签，未知枚举为unknown。正常对照允许 `normal_load` 或 `insufficient_evidence`，因为等量采样本身不能证明生产正常。非法输入另按真实参数/解析错误计分，服务故障或模型失败不会算有效拒绝。此指标不是根因准确率、模型解释正确率或生产误报率。

真实执行默认关闭。以下命令在仓库根运行；输出选全新的私有 `.local` 文件，不在命令行放凭据。凭据JSON只含 `username` 与 `password`。目标仅允许已授权 `https://82.157.81.79` 且严格验证TLS；独立本地夹具需额外 `--allow-loopback`。重定向、任意地址、采集确认和修复接口均禁止。

```powershell
python scripts/quality/agent/run.py --output .local/jd-quality/plan.json
python scripts/quality/agent/run.py --execute --credentials .local/jd-quality/credentials.json --output .local/jd-quality/capture.json
python scripts/quality/agent/quality.py score --input .local/jd-quality/capture.json --output .local/jd-quality/score.json
python scripts/quality/agent/quality.py human-template --input .local/jd-quality/capture.json --output .local/jd-quality/human-review.json
```

可使用 `--case jd01 --case jd07` 预注册子集，`--repetitions` 为1至3。实际执行只导入合成数据并请求已有只读Agent：每请求15秒、分析90秒、单诊断180秒、总1800秒、HTTP最多1500次。不自动重试模型/写请求，不丢弃失败或挑选成功结果。诊断超时走正常取消，分析超时走普通任务取消；登录后最终注销。保留导入任务用于证据复核，不擅自删除测试记录。生产需要与其他验收串行执行。

capture含实际DiagnosisRun工具观测，仅放受控私有目录。公开score只有结构枚举、身份、计数与SHA；不复制模型自由解释、凭据、原始采集文件或下载URL。JSON自身不提供真实性签名，发布时仍需绑定实际HTTP执行来源与服务版本。

`objective_report_guard`只复核报告版本、运行元数据、工具白名单和假设的证据编号引用，明确输出 `fact_values_revalidated=false`。事实标量的原始核验由平台Go报告防线完成；此Python评分器没有复制第二套单位/数值数学校验，也不能把guard通过解释为再次独立核对了每个事实值。

独立人工字段保持null。真人审阅后可用 `quality.py human-score --input ... --reviews ... --output ...` 导入：要求人工类型、明确独立性、报告SHA和情境匹配，不能导入Agent自评。根因TopK标签还必须给出 `causal_ground_truth_reference`；仅凭此合成采样集不应填写根因正确。工具不会验证审阅者的现实身份或自动声称盲评。

```powershell
python -m unittest discover -s scripts/quality/agent -p 'test_*.py' -v
```

单测中的API返回和模型报告都是明确合成控制，仅证明评分器、来源与执行边界，不证明真实模型效果。实际模型结果须另附运行收据。
