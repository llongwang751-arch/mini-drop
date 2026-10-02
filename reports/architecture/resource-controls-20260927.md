# 资源稳定性、容量区间与独立热点对照实测

本轮将 Mini-Drop 的测试工程链补到“风险判据 → 自动执行 → 原始证据 → 独立复核 → 可分享图表”。没有修改云端部署，也没有将受控样例数据写成生产成绩。

## 实测结论

| 实验 | 结果 | 能支持的结论 |
| --- | --- | --- |
| 本机 30 分钟持续请求 | 持续阶段与资源筛查通过；整轮 INVALID | 9000 次成功且引用检查通过，P95 41.15ms；80 RPS 档有 211 次未发出，不能据其推断服务容量 |
| 容量区间复测 | 整轮 PASSED；70 RPS 档 SLO_FAILED | 40/50/60 RPS 达标，70 RPS 全部发出但 P95 883.59ms；卸载后恢复，短持续阶段通过 |
| 隔离 Linux Python/Go | 两项 CONTROL_VERIFIED | 操作系统 CPU 与函数 profile 支持受控热点及撤销后的恢复；不是云端 AI 根因或代码修复验收 |

直接查看：[30 分钟 HTML](../business-acceptance/resource-controls-20260927/long/report.html)、[容量区间 HTML](../business-acceptance/resource-controls-20260927/refined/report.html)。图中保留失败档位、INVALID 状态和资源波动；两张 PNG 已实际打开检查。

## 30 分钟：先保存无效容量结果，再分析有效的持续阶段

使用默认 5/20/40/80 RPS 各 15 秒，最大在途 64；随后恢复 15 秒、5 RPS 持续 1800 秒，P95 门槛 200ms、成功/引用检查至少 99%。发压端和样例服务共享本机，业务是内存 SQLite FTS5、固定语料摘录和固定 10ms 依赖模拟。

阶梯前三档通过，80 RPS 计划 1200 次、发出 989 次、未发出 211 次。未发出记录保留 CLIENT_INFLIGHT_LIMIT，不虚构延迟，也不从成功率分母排除。因此整轮 INVALID，进程正常回收后 CLI 仍返回非零；这是门禁生效，不是需要隐藏的失败。

持续阶段 9000/9000 次全部成功且引用检查通过，P95 41.1544ms，60 个 30 秒窗口全部通过。资源读取失败 0 次、持续样本 1780 个；首末三分之一 RSS 中位数 38850560→38252544 bytes（约 -0.57MiB），线程 2→2，Windows 句柄 166→166。资源筛查 PASSED，仅表示这个 PID 和时间窗没有触发增长预算，不证明不存在内存泄漏。

该实验在 `782b4a8` 的未提交工作树启动，测量源码运行中保持冻结；实现随后进入 `d27ca56`。不得把 Git 基线误写成全部测量实现所在提交。归档的 `measurement-sources.zip` 逐文件字节匹配报告中的 SHA-256，包含当时的执行器、采样器和样例源码；保留原报告的 git_head。

## 区间复测：提高发压端在途上限，保持业务 SLO

在原实验结束之后执行，最大在途从 64 调为 128，阶梯细化为 40/50/60/70 RPS、每档 10 秒。SLO、语料与模拟依赖不变。这是另一组容量探索配置，不是业务代码性能优化的前后对比。

| 阶段 | 计划 / 实发 | P95 ms | 判定 |
| --- | --- | --- | --- |
| 40 RPS | 400 / 400 | 43.80 | PASSED |
| 50 RPS | 500 / 500 | 47.47 | PASSED |
| 60 RPS | 600 / 600 | 58.02 | PASSED |
| 70 RPS | 700 / 700 | 883.59 | SLO_FAILED：P95_LIMIT |
| 卸载恢复 5 RPS | 75 / 75 | 46.83 | PASSED |
| 持续 60 秒、5 RPS | 300 / 300 | 45.01 | PASSED |

所有 2575 个请求发出，成功/引用检查均为 100%，无发压端饱和；资源筛查通过。整轮 PASSED 表示容量探索测量有效、已有低档达标、恢复与持续阶段通过；不表示所有压力档都满足 SLO。只能说本次配置下最高已测通过档为 60 RPS，首个超标档为 70 RPS，不能将 60 RPS 写成生产最大容量。

复测从 `2785453` 工作树启动，采样断档修复当时尚未提交、随后进入 `3b6809c`；同样保存逐字节匹配的测量源码，不改写 git_head。

## 发现并修复的测试工具缺陷

