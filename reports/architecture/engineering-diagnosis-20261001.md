# 默认工程诊断验收交付 · 2026-10-01

默认验收改为 `engineering-diagnosis.v1`，允许有证据的路径定位、观测判断和完整反证。原严格因果评分、原始报告及旧 0/21 都保留，独立统计。新规则没有把有界观测改称因果根因。

## 为什么重复得到 0/21

旧页面读取冻结的 2026-09-30 验收索引。新代码部署不会重新运行该批次。旧严格评分器还要求独立反证/对照、完整匹配及因果范围；当前可信的数值/Profile 报告声明 `BOUNDED_OBSERVATION`、`causal_root_cause_verified=false`，会被范围检查拒绝。仅降低置信度不能消除这种标准与报告能力的冲突。

此前用严格专项分数表示整个项目完成度不合适。这次将默认分数改为工程诊断判断，另列异常路径定位、有效反证和未验收数量。

## 实际成绩及边界

| 项目 | 结果 |
| --- | --- |
| 工程诊断判断 | 3/3 |
| 异常路径定位 | 2/3：CPU 业务热函数、HTTP 调用耗时路径 |
| 有效反证 | 1：同步写入同窗口 684 次、平均 0.081ms，反驳本次慢操作假设 |
| 已注册 / 尚未按新标准验收 | 21 / 18 |
| 新一轮故障注入 | 未执行；本轮重评已冻结真机记录 |
| 因果与同负载修复验收 | 新标准不评价；旧严格 0/21 保持 |

本轮成绩不是完整 21 类通过率，也不是新的诊断准确率测试。REFUTED 通过的是有依据的诊断响应，不是异常定位，也不代表全部业务健康。CPU Profile 重叠比例不相加；HTTP 路径没有拆分服务处理与 TCP 传输；原 I/O 环境是 tmpfs，不能宣称真实磁盘等待已定位。

## 规则与扩展

独立因果对照、固定调查轮次、强制 COMPLETED 和同负载修复证明不再是默认前提。目标身份、Task/Attempt/Artifact/Analyzer 来源、下载 SHA、数值重新计算、对应信号域，以及实验撤销恢复、清理和收束仍必须满足。完整反证允许来自 INSUFFICIENT_EVIDENCE 会话；运行中、失败和取消仍不合格。

源合同是 `contracts/engineering_diagnosis.json`，通用评分器和生成器位于 `scripts/evaluate_engineering_diagnosis.py`、`scripts/build_engineering_diagnosis.py`。多个独立证据清单可以追加新批次，原清单不修改。评分器不写死三个 Go 案例；新增内存域复用测试验证已有数值域的扩展方式。新语义仍需补采集器/Analyzer 字段。

详细注册、采集、健康/反证用例、归档、生成和 CI 流程见 [工程诊断验收文档](../../docs/DIAGNOSIS_ACCEPTANCE.md)。生成物漂移检查已加入质量合同。

## 验证和线上版本

评分与展示源码提交 `0fa79f15f86e4fb0b6a8ab8a8776a63c02297ca9` 的 [CI 36861902293](https://github.com/llongwang751-arch/mini-drop/actions/runs/36861902293) 成功 13/13，测试 merge 为 `d44ec6de1951d45471ca129f0a90c2df964ca219`：Python 1382 通过 / 10 登记跳过、真实 PostgreSQL 8 通过零跳过、Web 299 通过。Chroma、Go race、真实镜像及连续 I/O 等原有专项也通过。

本轮另外从不可变 Git 对象导出 Web 源码，核对原始字节，独立安装依赖，再执行 299 项测试、构建与体积门禁，均通过。该包未激活：另一条前端任务已发布 `20261001T122233Z`，其中包含相同工程验收组件和语义一致的最终索引。因此直接验证现有发布，保留其界面改动。线上整体 Web 是独立冻结工作树构建，300 项测试通过，不能将本轮精确 Git CI 冒充整个线上 Web 的源码验证。

本轮只读复核线上 58 个 Web 文件 SHA、Worker/Analyzer 各 193 文件 SHA、API 二进制、Office 集成源码与进程。13 容器健康，API 三依赖健康，21 故障 inactive，Office PID1650962、NRestarts=0。Worker/Analyzer/Go 保持 `c59aac566cedef6a239eb1d640ee8fb796524ae3`，API 与 Office 不变。

真实 Chrome 再次验证新成绩 3/3、定位 2/3、反证 1、待验收 18，三张当前成绩卡及原诊断入口、CPU 按钮、HTTP/I/O 报告、正常检查、默认 21 项、4 项工程回归及 9 份下载 SHA。1440/1024/768/375px 无页面水平溢出，前进后退通过，无 JS/HTTP 错误。没有新建诊断、发压或注入故障；已接受的一小时不重跑。

## 原始证据

本轮测试、CI 原始 ZIP、冻结源码清单、线上源对照、发布回执副本、运行时及浏览器结果统一在 [归档 SHA 清单](../quality/engineering-diagnosis-20261001/manifest.json)，共 90 文件。原三案例和 559 文件清单仍使用此前独立归档，未替换。首次内存测试夹具形状错误、早期信号域映射不匹配及后续通过结果均保留；它们是开发过程记录，不宣称为旧生产代码的失败复现。

- [当前工程成绩实拍](../quality/engineering-diagnosis-20261001/deployed/browser/current-engineering-acceptance.png)
- [真实浏览器结果](../quality/engineering-diagnosis-20261001/deployed/browser/result.json)
- [运行时复核](../quality/engineering-diagnosis-20261001/deployed/final-runtime-verification.json)
- [线上源与精确提交对照](../quality/engineering-diagnosis-20261001/deployed/live-source-comparison.json)
- [发布范围说明](../quality/engineering-diagnosis-20261001/deployed/release-review.json)

剩余 18 类需要逐域补独立真实采集，并按同一工程标准验收。真实磁盘等待、HTTP/TCP 拆分，以及同输入、同负载的原因干预仍是额外能力，不能靠改分数替代。
