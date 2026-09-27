# 测试工程远程验证与实际业务缺陷复验（2026-09-27）

## 真实 CI 暴露的问题

[草稿 PR #1](https://github.com/llongwang751-arch/mini-drop/pull/1) 从 release 分支合入个人仓库 master，覆盖此前尚未验证的发布分支积累；本轮未合并、未部署。

1. [首轮 36296547283](https://github.com/llongwang751-arch/mini-drop/actions/runs/36296547283)：原始 JUnit XML 的尾空白使仓库质量检查失败。保留历史 XML，空白检查仅排除 reports 下 XML；代码和文档仍检查。干净 runner 用 `.env.example` 准备 Compose 检查配置。
2. [第二轮 36296726561](https://github.com/llongwang751-arch/mini-drop/actions/runs/36296726561)：10 个作业成功，Python 失败。`test_worker_starts_and_advances_autonomous_sessions` 得到 22 而非 28。初始 `_last_experiment_evaluation=0` 错把单调时钟原点当成首次执行时间，机器启动不足 300 秒时跳过首轮评估。本地长时间运行的机器掩盖了问题。
3. 修复采用 `None` 表示尚未执行；测试固定启动时间 0、12、900000 秒，检查首轮执行、间隔不足不执行、刚到 300 秒执行一次。同一时间重复轮询不重复评估。没有增加 sleep 或放宽断言。远程修复结果待下一轮记录。

第二轮已实际通过 PostgreSQL Python 5 项与 Go 幂等竞争 1 项、Go race、真实 Chromium、三轮 HTTP 业务验收、Web 测试和构建、Agent CTest、Control 构建、仓库质量与 Chroma 37 项零跳过。Chroma 使用临时本地数据库和测试 embedding，不访问外部模型；普通 Python 配置允许的数据库/检索跳过由专项实际执行覆盖。供应链作业为报告模式，成功不等于零漏洞。

另修复质量执行器在关键覆盖率报告缺失时可能假绿的问题，加入负向回归。Worker 与门禁定向测试共 37 passed。

## 实际检索缺陷闭环

这是对 2026-09-13 已保存业务修复的重新验证，新增加独立正确性回归和 cProfile 证据。没有修改历史冻结源码，也不将旧修复宣称为本轮新完成的业务代码改动。

- 旧版指纹：`d85a41d8d1f7593b9276d9a43bae43b76ad15ed71908e5889dac1616851c419a`。
- 修复版指纹：`a1a90b112093bae2c430a07909aa93cbeb3a8144a01cd76982a59745a6fa7f56`。
- 问题：本地词法检索加载整个 ORM 行及不用的 1536 维 embedding JSON。独立 cProfile 对 603 行、6 次查询记录旧版 3618 次 `json.loads`；修复版该调用路径为 0 次。剖析开销不能作为 HTTP 性能指标。
- 历史修复：只读 id/content/parent_content，流式读取，查询词项计算一次，有界 TopK 保持评分与 id 顺序。仍为 O(N) 文本扫描。
- 新独立回归：手工预期分数和排序、跨租户搜索/删除隔离、空查询/无匹配/top-k 边界、增删改新鲜度，两个版本均通过四组检查，冻结源前后指纹一致。

Windows / Python 3.14、本机独立 SQLite、600 条人工分块、6 个固定问题，1 请求/秒、并发上限 4，每窗 30 次测量与 4 次预热：

| 窗口 | 源码版本 | HTTP P95 | 成功率/固定答案检查 |
| --- | --- | --- | --- |
| 正常基线 | 修复版 | 35.27ms | 100% / 100% |
| 故障复现 | 旧版 | 271.13ms | 100% / 100% |
| 变更后 | 修复版 | 37.99ms | 100% / 100% |

恢复门槛为基线的 1.3 倍，即 45.85ms；结果 `IMPROVEMENT_VERIFIED`。这是一轮同负载版本对照，不是容量极限或生产 SLO；业务三轮稳定性 CI 是另外的合成场景。实际引擎实验使用摘录回答，没有验证远程 LLM、完整助手 UI、真实 Milvus 或 AI 根因，严格 21 场景 AI 根因验收仍沿用 1/21。

## 复现与证据

以下 PowerShell 命令在仓库根目录执行；输出必须是新目录。外部冻结源码是显式前提，干净检出缺少它时不能宣称此实验已经运行。

```powershell
python scripts/verify_actual_rag_search.py --source output/acceptance/actual-rag-20260913/source-before --output output/quality/rag-search-before-new
python scripts/verify_actual_rag_search.py --source output/acceptance/actual-rag-20260913/source-after --output output/quality/rag-search-after-new
python scripts/run_actual_rag_acceptance.py --before output/acceptance/actual-rag-20260913/source-before --after output/acceptance/actual-rag-20260913/source-after --output output/quality/actual-rag-new --rows 600 --arrival-rate 1
python scripts/run_quality_gate.py --profile retrieval
```

本轮原始证据（仓库本机保留）：

- `output/quality/actual-rag-closure-20260927/report.json`：三窗原始请求、比较判据、资源和指纹。
- `output/quality/rag-search-before-20260927/`、`output/quality/rag-search-after-20260927/`：各自 report.json、search.prof、全新 fixture.db。
- `output/quality/ci-validation-20260927/`：GitHub 作业状态、失败日志与下载的原始制品。GitHub 链接提供独立远程运行记录，制品受平台保留期限制。

面试展示可以依次讲：风险与判据 → CI 发现环境相关缺陷 → 确定性时钟回归 → 实际检索热点证据 → 修复前后同负载对照 → 正确性与隔离回归。未完成方向仍是更多真机故障的独立根因验证、真实上下游故障及更长时段容量测试。
