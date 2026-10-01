# 工程诊断验收与扩展新问题

2026-10-01用户要求降低不适合求职演示的默认门槛。默认采用`engineering-diagnosis.v1`，严格因果实验作为独立专项。两个版本评价的能力不同，不覆盖旧记录。

## 为什么一直显示0

页面的`latest_acceptance`读取冻结的2026-09-30批次，部署新代码不会重新执行或更新该批次。严格评分器还要求`VERIFIED`、独立反证/对照、完整匹配率，以及允许因果结论的范围；当前数值和运行时报告明确标为`BOUNDED_OBSERVATION`、`causal_root_cause_verified=false`。这些报告无论置信度多高，都不能通过这个范围检查。没有对应的原因干预实验，单纯降低置信度阈值无法解决。

此前把这种专项标准当成默认项目完成度，导致实际定位能力被旧0/21遮住。默认改成工程诊断判断通过率，并单独列出具体定位、有效反证和未验收案例；历史严格分数折叠显示，不作为当前能力总分。

## 放宽与保留的要求

| 项目 | 默认工程验收 | 严格因果实验 |
|---|---|---|
| 目标身份、任务/产物/Analyzer来源、SHA与测量真实性 | 必须 | 必须 |
| 有依据的具体观测或源码路径 | 必须；数值重新计算，不能靠关键词 | 必须 |
| 独立因果对照 | 可选，不用缺对照阻止有界判断 | 必须 |
| 固定调查轮次 | 不要求；按证据能否给出判断评分 | 依专项协议 |
| 会话必须COMPLETED | 不要求；INSUFFICIENT_EVIDENCE中的完整反证也可合格；运行中、失败、取消不合格 | 原协议保留 |
| 撤销注入、恢复、清理与收束 | 故障实验必须 | 必须 |
| 同负载代码修复证明 | 单独成绩，不作为诊断通过前提 | 要宣称修复则必须 |

工程结果包括`LOCALIZED_ANOMALY`、`SUPPORTED_OBSERVATION`、`REFUTED`、证据不足及证据冲突。REFUTED可以通过“诊断判断”，不能通过“异常路径定位”，也不能推广为全部业务健康。

当前按新规则重评已有三个冻结真机案例：诊断判断3/3，异常路径定位2/3，有效反证1条。共注册21类，另18类未按此规则验收。没有执行新21类批次，不宣称21/21，也不称这次重评为新准确率测试。

## 代码入口

- `contracts/engineering_diagnosis.json`：规则版本、21类的机器信号域、可选Profile合同、案例SHA与一个或多个证据清单。
- `scripts/evaluate_engineering_diagnosis.py`：通用评分器，解析引用、核对来源与原始数值，独立记录诊断和定位，不修改报告因果标志。
- `scripts/build_engineering_diagnosis.py`：核验清单中的原始字节，生成网页只读索引；`--check`已加入质量门禁。
- `web/src/components/EngineeringDiagnosisSummary.jsx`：当前工程结果、未验收数量与原诊断入口；故障卡优先展示对应工程成绩。
- `scripts/run_fault_plaza_strict_acceptance.py`：明确保留为严格专项，原0/21文件保持不变。

## 新增一个问题的流程

1. 定义可观察的现象和范围，例如“目标进程RSS在窗口内增加8MiB”，不要直接把增长叫内存泄漏。声明单位、阈值和反证条件。
2. 在`server/app/drop_insight/fault_plaza.py`注册安全的固定启停接口、目标和采集器；已有业务接入仍按`SERVICE_INTEGRATION.md`，不允许浏览器传入任意命令或URL。
3. 选用已有机器信号域。CPU、内存、I/O、HTTP、队列等已登记字段可复用；新的语义需要补Analyzer指标和`performance_criteria.py`字段，不能把HTTP延迟冒充TCP重传。
4. 做独立的真实采集，保留目标身份、Task/Attempt/Artifact/Evidence、下载SHA、撤销恢复与清理。健康/反证样本也保留；不只收成功样本。
5. 将新批次归档成独立清单，把清单路径/SHA追加到合同`evidence_manifests`，更新对应`scenarios`的`case_path`/`case_sha256`。新类型新增注册项，原批次和文件不修改。评分器不写死三个Go案例；已有内存域复用的回归测试验证了扩展方式。
6. 运行生成器、负向回归和CI，再发布网页索引。缺身份、篡改数字、缺测、错误信号域和未清理都应失败；规则含义变化时增加规则版本，保留旧成绩。

```powershell
python scripts/build_engineering_diagnosis.py
python scripts/build_engineering_diagnosis.py --check
python -m pytest tests/test_engineering_diagnosis.py -q
python scripts/run_quality_gate.py --profile python --output output/quality/engineering-diagnosis
```

验收规则不能替代采集器。新问题若没有可测量的指标，先补观测能力；若要证明真正原因，再增加同输入、同负载的原因干预专项。这样可以扩展工程诊断，而不必每个问题一开始就完成完整因果实验。

## 已验证的线上版本

评分/展示源码0fa79f15的[CI36861902293](https://github.com/llongwang751-arch/mini-drop/actions/runs/36861902293)成功13/13：Python1382通过/10登记跳过、Web299通过、真实PG8通过零跳过；不可变Git源码本地Web299/构建/体积门禁通过。线上Web20261001T122233Z已由并行前端任务发布，包含相同工程组件与语义一致的生成索引；整体为冻结工作树构建、300项测试，并非本提交精确CI产物。本轮直接复验现有发布，保留其界面改动。58份Web、Worker/Analyzer各193份SHA复核，13容器健康，21故障inactive，Office PID1650962/NRestarts=0。真实Chrome新成绩、三张卡与诊断入口、正常检查、9份下载SHA和4种宽度通过，无JS/HTTP错误。未创建新诊断、注入故障或重跑已接受的一小时。原始记录见[本轮交付](../reports/architecture/engineering-diagnosis-20261001.md)与[90文件SHA清单](../reports/quality/engineering-diagnosis-20261001/manifest.json)。

当前剩余18类是“未按工程标准验收”，不能展示为已通过，也不是本轮失败。扩展时按下述源合同和独立证据流程逐域补齐。
