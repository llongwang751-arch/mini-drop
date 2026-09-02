# LangGraph Diagnosis Agent 实现说明

## 选型

当前诊断 Agent 采用：

- `langchain` 的 `create_agent` 作为模型与工具循环入口；
- LangGraph Runtime 作为状态、checkpoint 和执行底座；
- `langchain-deepseek` 的 `ChatDeepSeek` 连接 `deepseek-chat`；
- `langgraph-checkpoint-postgres` 保存线程短期记忆。

项目已经有 Go/C++ 异步执行面和严格证据门禁，框架只接管单 Agent 的有状态工具循环与可恢复线程。业务 Task 继续由 Mini-Drop 状态机管理，避免形成第二套调度状态机。

## 代码入口

- Agent Runtime：`server/app/drop_insight/diagnosis_agent.py`
- 框架切换与规则回退：`server/app/drop_insight/adaptive_planner.py`
- Skill 激活、策略门禁和 Task 创建：`server/app/drop_insight/service.py`
- Skill 检索与生命周期：`server/app/drop_insight/skill_evolution.py`
- 单元测试：`tests/test_diagnosis_agent.py`

## 三层记忆

### 当前回合

可信目标、规则计划、已有证据、人工纠错和 Skill 通过 `DiagnosisAgentContext` 注入。该对象不由用户自由填写。

### 线程记忆

LangGraph Checkpointer 按 `diagnosis_id` 保存消息和工具调用。配置：

```text
MINI_DROP_AGENT_FRAMEWORK=langgraph
MINI_DROP_AGENT_CHECKPOINT_BACKEND=postgres
MINI_DROP_AGENT_MEMORY_MAX_MESSAGES=24
```

### 长期策略记忆

长期记忆由版本化 Diagnostic Skill 提供。普通聊天内容不会自动写成 Skill；只有证据已验证、工具路线真实执行、离线门禁通过且人工发布的候选才能成为 ACTIVE。

## 权限边界

Agent 只拥有 `request_diagnostic_probe`。这个工具记录取证请求，不运行 Shell、不调用 Collector、不写 Evidence、不发布 Skill。

真正执行路径为：

```text
LangGraph ToolCall
  -> Pydantic + allowlist
  -> Mini-Drop policy/risk/budget gate
  -> human approval when required
  -> persisted Task and Attempt
  -> C++ Agent Collector
  -> Artifact and AnalysisJob
  -> Evidence gate
  -> hypothesis/report update
```

LangGraph 选择和解释下一步，C++ Agent 负责现场采集，Evidence Gate 决定材料能否用于结论。

## Skill 复用

Skill 检索发生在模型调用之前：

1. 只读取 ACTIVE 版本；
2. 硬过滤故障类别、环境冲突、服务范围和 Agent 能力；
3. 用结构化上下文、BM25 和确定性向量打分；
4. 分数不足或前两名歧义时拒绝激活；
5. 把通过的 Skill 路线、必需证据和停止条件注入 Agent；
6. Agent 在本轮 allowlist 内请求下一探针；
7. 新证据出现反证或环境漂移时退出 Skill。

这条设计让 Skill 影响路线，同时不能绕过计划器、策略门禁和证据门禁。

## 回退策略

- 模型不可用：使用确定性规则计划；
- 模型没有调用工具：使用确定性规则计划；
- 工具名越过 allowlist：拒绝并回退；
- 把采集错误写成反证：拒绝并回退；
- PostgreSQL checkpoint 不可用：当前进程降级到内存 checkpoint，并记录结构化错误；
- 环境冲突或 Skill 失效：退出 Skill，回到基线计划。

## 验证命令

```powershell
python -m pytest tests/test_diagnosis_agent.py -q
python scripts/run_skill_evolution_benchmark.py
docker compose build migrate
docker compose up -d --no-deps --force-recreate diagnosis-worker analyzer
```

完整容器验证还应从 Go API 创建诊断、运行 Planner、审批 ToolCall，并核对 Task、Evidence 和 Report。直接调用 Python Agent 只能算框架冒烟，不能代替平台端到端验证。
