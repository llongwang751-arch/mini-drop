# 2026-09-26 测试工程审查与本地验证

这是本地代码审查和回归记录，不是云端发布记录。长期入口是 [TEST_ENGINEERING.md](../../docs/TEST_ENGINEERING.md)。原工作树中的历史报告、发布包、数据和用户新增文件保留。

## 实施内容

- 修复阶段耗时缺失被补零、质量不达标的降级响应被误判可用；旧实际 RAG 报告只兼容新增字段缺失，不放松旧指标、结论和哈希校验。
- 新增版本化风险合同与 smoke/python/local/business 入口，保留 HTML/JSON/JUnit/覆盖率/日志及源码摘要；零测试、未知跳过、报告缺失、重复或不完整 Go 结果、超时、执行中源码变化都不能通过。
- CI 增加 PostgreSQL 16 隔离专项，强制 5 个 Python 并发用例和 1 个 Go 真实幂等用例；增加 Go race，原生 Agent 拒绝零测试，Control 明确仅构建。
- Python CI 复用质量入口，上传包括 `.coverage` 在内的证据；补充直接使用但先前遗漏的 `jsonschema` / `PyYAML` 开发依赖。
- README、权威上下文、交接、业务验收和面试入口同步；教材逐文件索引通过原生成器更新。

## 验证结果

主机为 Windows，Python 3.14.0；Git HEAD `bef5ebc8ca84c6be820dd9f1f56cb80861f2a516` 加本次未提交修改。每份质量 JSON 另存具体源码摘要。CI Python 为 3.11，不能拿本机成绩替代该环境执行。

| 检查 | 结果 | 原始位置 |
| --- | --- | --- |
| 改前 Python | 625 passed / 7 skipped；行覆盖率 70.6038% | `output/sdet-review-20260926/baseline.xml`、`baseline-coverage.json` |
| 改后 Python 全量 | 650 passed / 7 skipped；行覆盖率 70.6058% | `output/quality/sdet-local-20260926-final/python-all/` |
| 改后 Web | 43 文件、203 passed | 同目录 `web/` |
| 改后 Go API | 73 个测试事件通过、1 skip（含子测试） | 同目录 `go/command-1.log` |
| Go 故障目标 | 编译检查通过；没有行为测试计数 | 同目录 `go-demo/` |
| 最终高风险 smoke | 82 passed，合同检查通过 | `output/quality/sdet-smoke-dependencies-20260926/` |
| 开发依赖补齐后合同回归 | 6 passed；已安装版本符合声明范围 | `test_taskkind_contract_generation.py`、`test_k8s_manifests.py` 命令输出 |
| 本机真实 HTTP | 3/3 符合各自预期；270 测量请求，另 36 预热 | `output/quality/sdet-business-20260926/` |
| 报告浏览器检查 | Edge 无头实际渲染，中文及结果可见 | `output/quality/sdet-smoke-20260926-final/report-preview.png` |
| 静态检查 | Ruff、合同检查、Git diff、CI YAML 及嵌入校验器检查通过 | 命令输出与各合同日志 |

最终完整 local 运行 ID：`20260926T105614Z-74a436ac`，运行前后源码摘要一致；总状态是 **PASSED_WITH_SKIPS**。Python 的 7 个 skip 是 PostgreSQL 5 项与 Chroma 2 项；Go 的 1 项是 PostgreSQL 幂等竞争。本机没有启动 Docker、连接真实数据库或运行原生 Linux 采集。

全量运行后只补充两项开发依赖声明，已安装并被全量运行使用的版本为 jsonschema 4.26.0、PyYAML 6.0.3；随后核对依赖范围并重跑 smoke/直接导入这些包的合同测试。全量报告保留当时源码摘要，不回写为后续版本。

25 个新增执行用例包括业务缺陷回归及质量执行器负例；行覆盖率基本不变。补充这些测试的价值是防止具体错误结果，不以覆盖率增长包装收益。

## 确认旧缺陷确实存在

只读加载 Git HEAD 的旧验收模块，用同一输入分别调用旧版和工作树修复版：

| 反例 | 旧版 | 修复版 |
| --- | --- | --- |
| 30 请求仅 1 条 retrieval=123ms | 阶段 P95=0 | 阶段 P95=123ms，样本数=1 |
| 30 条降级响应都成功但质量全不合格 | DEGRADED_AVAILABLE | REJECTED |

反例结果位于 `output/sdet-review-20260926/defect-counterexamples.json`。这是确定性逻辑反例，不是性能基准或真机 AI 诊断结果。

## 本次 HTTP 实验

每窗固定 30 请求，相同数据、问题集、到达率与并发上限。结果只描述当前本机固定样例。

| 场景 | 正常 P95 | 异常 P95 | 变更后 P95 | 结论 |
| --- | ---: | ---: | ---: | --- |
| RAG-01 重排候选过多 | 30.59ms | 316.91ms | 30.91ms | IMPROVEMENT_VERIFIED |
| RAG-02 导入争抢查询队列 | 31.98ms | 222.32ms | 27.38ms | IMPROVEMENT_VERIFIED |
| RAG-03 持续依赖慢与摘录回退 | 32.49ms | 154.74ms | 38.03ms | DEGRADED_AVAILABLE |

九个窗口固定引用检查通过率均为 100%。第三场景即使较快且引用合格，仍只判降级。这是一次小规模运行，不是稳定容量、长期 SLO 或生产收益；实验与其他本机回归存在资源竞争，不用于跨机器比较绝对耗时。

## 尚未执行与下一步

新 GitHub Actions 工作流未推送运行，PostgreSQL 专项、Linux race、原生 CTest 尚无本轮实测。Windows race 尝试因本机运行时 `0xc0000139` 退出，未当作通过。真实浏览器应用 E2E 与可选 Chroma 尚未加入新 CI 专项；本轮浏览器只验证生成的本地报告。

严格云端故障验收继续引用 2026-09-20 的 1/21，不改旧报告；没有新增生产诊断、修复或发布。后续优先执行新 CI、补真实浏览器及 Chroma 验证、重复性能实验，做透一个同负载真实业务修复案例。
