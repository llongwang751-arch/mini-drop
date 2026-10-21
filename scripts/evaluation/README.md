# 诊断方法与经验的固定评测

这里的 `diagnostic_cases.json` 是明确导入的合成回归集，没有生产历史、用户资料或真实采样。22 个案例覆盖 CPU、分配/GC、锁/IO、正常高负载、证据不足和非法观测。`holdout` 是作者可见的预留回归分区，不是外部盲测，不能据此宣传生产根因准确率。

`ground_truth` 的类别描述作者控制的线索情境。所有案例 `causal_root_proven=false`：CPU 热点、GC 符号或等待符号都不能独立证明业务缺陷或因果关系。正常负载允许模型明确指出证据不足；不能因为匹配到 CPU 方法就算诊断出故障。

## 离线：执行真正的服务入口

在仓库根目录执行：

```powershell
$env:GOMAXPROCS='2'
$env:GOMEMLIMIT='512MiB'
$env:GOFLAGS='-p=1'
python -X utf8 scripts/evaluation/evaluate_diagnostics.py --mode offline --output .local/diagnostic-evaluation-20261021/offline.json
python -X utf8 -m unittest discover -s scripts/evaluation -p 'test_*.py' -v
```

CLI 调用 Go 的 `TestDiagnosticEvaluation*`。固定案例实际经过 `MatchDiagnosticSkills`、`diagnosisTool` 和 `validateDiagnosisReport`。`off` 与 `skills` 使用同一份当前观测，对照未知编号、伪造元数据、仅先验引用、混合引用及缺少必需工具等边界。报告正文是明确的控制器样例，仅用于协议检查，没有模型调用；不会把样例语义记成模型根因准确率。

离线结果分开报告 Skill 路由的精确匹配、合法/非法输入边界和报告防线。`off` 不加载 Skill，不会因没有 Skill 被记为根因 0 分。耗时是当前机器的纯函数/服务测试耗时，模型 token 和费用为 `null`。

过期与异主案例会调用实际来源重验入口的早期拒绝分支。跨项目、版本、共享撤权、来源轮次和撤销的完整存储链路应由 PostgreSQL 集成测试验证；本套离线评测不会复制 SQL 过滤规则来冒充数据库实测。`prior_candidates` 中过期、作用域不匹配和恶意文本均只是负例背景，不会写入案例库。恶意历史用例验证“历史不能单独当成当前证据”，不宣称已经证明模型完全抗提示注入。

## 在线：只对外部真实 API 成对结果评分

本工具不联网、不登录、不启动采集或模型，也不会自动把既有生产历史当夹具。实际 API 调用由获得授权的独立采集器完成，`--mode live` 只读取该采集器保存的 JSON。

```powershell
python -X utf8 scripts/evaluation/evaluate_diagnostics.py --mode live --manifest --output .local/diagnostic-evaluation-20261021/manifest.json
python -X utf8 scripts/evaluation/evaluate_diagnostics.py --mode live --input .local/diagnostic-evaluation-20261021/private/live-pairs.json --output .local/diagnostic-evaluation-20261021/live-metrics.json
```

需要只对照方法目录时使用 `--on-mode skills`，默认比较 `off` 与 `experience`。每个 case 必须有完整的两个结果，且任务、采集轮次、分析任务、分析轮次、用户问题相同，诊断运行 ID 不同；不能重复提交同一结果。`input_digest` 本身包含知识开关，不能要求两组相同。

只有四个带 `reproduction` 的案例可绑定在线导入任务：

| case                             | 分区    | 用途                      |
| -------------------------------- | ------- | ------------------------- |
| `seed-cpu-folded-dev`            | dev     | 独立 CPU 人工纠正案例来源 |
| `seed-allocation-folded-dev`     | dev     | 独立分配人工纠正案例来源  |
| `live-cpu-folded-holdout`        | holdout | CPU off/on 对照           |
| `live-allocation-folded-holdout` | holdout | 分配线索 off/on 对照      |

创建导入任务时把 `case.reproduction.collapsed_text` 原样交给 `kind=IMPORT_COLLAPSED` 的 `collapsed_text`。它们的 `profile` 由真正的 `analyze_folded → make_result` 生成。CLI 比较实际结果的稳定语义投影：`kind`、`total_samples`、`node_count`、`top_functions`、`flamegraph`、`summary.unit`、`summary.source`。平台/Python 版本、制品 ID、动态规则建议等环境元数据不参与等同判断；通过表示同一分析语义，不表示整个结果 JSON 字节完全一致。

