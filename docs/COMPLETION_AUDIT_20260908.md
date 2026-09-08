# Mini-Drop 完成度审计（2026-09-08）

本文只记录可由代码、测试或云端报告证明的状态。`代码完成`、`自动验收通过`、`云端发布完成`和`人工页面确认`是四个不同结论，不能互相替代。

## 九项闭环状态

| 项目 | 当前状态 | 可核验证据 | 仍然不能宣称什么 |
|---|---|---|---|
| 21 个故障场景逐项闭环 | 云端 live 验收完成 | `scripts/run_fault_plaza_closure_campaign.py` 在同一版本得到 21/21；每项均有真实 Diagnosis、Task、Attempt、非空 Artifact、SUPPORT Evidence、Report 引用、终态和清理 | 这是受控 Linux live E2E，不是生产准确率 |
| Java GC 独立计数器 | 云端 live 验收完成 | 新场景生成 2 个已校验 `jvm_gc_metrics` 样本并以 CONTROL Evidence 入链，同时有 2,828 个 allocation Profile 样本；Java 容器无重启/OOM | 旧报告仍保持 `PARTIAL_WITHOUT_COUNTER`，不能回写成新结果 |
| 540/500 测试集口径 | 完成 | 540 条是 21 个合同生成的确定性受控回放；500 组同题同两次工具预算为 41.20% 对 68.40%，差值 27.20 个百分点，bootstrap 95% 区间 `[23.2, 31.2]`，精确符号检验 `p=2.295887e-41`，改善 136、退化 0 | 不是 540 次云服务器故障，也不是生产准确率 |
| 实时与完整 LATS | 边界完成 | 实时工具链标记 `BUDGETED_LATS / LIVE_PROGRESSIVE`；只有冻结 observation + reset proof 才标记 `FULL_LATS / FROZEN_REPLAY` | 真实现场不可回滚，不能宣称兄弟分支处于完全相同状态 |
| Skill 随机 A/B | 代码完成，待云端迁移和页面验收 | 服务端持久 Experiment/Assignment/Observation/Metric；带盐稳定随机分流；两比例 z 检验、95% 区间、样本量、效果和安全护栏；页面支持真实分流与人工/Oracle 标签 | 旧双会话页面不是随机实验；无标签不能算根因准确率 |
| Skill 自进化 | 受控闭环完成 | 候选 → 离线/在线评测 → 自动生成 `ROLLOUT_RECOMMENDED` → 授权人员审批 → 隔离/回滚；后台只在新标签后追加指标快照 | 不会自动改 Prompt、代码、权限或直接切生产流量 |
| 跨诊断用户记忆 | 代码完成，待云端迁移和页面验收 | PostgreSQL 按 principal 保存显式偏好；只允许语言、解释详略、时区、低风险优先；页面可写入/删除，Agent 只把它当可选提示上下文 | 不保存旧根因、查询、PID、目标绑定或工具权限；不是自动抽取的通用长期记忆 |
| 云端版本号统一 | 完成 | `/opt/mini-drop-current` 已原子指向 `/opt/mini-drop-releases/20260908T094648Z`，主文档同步更新 | 旧报告仍按各自历史版本解释，不能回写 |
| 最终页面验收 | 待执行 | 自动化将检查健康、路由、实验面板、记忆面板、诊断链和 Java GC；最后由用户逐项确认页面交互与观感 | 浏览器自动化通过不等于用户已经亲自验收满意 |

## 测试口径

- Python 全量回归：`501 passed, 3 skipped`（2026-09-08 候选代码）。
- Web 全量回归：`32` 个测试文件、`133` 个测试通过；生产构建与 bundle 检查通过。
- Go：模块全包测试通过。
- OpenAPI：`80` 个 method/path 对与实现一致。
- Compose：`docker-compose.control.yml` 使用面试环境变量完成静态配置校验；本地 Windows Docker daemon 不作为 Linux live 证明。

上述代码测试不会替代云端真实故障验收。最终发布版本为 `/opt/mini-drop-releases/20260908T094648Z`；完整 Campaign 为 21/21，机器报告 `reports/ai-diagnosis/fault-plaza-full-21-final-v2-20260908.json` 的规范化 payload SHA-256 为 `c8f7d0413fce94601cd905238533553f2d20509ae2492035e03f8c78faa07eea`。尚未完成的是用户亲自逐页点击后的主观页面验收，以及依赖真实随机流量和长期指标窗口的生产 A/B。