原采样摘要只检查持续阶段内部相邻样本，可能漏掉首次启动过晚和跨阶段断档。两个负向用例先实际失败，见 [修复前日志](../business-acceptance/resource-controls-20260927/resource-gap-before.log)。修复后检查整个连续采样序列及首样本延迟，任一间隔超过 5 倍采样周期即 INVALID；没有放宽增长预算。

补充采样线程退出/写盘失败测试、Go profile 错函数拒绝、撤销后同 PID 与故障已停止回读。资源、热点对照、原始复核、图表四组针对性回归共 57 项通过。30 分钟原始记录经修复后的复核器重算结论仍一致：持续与资源通过，整轮 INVALID。

## 独立 Linux 热点对照

实际执行源码 demo 和真实子进程；每项先正常 4 秒、注入 8 秒、撤销后恢复 4 秒，CPU 来自 OS 累计 CPU 时间差，100% 为一个逻辑核。最终 CI 代码为 `3b6809c`，测试 merge 为 `36a40a5f98072e84f080f898d524b86c2c0034cd`。

| 对照 | 正常 / 故障 / 恢复 CPU | 函数观察 | 结果 |
| --- | --- | --- | --- |
| Python | 0.25% / 94.12% / 0.50% | `source_hot_function` 新增 1383 个合作式栈样本 | CONTROL_VERIFIED，撤销回读通过 |
| Go | 0.75% / 104.87% / 1.00% | 实际 pprof 5.24 CPU 秒，`main.goCPUHotFunction` 累计 95.04% | CONTROL_VERIFIED，撤销回读通过 |

Python 栈采样是进程内部合作式插桩，Go 为原生 pprof，二者不能描述成同一种外部采样器。撤销故障减少了工作量，`fix_verified=false`；未经过云端 Agent、AI 编排和严格来源门禁，`ai_root_cause_verified=false`。历史严格 AI 根因仍是 1/21。

原始快照、三窗 CPU、pprof、源码指纹位于 [热点归档](../business-acceptance/resource-controls-20260927/hotspot-controls/report.json)。仓库省略编译后的 Go 可执行文件，报告保留其原 SHA，完整 CI ZIP 仍在原本机 output，下载来源与 ZIP 哈希见 [制品来源](../business-acceptance/resource-controls-20260927/ci-artifact-source.json)。

## 远程质量门禁

[CI 36301690538](https://github.com/llongwang751-arch/mini-drop/actions/runs/36301690538) 的 **13/13 作业通过**，被测试的 PR head 为 `3b6809c9c53b192255c585b7ca84517e4b122875`。Python **780 passed / 7 skipped**；跳过的 PostgreSQL/Chroma 依赖项由独立专项实际执行。原生 Agent CTest、Go race、真实 Chromium、业务三轮重复、短版持续请求及新增热点对照均通过。Trivy 等报告型作业成功不等于安全漏洞零项。

原始 [CI 作业清单](../business-acceptance/resource-controls-20260927/ci.json) 与 [Python 门禁报告](../business-acceptance/resource-controls-20260927/ci-python-report.json) 已保存。随后提交仅归档证据和同步文档，不将这一轮结果冒充后来提交已经跑完的 CI。

## 复现与证据复核

在仓库根目录安装 `python -m pip install -e ".[dev,reports]"`。每次使用新的输出目录，拒绝覆盖历史数据。

```powershell
python scripts/run_load_endurance.py --soak-seconds 1800 --output output/quality/resources-new
python scripts/verify_load_report.py output/quality/resources-new/report.json
python scripts/render_load_report.py output/quality/resources-new/report.json --output output/quality/resources-new/report.html
```

容量复测使用 Python API，完整配置也保存在机器报告中：

```python
from pathlib import Path
from scripts.run_load_endurance import Plan, run
run(Path("output/quality/refined-new"), Plan(
    rates=(40, 50, 60, 70), step_seconds=10, concurrency=128, soak_seconds=60,
))
```

复核归档时，将对应 `requests.zip` 解压到新的临时目录，与字节不变的 `report.json` 放在一起，再调用 `verify_load_report.py`。完整但 INVALID 的原实验不加 `--require-pass`；给容量复测加该选项。复核器重算请求槽位、分母、P95、窗口与资源摘要，哈希仅保证文件一致性，不证明任意人无法整体伪造数据。

下一步仍需独立压测机、小时级观测和真实云端来源合同；不需要再堆技术栈。面试可现场演示“发压端漏发为何不能算服务容量”“断档负向用例如何避免假通过”，比只展示一张全绿截图更能体现测开判断力。