种子与 holdout 的符号有部分重叠，权重不同，输入指纹不同。只允许用不同 case 的 `dev` 数据播种经验，不允许把被评测 case 自己或其他 holdout 的答案写入经验。测试问题应保持中性，不把 `ground_truth` 或期望 Skill ID 填进模型问题。

输入协议如下；尖括号内容必须替换成真实采集器记录，示例本身不是实测结果：

```json
{
  "schema_version": 1,
  "mode": "live",
  "runs": [
    {
      "case_id": "live-cpu-folded-holdout",
      "knowledge_mode": "experience",
      "fixture_provenance": {
        "source": "imported_synthetic",
        "case_sha256": "<manifest 中的固定 case 摘要>",
        "task_id": "<实际授权测试任务 ID>"
      },
      "analysis_result": "<实际 GET 的分析结果对象>",
      "run": "<实际完成的 DiagnosisRun 对象，含 trace/report>",
      "elapsed_ms": 1234,
      "experience_sources": [
        {
          "case_id": "seed-cpu-folded-dev",
          "case_sha256": "<种子 case 的固定摘要>"
        }
      ]
    }
  ]
}
```

实际输入中 `analysis_result`、`run` 是对象。`run` 必须有 `id`、`task_id`、`attempt_id`、`analysis_job_id`、`analysis_run`、`goal`、`mode=model`、`knowledge_mode`、完成状态、`trace` 和 `report`。成功运行必须包含当前真正的运行元数据：`framework=agentscope`、`framework_version=2.0.10`、`agent_class=agentscope.agent.Agent`、`model_calls>0`。有历史经验时必须提供显式的 `experience_sources` 以检查分区泄漏；没有加载案例时无需填写。

固定 case 摘要的协议为：

```python
hashlib.sha256(
    json.dumps(
        case, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
).hexdigest()
```

## 指标与人工判断

证据覆盖只从实际 `trace` 与 `report.hypotheses[].evidence_ids` 计算，区分当前观测工具与 `read_diagnostic_knowledge` 先验。已知编号覆盖、每条假设至少引用当前观测，可以验证引用边界，不能验证解释语义正确。方法路由仅依据服务附加的 `knowledge.skills`，不能等同于根因确认。

API 报告目前是自由文本，没有受约束的根因类别枚举。因此不会用关键词猜测类别或拒答，也不会自动把自评当分数。需要语义计分时，每行可由独立人工审阅添加：

```json
{
  "assessment": {
    "method": "manual_review",
    "reviewer": "<独立审阅标识>",
    "category": "cpu_hotspot",
    "abstained": true,
    "actionable_defect": false
  }
}
```

类别允许 `cpu_hotspot`、`allocation_gc`、`lock_io`、`normal_load`、`insufficient_evidence`、`invalid_evidence`。`abstained` 表示明确拒绝当前证据不足以支持的确定结论，可以同时提出可证伪假设；`actionable_defect` 表示把当前情境判为需要修复的实际缺陷。正常负载的误报率只在正常案例中计算，必需拒答的召回只在证据不足案例中计算。

没有人工审阅时，类别命中、拒答和误报的分母为零、rate 为 `null`，同时显示人工审阅覆盖率。所有率保留分子和分母；小样本不能外推生产。延迟仅报告实际采集器的墙钟 median/min/max，不虚构 P95。token 只取真实 `report.agent_runtime.usage.input_tokens/output_tokens`，缺失保持未知；没有核验定价来源，费用始终为 `null`。指标输出不复写报告正文、原符号、Cookie、token 值或用户问题。

JSON 来源由外部采集器声明，运行元数据与 SHA 一致性也不是加密证明。公开结果必须保留数据来源、样本范围、模型版本及未覆盖边界。

## v2：事实协议与重复试验

`diagnostic_cases_v2.json` 单独保留 4 个新增 holdout：正常高负载、证据不足、复合线索、错误分配归因负控；另复用两个未改动的 dev 经验种子。v1 的 22 个案例及其 canonical SHA 保持不变。默认 CLI 仍读取 v1；验证新协议时明确指定 v2 文件：

