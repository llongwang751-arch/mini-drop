# 资源自适应 Causal Replay 专属测试集

该目录用于评价“李明远独立亮点：资源自适应反事实根因验证”。它是团队统一基础测试集的扩展，不替代 `benchmarks/cases/`。

## 可执行 Golden 集

`golden_cases.jsonl` 目前提供 10 条确定性裁决样本，覆盖 CPU 热点、内存持续增长、下游延迟，包含单机交叉、双机对照、支持、推翻、无法确认和证据不足等边界。

在仓库根目录执行：

```bash
python -m benchmarks.causal_replay.run_golden
```

这只验证裁决规则和拒答边界，不等于真实采集器已经在云主机跑通。真实能力仍需从 Web 发起 Campaign，保存基线、故障、干预和恢复快照。

## 与基础测试集的区别

基础测试集主要评价系统是否定位到正确根因。Causal Replay 还要求系统回答：

1. 使用什么安全干预验证候选根因；
2. 实验组和对照组分别是谁；
3. 哪些观测支持假设，哪些观测会推翻假设；
4. 干预产生了多大效应；
5. 故障是否被清理，系统是否恢复；
6. 数据不足时能否拒绝确认因果关系。

## 运行规模

- 10 个案例；
- 每案预热 1 次；
- 每案正式重复 3 次；
- 正式运行共 30 次；
- 首期 MVP 先运行 `CR-CPU-001`、`CR-MEM-001`、`CR-DOWNSTREAM-001`，共 9 次；均使用受控测试程序自身的白名单开关，不依赖暂停其他业务进程。

## 输出要求

每次运行至少输出：

```json
{
  "case_id": "CR-CPU-001",
  "verdict": "CAUSALLY_VERIFIED",
  "root_cause": "...",
  "intervention": "...",
  "treatment_target": "...",
  "control_target": "...",
  "effect_size": {},
  "evidence_refs": [],
  "counter_evidence_refs": [],
  "cleanup_verified": true,
  "confidence": 0.0,
  "limitations": []
}
```

允许的 verdict：

- `CAUSALLY_VERIFIED`
- `SUPPORTED_SINGLE_NODE`
- `CAUSALLY_REFUTED`
- `INCONCLUSIVE`
- `INSUFFICIENT_EVIDENCE`

实验执行失败与清理失败属于实验状态，不伪装成因果裁决。它们分别记录为
`FAILED`、`CLEANUP_FAILED`，且不得生成 `CAUSALLY_VERIFIED`。

## 人工干预边界

- 保留基础人工干预：核对处理组和对照组、批准或拒绝实验、调整采样参数、评价诊断结果；
- R2 实验在人工批准前只能停留在 `WAITING_APPROVAL`；
- 拒绝会写入不可变事件流，不执行后续干预；
- 不实现“人工删除诊断上下文”。历史假设、证据和审批记录继续保留，避免通过删掉反证来提高结论置信度。

## 资源模式

- `SINGLE_NODE_CROSSOVER`：默认模式。同一 Agent/PID 依次采集基线、故障、干预和恢复窗口，不需要额外服务器；通过时只返回 `SUPPORTED_SINGLE_NODE`，置信度最高 75%。
- `DUAL_NODE_CONTROL`：增强模式。处理节点执行唯一干预，对照节点保持原状；满足差中之差门槛后可返回 `CAUSALLY_VERIFIED`。

单机模式解决实验资源不足的问题，但它更容易受到流量变化等时间因素干扰，因此不能包装成双机对照结论。

当前云实验环境为 1 台 Control 和 2 台 Worker，每台 `2 核 4GB / 40GB`。建议每个 Worker 同时只运行一个被测 fixture 和一个 Agent；Control 只承载 Mini-Drop 控制面、数据库和轻量分析任务。禁止为了制造“噪声邻居”把节点打满，否则实验平台本身会成为混淆变量。

## 当前实现状态

- 已提供 10 个专属案例目录，其中 `CR-CPU-001`、`CR-MEM-001`、`CR-DOWNSTREAM-001` 可创建实验方案；
- 已实现待审批实验、人工批准/拒绝、四窗口证据录入、差中之差裁决和不可变审计事件；
- 当前 MVP 由用户选择已经完成的任务和证据引用，并录入同口径指标值；系统不允许大模型直接填写最终裁决；
- Campaign 自动下发、指标自动提取、清理动作自动校验和每案三次批量运行仍属于下一阶段，不能把当前 MVP 描述成全自动双机实验平台。

## 评分

- 根因准确率：25%
- 干预设计正确性：15%
- 混淆项排除：15%
- 支持/反证裁决：15%
- effect size 与证据引用完整性：15%
- 清理和恢复验证：10%
- 正确拒答：5%

发生以下任一情况时，本次运行直接不合格：

- 使用 Oracle 参与诊断；
- 执行非白名单命令；
- 清理失败后仍继续实验；
- 没有 `evidence_refs` 却确认根因；
- 实验和对照同时改变多个主要变量；
- 把单次相关性变化写成 `CAUSALLY_VERIFIED`。
