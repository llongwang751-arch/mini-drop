# 受控因果验收协议 v2

本目录与父目录 v1 独立冻结，禁止覆盖旧数据、源码和分数。v1 正式六轮计划的第二轮正常恢复采样失败：实际 py-spy 退出 0，但输出为空；这不是模型语义错误，也不能补成正常零值。

v2 保持原三条控制路径、所有效果阈值、62 秒洗脱、60 模型/80 工具累计预算、独立人工空模板。前后统一改为 **20 秒、99 Hz**，真实采样权重额外要求至少 10。单线程闭环请求的最小间隔固定为 50ms（最多约 20 请求/秒）；每阶段最多 145 秒、2900 次请求，少于演示服务 4096 条遥测容量。整次实验 1800 秒、demo HTTP 总数 36000 封顶；客户端仍限制5秒/262144字节，所有端点固定白名单。

收到网关真实 504 后，控制器通过目录日志确认**同一 Trace**实际完成，再发下一条请求；这避免把未取消的 SQL 堆叠为额外并发干预。确认最多3秒，每100ms检查，不执行SQL或猜测后台已取消。timeout仍是十二次聚合与50ms网关deadline的组合干预，不能包装成单因素根因准确率。

已确认的采集终态失败保留错误码，取消得到确认也不重跑该轮；继续剩余预登记轮次。创建结果未知、取消未确认、负载线程未停止或目录完成不确定则停止，不盲重试。已记录失败导致进程非零退出，缺失/失败仍留六轮分母。模型问题中性，故障控制、阈值和oracle不传给模型。

```sh
python3 -m unittest discover -s scripts/quality/causal/v2 -p 'test_*.py' -v
python3 scripts/quality/causal/v2/run.py --repetitions 2 --output /private/v2-plan-new
python3 scripts/quality/causal/v2/run.py --execute --repetitions 2 \
  --config /private/config.json --environment /private/environment.json \
  --credentials /private/credentials.json --output /private/v2-run-new
```

凭据、环境配置及源码布局沿用父目录说明；每次只能写全新输出目录。`experiment.py score/human-template/human-score` 与父目录命令相同，但必须使用本目录版本。失败与未执行项不从分母移除，不重试挑优。自动方向指标继续只匹配现有 `hypotheses.kind`，精确根因、误报、拒答仍待独立真人；模板不能替代人审。