```powershell
python -X utf8 scripts/evaluation/evaluate_diagnostics.py --mode offline --cases scripts/evaluation/diagnostic_cases_v2.json --output .local/diagnostic-evaluation-20261022/facts-offline.json
python -X utf8 scripts/evaluation/diagnostic_experiment.py plan --repetitions 3 --seed 20261022 --output .local/diagnostic-evaluation-20261022/plan.json
```

第一条运行真正的 `TestDiagnosisFactEvaluation*`：当前观测、错误正 self 数量、错误单位、人工导入冒充参数计划、无分配证据却归因分配等正负控均经过实际工具和 v2 验证器。其报告由明确控制器构造，模型调用为零，语义评分仍是 `null`。

第二条只生成预登记计划，不执行模型。4 个 holdout、3 次重复、`off/experience` 两组共有 24 个待执行 trial。固定种子决定分块顺序，并在同一 case/重复编号内随机两组先后。外部采集器应原样保留计划，把其中 `experiment` 放进 live 输入顶层，每行增加 `replicate_id`，每次实际调用使用独立诊断运行 ID。不能复用同一结果，也不能丢掉失败重复只报成功。CLI 会拒绝缺失的预登记 case/重复编号、重复运行 ID、不同问题或不同来源身份的成对结果。

计划由调用方声明，没有外部时间戳背书；同一合成情境重复调用模型不等于新增独立业务故障。固定问题、模型配置、输入、经验种子和环境条件由实际采集器负责，偏差需要记录。工具不会把随机波动、小样本延迟差异或 token 数变化解释成语义准确率提升。

v2 模型提交结构化事实请求：E 编号、有限字段路径、运算、声明值和单位。Go 对照真实工具观测核验后生成现有摘要、假设中的事实描述与验证步骤。模型不再提交权威 `summary/claim/verification` 自由文字。热点数量只描述已返回 Top 表；调用路径计数只描述有界上下文，不推广为全树或所有时段的唯一性。工具来源计划必须确有对应参数；模型自行建议的 60 秒/99 Hz 可以作为待审建议，不能写成工具已经返回或校验的计划。

`unverified_interpretation` 如存在，属于单独标记的未核验模型解释。算术、单位和出处一致并不证明这段解释、业务根因或建议收益正确。v2 报告覆盖指标也只表示收到相应协议事实字段，不是语义正确率。

## 独立人工评审录入

实际成对试验完成后，可生成未填写的审阅模板。模板不包含问题、报告全文或原符号，也不预填类别分数：

```powershell
python -X utf8 scripts/evaluation/diagnostic_experiment.py review-template --input .local/diagnostic-evaluation-20261022/private/pairs.json --output .local/diagnostic-evaluation-20261022/private/reviews-blank.json
python -X utf8 scripts/evaluation/diagnostic_experiment.py apply-reviews --input .local/diagnostic-evaluation-20261022/private/pairs.json --reviews .local/diagnostic-evaluation-20261022/private/reviews-filled.json --output .local/diagnostic-evaluation-20261022/private/pairs-reviewed.json
python -X utf8 scripts/evaluation/evaluate_diagnostics.py --mode live --cases scripts/evaluation/diagnostic_cases_v2.json --input .local/diagnostic-evaluation-20261022/private/pairs-reviewed.json --output .local/diagnostic-evaluation-20261022/reviewed-metrics.json
```

人工通过实际平台报告和对应分析结果审阅后填写 `reviewer`、`category`、`abstained`、`actionable_defect`，明确确认 `independence_attested=true`。每行绑定实际运行 ID、case、重复编号、报告和分析投影 SHA，报告改变后旧审阅不能继续套用。新增类别允许 `mixed_signals`、`business_latency`、`downstream_dependency`、`deployment_regression`，都属于待核验方向；时间重合或登记依赖不是因果证明。

未审阅行保留 null；部分审阅只按实际覆盖量计算。`agent_review` 不能导入为 `manual_review`，已有人工记录不能静默覆盖，CLI 也不会覆盖原始输入文件。v2 的人工分数必须附明确人类来源、独立性声明及报告 SHA。工具不能证明填写者真实身份；只有实际遮蔽知识模式后才可勾选盲评。没有独立人工结论时，真实语义评分继续保持 null。
