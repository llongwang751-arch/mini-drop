# 受控因果评测 v2-r2（API 20261023.2）

2026-10-22 19:52:48.469273Z 至 20:06:01.038423Z，按与 v2-r1 相同的六轮预登记计划执行一次；12 次真实采集与 DeepSeek/AgentScope 诊断均终态成功，逻辑模型调用 43/60、工具调用 50/80，未重试挑优，未放宽 oracle 或预算。CPU、慢 SQLite 查询、网关 deadline 组合干预及撤销恢复的独立客观验证为 6/6；故障方向 Top3 为 2/6，恢复方向 Top3 为 6/6。每轮须三条件同时成立，综合仍是 **failed：2 通过、4 失败、0 跳过**。

**正常 Top3 的 6/6 不能解释为误报率 0。** 六份正常报告都包含 `cpu_hotspot`，同时含 `insufficient_evidence`，其中一份另含 `normal_load`。该指标只是是否包含预登记粗粒度类别；独立人审尚未提交，精确根因、正常误报和语义拒答指标均为 null。慢 SQL 与 timeout 四轮均未命中故障方向，且所有故障轮都没有主动查询业务信号。SQLite 合成故障本身使用 CPU 密集聚合，方向未命中也不能代替真人对完整解释的独立语义判断。

修复后的业务工具只有一次实际 metrics 调用（正常 run125），返回 `window_scope=collector_execution`、`context_queried=false`；20.435 秒采集窗与仅作元数据的上下文窗已分开。零模型接线另使用实际 task135 的 runner 时钟，独立 HTTP 比较精确采集窗与旧扩展窗：错误率 1.0 与 0.009057，不把直接 adapter 查询冒充生产 Go 私有 tool 调用。对应 Go→HTTP 转发与混杂负控另有 43 项 fresh-PG 回归，无需泄露 oracle 给模型。

`full-two-repetitions/score.json`、计划与未填人审模板保留原字节。原始 capture 含模型正文与私有结果，继续留在服务器受保护 QA 目录；`capture-reference.json` 只公开其 SHA 和白名单投影位置。`source-pins.json` 与 `receipts/environment-binding.public.json` 绑定本轮 API 镜像、61 文件控制器与 6 镜像/3 来源摘要，不冒充远端可信执行或独立编译证明。旧 v1/v2-r1 记录、分母和失败均未修改。

后验确认 29 个正式容器 ID、镜像、挂载和网络与本轮初始状态相等且均运行；61 个冻结源文件与三个环境来源摘要不变。QA 驱动已移除、临时 0600 管理员凭据已删除、logout 确认、无待清理负载；后续发布已释放冻结。

人工评审入口是 `full-two-repetitions/human-review.json` 和 `protocol/human-review.schema.json`。必须由独立真人填写评审身份、独立性声明与报告 SHA 绑定后，才能通过 `scripts/quality/causal/v2/experiment.py human-score` 导入；当前模板保持未填，未声称盲评。
