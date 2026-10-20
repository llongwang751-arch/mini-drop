# 当前项目上下文

当前工程是 tx-drop，按用户明确授权替换 GitHub `mini-drop` 仓库的过时项目文件。保留既有 Git 历史，不使用强制推送，不将旧 LangGraph 项目的目录、说明或验收结论混入本实现。

运行链路是 React → Go HTTP 编排 → C++ gRPC Server/Agent → 私有对象存储 → Python Analyzer → 类型化结果与可视化。PostgreSQL 保存双状态机、attempt/lease、事件、权限及调查记录。契约以根目录 `AGENTS.md`、`contracts/drop.proto`、`contracts/task-kinds.json`、SQL migrations 和 `docs/CONTRACT.md` 为准。

智能诊断使用官方 AgentScope 2.0.10 和 DeepSeek。Go 校验身份、权限、目标、工具参数、证据、预算和最终结果；独立 Python runtime 通过私有 Unix socket 执行有界 ReAct。自然语言计划经过确认后采集，诊断提出可核验假设并复测，再次诊断；源码定位核对文件 SHA，补丁只在登记源码的无网络隔离副本运行。代码执行器按固定镜像和不可变容器 ID 执行，可取消并清理自己的任务。禁止把模型输出直接变成任意宿主 shell 命令。

当前生产目标是用户确认的 `82.157.81.79`，入口为 [HTTPS 工作空间](https://82.157.81.79)。版本为 API12 / runtime2 / Web10 / AgentHelper3 / SQL8；27 个运行容器。真实 Saber FTS5 组件闭环见[发布收据](releases/20261020-agentscope.md)，原始 CRM/Saber 项目不随本仓库发布，也未被自动修改或发布。

后续变更先阅读 [交付矩阵](DELIVERY_MATRIX.md)、[验收边界](CAPABILITY_GAPS.md)、[本地开发](LOCAL_DEVELOPMENT.md) 和 [运行手册](RUNBOOK.md)。生产保留数据卷、命名镜像、历史失败证据及备份；不得执行 `compose down -v`。企业登录和异地备份展示按用户要求移除。
