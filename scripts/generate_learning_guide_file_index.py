"""Refresh the file-level appendix in docs/PROJECT_LEARNING_GUIDE.md.

The learning guide is the canonical teaching document.  This generator keeps
its file dictionary aligned with files that actually exist in the working
tree, while descriptions stay deterministic and intentionally concise.
"""

from __future__ import annotations

import subprocess
import ast
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "PROJECT_LEARNING_GUIDE.md"
START = "<!-- FILE_INDEX:START -->"
END = "<!-- FILE_INDEX:END -->"

GROUP_TITLES = {
    "(root)": "根目录：工程入口与组合配置",
    "web": "web：React 页面与交互",
    "apiserver": "apiserver：Go HTTP/SSE 入口",
    "server": "server：Python 诊断、编排与持久化",
    "analyzer": "analyzer：采集物分析",
    "native": "native：C++ Control、Agent 与 Collector",
    "proto": "proto：跨语言协议源文件",
    "contracts": "contracts：共享稳定合同",
    "demo": "demo：四种运行时的受控故障实验室",
    "integrations": "integrations：真实业务源码接入与独立验收适配器",
    "skills": "skills：可复用诊断路线",
    "knowledge": "knowledge：Agentic RAG 知识先验",
    "benchmarks": "benchmarks：公开题目与私有真值",
    "tests": "tests：Python 自动化验证",
    "scripts": "scripts：生成、检查和云端验收入口",
    "deploy": "deploy：镜像、环境、证书与编排",
    "docs": "docs：权威设计与接口文档",
    "reports": "reports：已经运行后产生的证据报告",
    "output": "output：交付型派生材料",
    ".github": ".github：持续集成",
}

EXACT = {
    "docs/TEST_ENGINEERING.md": "测试开发定位、风险回归、质量报告、并发 CI、缺陷复盘与开源机制对照。",
    "contracts/quality_plan.json": "版本化风险与执行套件映射，定义本地/CI 质量配置及允许跳过边界。",
    "scripts/run_quality_gate.py": "一键风险回归，严格判定测试结果并保留 HTML/JSON/JUnit/日志与源码摘要。",
    "tests/test_quality_gate.py": "防止空报告、跳过、失败重试覆盖、超时和不完整报告导致质量门禁假绿。",
    "docs/assets/architecture.svg": "README 静态架构图的可编辑源文件，展示平台、采集 Agent、业务进程与存储职责。",
    "docs/BUSINESS_ONBOARDING_DESIGN.md": "业务接入设计与首批轻量业务进展，区分已实现请求关联与待实现阶段观测。",
    "integrations/agi_saber/service.py": "独立测试库调用实际 AGI-saber 检索引擎，提供本机 HTTP 与有界脱敏请求观测。",
    "scripts/run_actual_rag_acceptance.py": "冻结原 RAG 修复前后源码，按相同语料和流量执行真实 HTTP 三窗对照。",
    "scripts/render_actual_rag_acceptance.py": "合并实际业务测量与独立 AI 诊断结果生成可追溯复盘文档。",
    "docs/BUSINESS_ACCEPTANCE.md": "业务测量、同负载修复比较、知识库样例、CI 计划及本地/AI 验收边界。",
    "server/app/drop_insight/business_acceptance.py": "业务测量源合同与可比性、延迟、成功率、质量、降级门禁。",
    "server/app/drop_insight/business_showcase.py": "读取服务端固定且哈希校验的业务结果，仅提供只读展示。",
    "scripts/run_business_acceptance.py": "真实本地 HTTP 查询、并发导入和三个测量窗口的可重复业务验收。",
    "scripts/check_business_test_plan.py": "校验需求与执行案例一致、引用有效，并生成保守的变更影响清单。",
    "scripts/generate_business_contracts.py": "从 Pydantic 源合同生成业务测量与验收策略 JSON Schema。",
    "scripts/build_business_acceptance_view.py": "从完成且哈希一致的业务报告生成页面投影。",
    "scripts/render_business_acceptance.py": "从业务原始报告生成 Markdown 对比结果。",
    "scripts/run_fault_plaza_strict_acceptance.py": "21 场景真机严格验收：独立记录采集链路、根因门禁、注入指标、撤销恢复与清理，逐场保存原始证据。",
    "scripts/render_fault_plaza_acceptance.py": "从严格验收原始 JSON 生成逐场 Markdown 报告，保留失败与根因缺口。",
    "scripts/build_fault_plaza_acceptance_index.py": "校验完成的 Campaign 和逐场证据哈希，生成页面最近验收结果索引。",
    "server/app/drop_insight/fault_acceptance.py": "读取并校验只读挂载的验收索引，为故障广场提供真实最近验收结果。",
    "docs/FAULT_PLAZA_ACCEPTANCE.md": "严格验收协议、通过标准、历史链路边界、发布修复与页面截图。",
    "native/agent/include/self_metrics.h": "读取 Agent 自身 /proc 计数，按相邻时间窗计算 CPU、RSS 与磁盘读写速率；无有效窗口不填零。",
    "native/agent/tests/self_metrics_test.cpp": "验证自身指标的有效窗口、计数回退、缺失来源和真实 RSS。",
    "web/src/utils/agentMetrics.js": "格式化 Agent 自身指标与单位，区分未上报、非法值和实测零。",
    "web/src/utils/reportPresentation.js": "构造报告结论标题、证据边界和下一步，限制旧主机 I/O 观察被误读为进程根因。",
    "server/migrations/versions/20260910_0008_agent_latest_metrics.py": "为 Agent 增加可空 JSON 指标字段，保留旧行与缺失值语义，并兼容基线建表。",
    "tests/test_agent_metrics_migration.py": "验证指标迁移保留旧数据、默认缺失以及重复升级兼容性。",
    "scripts/render_learning_guide.py": "把唯一 Markdown 教材生成离线 HTML 阅读版，内嵌截图、目录搜索和图片放大，并校验链接。",
    "server/app/drop_insight/skill_experiments.py": "服务端粘性随机分流、人工/Oracle 标签、统计快照、护栏和发布建议。",
    "server/app/drop_insight/operator_memory.py": "按 principal 隔离的显式偏好读写与删除，拒绝目标或权限等越界记忆。",
    "web/src/components/SkillExperimentPanel.jsx": "随机实验创建、流量分配、标签、统计结果与人工批准入口。",
    "AGENTS.md": "仓库协作规则；规定重启后先读哪些权威文档以及禁止破坏的数据。",
    "README.md": "项目首页，给出能力概览、快速启动和权威文档入口。",
    "pyproject.toml": "Python 依赖、打包、pytest 与开发工具配置。",
    "alembic.ini": "Alembic 数据库迁移入口配置。",
    "Makefile": "开发、测试、生成合同和容器操作的快捷命令。",
    ".env.example": "环境变量示例；只说明字段，不保存真实密钥。",
    "docker-compose.yml": "本地开发用的组合服务定义。",
    "docker-compose.control.yml": "云端控制面、数据层、Web 和演示实验室的主编排。",
    "docker-compose.worker.yml": "独立采集 Worker 的容器编排。",
    ".gitignore": "Git 忽略规则。",
    ".dockerignore": "构建镜像时排除无关或敏感文件。",
    "web/src/main.jsx": "React 启动入口，挂载路由、主题和错误边界。",
    "web/src/router.jsx": "URL 到任务、Agent、诊断、计划任务和审计页面的映射。",
    "web/src/api/client.js": "浏览器唯一 API 客户端；处理会话、错误翻译和全部业务请求。",
    "web/src/theme.js": "前端颜色、间距、字号等设计 Token。",
    "web/src/pages/AIDiagnosis.jsx": "AI 诊断主工作台；组织案例、对话、树、人工干预和验证中心。",
    "web/src/pages/Dashboard.jsx": "任务面板；展示 Task、Agent、筛选、排序和新建采集。",
    "web/src/pages/TaskResult.jsx": "任务详情；区分采集/分析状态并展示 Artifact 与可视化。",
    "web/src/pages/AgentDetail.jsx": "Agent 心跳、能力、开销、趋势和历史任务详情。",
    "web/src/pages/Schedules.jsx": "计划任务的创建、触发、历史和删除页面。",
    "web/src/pages/AuditLogs.jsx": "审计事件查询页面。",
    "web/src/components/ActualExplorationTree.jsx": "真实 LATS 父子树、评分、剪枝、回溯、缩放与全屏交互。",
    "web/src/components/AgentCockpit.jsx": "阶段、计划、RAG、工具、Evidence、记忆、评测和 LATS 指标驾驶舱。",
    "web/src/components/ChatThread.jsx": "把持久化领域事件按轮次投影成多轮诊断对话。",
    "web/src/components/DiagnosisFinding.jsx": "从本会话既有报告提取首屏摘要，保留证据引用、限制与下一步。",
    "scripts/verify_frontend_workbench.mjs": "使用本地合成 API 与 Chromium 验证首屏、响应式、树和失败刷新；不连接云端。",
    "scripts/verify_final_ui_acceptance.mjs": "通过公网只读验证首屏、移动布局、已有报告、探索树、验证中心和记忆，阻止业务写请求。",
    "deploy/dockerfiles/web-prebuilt.Dockerfile": "将已测试的 web/dist 叠加到指定 Web 运行镜像，保留旧哈希资产供已打开页面继续加载。",
    "web/src/components/FaultPlazaPanel.jsx": "故障广场；管理 21 个白名单场景、状态、时长和启动/停止。",
    "web/src/components/SkillABPanel.jsx": "创建并比较 Skill AUTO/DISABLED 两臂，恢复浏览器 A/B 历史。",
    "web/src/components/SkillEvolutionPanel.jsx": "查看 Skill 候选、评测、发布、隔离、回滚和沉淀。",
    "web/src/components/TaskCreatePanel.jsx": "基础采集表单，根据 Agent 能力生成采集参数。",
    "web/src/components/FlamegraphViewer.jsx": "交互式火焰图。",
    "web/src/components/TopNChart.jsx": "热点函数 TopN 图。",
    "web/src/components/CallGraphViewer.jsx": "Caller/Callee 调用关系图。",
    "web/src/components/EBPFHistogram.jsx": "eBPF I/O 延迟直方图。",
    "web/src/components/EvidenceCard.jsx": "Evidence 来源、角色、质量和引用展示。",
    "web/src/components/ToolCallCard.jsx": "工具计划、参数、门禁和人工批准/拒绝交互。",
    "web/src/components/ScopeCard.jsx": "目标发现、服务/环境/安全 binding 和时间窗确认。",
    "web/src/components/ConclusionCard.jsx": "根因、置信度、门禁状态、限制和建议展示。",
    "web/src/components/EvalPanel.jsx": "评测总览、故障广场、A/B 与 Skill 示例入口。",
    "web/src/components/LatsReplayPanel.jsx": "创建并查看 FULL_LATS 冻结回放。",
    "web/src/components/AppLayout.jsx": "全局导航、页面标题、访问凭据和 SSE 状态。",
    "apiserver/cmd/apiserver/main.go": "Go API 进程入口，装配配置、数据库、Control、诊断服务和路由。",
    "apiserver/internal/httpapi/server.go": "公开 HTTP/SSE 路由、鉴权、代理、幂等与响应合同。",
    "apiserver/internal/repository/postgres.go": "Go 控制面使用的 PostgreSQL 查询与事务实现。",
    "apiserver/internal/objectstore/minio.go": "MinIO 对象索引、下载和短时上传授权。",
    "apiserver/internal/scheduler/runner.go": "计划任务扫描、抢占与 Task 创建循环。",
    "apiserver/internal/cron/cron.go": "五字段 Cron 解析和下一次触发时间计算。",
    "apiserver/internal/taskkind/catalog.go": "由合同生成的采集器目录与参数规则。",
    "apiserver/internal/taskkind/validation.go": "TaskKind 参数校验。",
    "server/app/diagnosis_worker.py": "后台推进待处理诊断和工具完成后的下一轮。",
    "server/app/diagnostic_ai_rpc.py": "Go API 到 Python 诊断领域的内部 gRPC 适配层。",
    "server/app/analyzer_runner.py": "统一调用 perf、py-spy、pprof、async-profiler 等解析器并执行质量门禁。",
    "server/app/analysis_jobs.py": "持久化 AnalysisJob 的领取、运行、重试和终态编排。",
    "server/app/models.py": "SQLAlchemy 领域表模型。",
    "server/app/sql_repository.py": "组合各领域 Repository mixin 的数据库入口。",
    "server/app/database.py": "数据库 Engine、Session 和 schema 版本检查。",
    "server/app/storage.py": "MinIO 客户端、对象上传下载和预签名地址。",
    "server/app/process_attestation.py": "进程快照规范化与不可变 Agent/PID/启动时间 binding。",
    "server/app/task_attempt_authority.py": "Task Attempt 执行权、租约和回报授权。",
    "server/app/outbox_dispatcher.py": "事务 Outbox 的带租约派发器。",
    "server/app/drop_insight/service.py": "AI 诊断领域总编排：范围、轮次、工具、证据、报告、树和干预。",
    "server/app/drop_insight/diagnosis_agent.py": "LangChain create_agent 与 LangGraph Checkpoint 适配。",
    "server/app/drop_insight/adaptive_planner.py": "模型辅助假设规划和中文确定性兜底。",
    "server/app/drop_insight/lats.py": "UCT/PUCT、Selection、Expansion、Simulation、Reflection 和价值回传原语。",
    "server/app/drop_insight/exploration_tree.py": "从领域记录重建可恢复的探索树快照。",
    "server/app/drop_insight/tools.py": "模型可见工具白名单及 Task 参数映射。",
    "server/app/drop_insight/policy.py": "风险、预算、能力、目标和审批门禁。",
    "server/app/drop_insight/artifact_evidence.py": "把 Artifact 质量保守转换为 Evidence 分类。",
    "server/app/drop_insight/claim_verifier.py": "检查报告主张是否被当前 Evidence 引用和支持。",
    "server/app/drop_insight/skill_evolution.py": "Skill 混合检索、激活、跨轮沿用、候选演进和发布门禁。",
    "server/app/drop_insight/builtin_skills.py": "把仓库 Skill catalog 同步到运行时数据库。",
    "server/app/drop_insight/fault_plaza.py": "四运行时 21 个故障场景的服务端白名单。",
    "server/app/drop_insight/frozen_replay_showcase.py": "冻结 fixture 到持久化 FULL_LATS 会话的桥。",
    "server/app/drop_insight/root_cause_benchmark.py": "540 条根因回放与 500 组 Skill A/B 的评分实现。",
    "server/app/agent_runtime/harness.py": "模型输入输出授权边界；阻止模型伪造目标和工具。",
    "server/app/agent_runtime/context.py": "可信上下文组装、裁剪和确定性序列化。",
    "server/app/agent_runtime/memory.py": "短期 Checkpoint 与上下文窗口策略。",
    "server/app/agent_runtime/retrieval.py": "Knowledge 目录的本地 BM25/词法混合检索。",
    "server/app/agent_runtime/themes.py": "版本化诊断行为主题和系统提示。",
    "server/app/agent_runtime/runtime.py": "框架、模型和 Checkpoint 后端身份描述。",
    "native/control/src/main.cpp": "C++ Control gRPC 服务；处理 Agent 注册、任务租约、状态和上传授权。",
    "native/agent/src/main.cpp": "C++ Agent 主循环；注册、心跳、领取任务并调度 Collector。",
    "native/agent/src/language_collectors.cpp": "py-spy、Go pprof、async-profiler 等运行时采集器。",
    "native/agent/src/perf_collector.cpp": "perf 与持续 perf 采集。",
    "native/agent/src/ebpf_io_collector.cpp": "白名单 eBPF/bpftrace I/O 延迟采集。",
    "native/agent/src/proc_collectors.cpp": "procfs 系统指标和 smaps 内存采集。",
    "native/agent/src/process_snapshot.cpp": "发现进程并上报 PID、启动时间、命名空间和运行时。",
    "native/agent/src/artifact_uploader.cpp": "使用短时授权上传 Artifact 与 manifest。",
    "native/agent/src/result_outbox.cpp": "Agent 本地结果 Outbox，网络恢复后重放。",
    "analyzer/mini_drop_analyzer/hotmethod_analyzer.py": "Analyzer 命令入口和统一热点产物生成。",
    "analyzer/mini_drop_analyzer/pprof_analyzer.py": "解析 Go pprof protobuf。",
    "analyzer/mini_drop_analyzer/pyspy_analyzer.py": "解析 py-spy speedscope。",
    "scripts/generate_learning_guide_file_index.py": "生成本节文件字典；修改仓库结构后重新运行。",
    "scripts/capture_learning_guide_screenshots.py": "通过临时无头浏览器抓取当前云端只读页面，生成总教材使用的可复现截图。",
    "scripts/generate_root_cause_benchmark.py": "从故障合同生成 540 条公开 Case 与私有 Oracle。",
    "scripts/run_root_cause_benchmark.py": "运行根因 Top-1 与 500 组 Skill A/B 并输出报告。",
    "scripts/generate_diagnosis_benchmark_v2.py": "生成 540 条 Skill 检索与拒绝测试集。",
    "scripts/run_diagnosis_benchmark_v2.py": "运行生产 Skill 选择器 Benchmark。",
    "scripts/verify_interview_demo.py": "用页面同款 API 验收真实故障、A/B、Artifact、Evidence、报告和清理。",
    "scripts/verify_lats_replay_showcase.py": "验收冻结 FULL_LATS 双会话、重置证明和命名空间隔离。",
    "scripts/verify_priority_collectors.py": "验收持续 perf 与独立 eBPF Campaign。",
    "docs/PROJECT_CONTEXT.md": "跨会话架构和当前事实总锚点。",
    "docs/RESTART_HANDOFF.md": "电脑或会话重启后的精确恢复入口。",
    "docs/PROJECT_LEARNING_GUIDE.md": "当前这份从页面到源码、测试和面试的唯一总教材。",
    "docs/INTERVIEW_DEMO_GUIDE.md": "面试现场逐步点击、讲解、预期结果和排障脚本。",
    "docs/AI_DIAGNOSIS.md": "AI 范围、规划、Evidence Gate、LATS 和页面语义。",
    "docs/AGENT_RUNTIME.md": "Runtime、Harness、Theme、上下文、记忆和框架边界。",
    "docs/SKILLS.md": "Skill 格式、检索、渐进披露、评测、发布与回滚。",
    "docs/REPLICATION.md": "本地、云端和多 Worker 的复刻部署。",
    "docs/assets/learning-guide/01-ai-diagnosis-workbench.png": "总教材图 1：AI 诊断空白工作台和全局导航。",
    "docs/assets/learning-guide/02-diagnosis-case-drawer.png": "总教材图 2：诊断历史抽屉、筛选和新建入口。",
    "docs/assets/learning-guide/02b-new-diagnosis-composer.png": "总教材图 3：新诊断自然语言输入框和开始按钮。",
    "docs/assets/learning-guide/03-selected-diagnosis.png": "总教材图 4：四轮 Java 会话、阶段条和 Agent 驾驶舱。",
    "docs/assets/learning-guide/03b-cockpit-metric-dialog.png": "总教材图 5：逐轮规划假设指标弹窗。",
    "docs/assets/learning-guide/04-dynamic-exploration-tree.png": "总教材图 6：实时 LATS 动态探索树、预算和路径控制。",
    "docs/assets/learning-guide/05-evaluation-overview.png": "总教材图 7：诊断验证中心四个入口。",
    "docs/assets/learning-guide/06-fault-plaza.png": "总教材图 8：21 个受控故障的运行时筛选和状态区。",
    "docs/assets/learning-guide/07-skill-ab.png": "总教材图 9：540/500 量化指标与同题 Skill A/B 输入区。",
    "docs/assets/learning-guide/08-skill-plaza.png": "总教材图 10：Skill 指标、生命周期和参考路线。",
    "docs/assets/learning-guide/09-task-dashboard.png": "总教材图 11：基础采集表单、运行指标和任务列表。",
    "docs/assets/learning-guide/10-task-result.png": "总教材图 12：Java 任务状态、Artifact 和火焰图结果。",
    "docs/assets/learning-guide/11-schedules.png": "总教材图 13：计划任务列表和新建入口。",
    "docs/assets/learning-guide/11b-schedule-form.png": "总教材图 14：Cron 调度规则与任务模板表单。",
    "docs/assets/learning-guide/12-audit-log.png": "总教材图 15：审计搜索、事件筛选和记录列表。",
    "docs/COMPETITOR_DESIGN_DECISIONS.md": "开源/商业竞品机制到本项目设计决策的证据链。",
}

TOPICS = {
    "artifact": "采集产物、完整性和生命周期",
    "analysis": "分析任务与质量状态",
    "diagnosis": "AI 诊断状态与流程",
    "skill": "Skill 检索、策略与演进",
    "lats": "LATS 搜索与回放",
    "fault": "故障广场及受控故障",
    "evidence": "Evidence 分类与门禁",
    "scope": "目标发现与安全范围",
    "memory": "上下文与记忆",
    "runtime": "Agent Runtime",
    "database": "数据库与迁移",
    "storage": "对象存储",
    "outbox": "Outbox 可靠投递",
    "state": "状态机",
    "contract": "跨语言合同",
    "collector": "采集器",
    "profile": "性能 Profile",
    "agent": "Agent 注册、状态或能力",
    "schedule": "计划任务",
    "security": "权限与安全边界",
    "intervention": "人工干预",
    "benchmark": "评测数据与指标",
    "source": "源码定位",
    "logging": "日志和 Trace",
    "kernel": "Linux 内核兼容性",
}


def topic_for(path: str) -> str:
    lower = path.lower()
    for token, label in TOPICS.items():
        if token in lower:
            return label
    return "对应模块行为"


def describe(path: str) -> str:
    if path in EXACT:
        return EXACT[path]
    name = Path(path).name
    lower = path.lower()
    if name == "__init__.py":
        return "Python 包入口；声明包边界并按需导出公共对象，不是常驻服务启动器。"
    if name.lower().startswith("readme"):
        return "当前目录的用途、运行方式、依赖与边界说明。"
    if name in ("go.mod", "go.sum"):
        return "Go 模块依赖与版本声明。" if name == "go.mod" else "Go 依赖内容校验记录，随依赖工具更新。"
    if name in ("package.json", "package-lock.json"):
        return "Node 工程依赖和 build/test 等脚本入口。" if name == "package.json" else "npm 精确依赖版本与完整性锁文件，供 npm ci 重现。"
    if name == "CMakeLists.txt":
        return "CMake 原生构建目标、源文件、编译选项和链接依赖。"
    if name.endswith((".png", ".jpg", ".svg")):
        return "可视化/截图静态资源；结合引用位置与拍摄日期解释，不是可执行业务代码。"
    if lower.startswith("docs/assets/") and name.endswith(".json"):
        return "截图取证元数据：页面文字、控件、路径、时间、图片 hash 或批次清单。"
    if lower.startswith("web/public/report-assets/"):
        return "随页面发布的评测/演示静态材料；不能当成当前会话的现场 Evidence。"
    if lower.startswith(".github/workflows/"):
        return "CI 工作流：声明触发条件、运行环境、测试和构建检查步骤。"
    if "/generated/" in lower or name.endswith((".pb.go", "_pb2.py", "_pb2_grpc.py")):
        return "由协议或 JSON 合同自动生成的代码；应修改源合同后重新生成，不要手改。"
    if "/migrations/versions/" in lower:
        return f"Alembic 迁移：{Path(path).stem.replace('_', ' ')}。"
    if name.endswith((".test.jsx", ".test.js")):
        return f"前端自动化测试，验证同名模块的{topic_for(path)}。"
    if lower.startswith("tests/test_"):
        return f"Python 自动化测试，验证{topic_for(path)}的成功、失败与边界条件。"
    if name.endswith("_test.go"):
        return f"Go 单元测试，验证{topic_for(path)}。"
    if name.endswith("_test.cpp"):
        return f"C++ 单元测试，验证{topic_for(path)}。"
    if name.endswith((".css", ".module.css")):
        return "同名页面或组件的布局、响应式和视觉样式。"
    if name == "SKILL.md":
        return f"{Path(path).parent.name} 的可审计诊断路线正文。"
    if lower.startswith("knowledge/") and name.endswith(".md"):
        return f"Agentic RAG 知识条目：{Path(path).stem.replace('_', ' ')}；只作先验，不作 Evidence。"
    if lower.startswith("benchmarks/"):
        if name == "cases.json":
            return "公开评测输入，不包含根因答案。"
        if name == "oracles.json":
            return "私有根因/Skill 真值，评测时才与公开输入合并。"
        if name == "manifest.json":
            return "数据集数量、版本、随机种子和文件 SHA-256 清单。"
        return "评测来源或数据合同。"
    if lower.startswith("reports/"):
        return "已运行后生成的验收/评测产物；结合时间、ID 链和 SHA-256 使用。"
    if lower.startswith("deploy/k8s/") and name.endswith((".yaml", ".yml")):
        return f"Kubernetes {Path(path).stem} 资源定义。"
    if name.endswith("Dockerfile") or name.endswith(".Dockerfile"):
        return "对应服务的可复现容器镜像构建配方。"
    if lower.startswith("deploy/env/"):
        return "部署环境变量模板或面试实验室配置；真实密钥不得写入教材。"
    if lower.startswith("deploy/scripts/"):
        return f"部署辅助脚本，负责{topic_for(path)}或服务初始化。"
    if lower.startswith("scripts/"):
        return f"工程脚本，负责{topic_for(path)}的生成、检查或验收。"
    if lower.startswith("proto/") and name.endswith(".proto"):
        return f"{Path(path).stem} gRPC/消息协议源文件。"
    if lower.startswith("contracts/") or lower.startswith("docs/contracts/"):
        return f"{topic_for(path)}的机器可读或人类可读稳定合同。"
    if lower.startswith("demo/"):
        return f"{Path(path).parts[1]} 受控故障实验室的源码、依赖或镜像构建文件。"
    if lower.startswith("web/src/"):
        return f"React 前端模块，负责{topic_for(path)}的展示或交互。"
    if lower.startswith("apiserver/"):
        return f"Go API 模块，负责{topic_for(path)}。"
    if lower.startswith("server/"):
        return f"Python 服务模块，负责{topic_for(path)}。"
    if lower.startswith("native/"):
        return f"原生 C++ 模块，负责{topic_for(path)}。"
    if lower.startswith("analyzer/"):
        return f"Analyzer 文件，负责{topic_for(path)}或第三方格式兼容。"
    if lower.startswith("docs/"):
        return "项目设计、使用、部署、接口或验收说明。"
    return "项目配置、源码或派生材料；从所在目录和引用关系理解其职责。"


def current_files() -> list[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    paths = []
    for raw in result.stdout.splitlines():
        path = raw.replace("\\", "/").strip()
        if path.startswith(("output/learning-guide/", "output/acceptance/")):
            continue
        if path and (ROOT / path).is_file():
            paths.append(path)
    return sorted(set(paths), key=lambda value: value.casefold())


DIRECTORIES = {
    "web/src/pages": "路由页面：组织数据加载、表单和页面级状态。",
    "web/src/components": "可复用或领域专用组件：对话、树、图表、工具与证据卡。",
    "web/src/api": "统一 HTTP 客户端、鉴权和业务请求封装。",
    "web/src/hooks": "轮询与 SSE 等跨页面状态逻辑。",
    "web/src/utils": "状态/证据展示、格式兼容和数据转换纯函数。",
    "web/src/generated": "由源合同生成的前端枚举；不要手改。",
    "web/src/lib": "第三方库的项目内统一入口。",
    "web/public": "随 Web 发布的公开静态资源；不能放密钥或私有真值。",
    "web": "React 前端工程，含依赖、构建配置、页面与测试。",
    "apiserver/cmd": "Go 服务启动程序与命令入口。",
    "apiserver/internal/httpapi": "HTTP 路由、鉴权、输入校验与内部调用。",
    "apiserver/internal/repository": "PostgreSQL 查询、写入和事务边界。",
    "apiserver/internal/scheduler": "定时扫描到期计划并触发任务。",
    "apiserver/internal/cron": "Cron 表达式解析及下一次触发计算。",
    "apiserver/internal/objectstore": "MinIO 对象元数据与短时访问授权。",
    "apiserver/internal/config": "环境配置解析、默认值和启动校验。",
    "apiserver/internal/gen": "Protobuf/gRPC 生成代码；源头在 proto。",
    "apiserver": "Go 公开 API 模块，独立 go.mod 与测试。",
    "server/app/drop_insight": "AI 诊断领域：目标、假设、工具、Evidence、Report、LATS 与 Skill。",
    "server/app/agent_runtime": "框架适配、上下文、Checkpoint、Harness 与知识检索。",
    "server/app/generated": "Python 协议生成物。",
    "server/app": "Python 服务基础能力、数据模型、Worker 和分析调度。",
    "server/migrations/versions": "按 revision 排序的数据库 schema 迁移。",
    "server/migrations": "Alembic 迁移环境、模板和版本链。",
    "server": "Python 服务包。",
    "native/agent/src": "采集 Agent 主循环、进程快照、采集器、上传与本地结果 Outbox。",
    "native/control/src": "C++ Control 调度、注册、租约和结果回报实现。",
    "native/agent": "原生采集节点程序及其构建配置。",
    "native/control": "原生控制面程序及其构建配置。",
    "native/gperftools_bridge": "gperftools 兼容桥与原生产物接入。",
    "native": "原生 C++ 工程及公共工具。",
    "analyzer/mini_drop_analyzer": "各类 Profile、指标和内存产物的解析与统一输出。",
    "analyzer": "独立分析包、格式处理与测试材料。",
    "proto": "跨语言消息和 gRPC 协议源头及生成物。",
    "contracts": "机器可读的共享枚举、参数、状态和产物合同。",
    "demo": "隔离的受控故障实验室；按运行时设置固定动作与自动停止。",
    "skills": "可复用调查路线正文和轻量 catalog，不保存当前事故 Evidence。",
    "knowledge": "可检索的诊断知识文档与来源目录。",
    "benchmarks": "评测输入、真值与生成合同；不得将私有答案喂给被测规划器。",
    "tests": "Python 行为、合同、可靠性与失败边界验证。",
    "scripts": "可重复运行的生成、检验、截图、评测与运维辅助入口。",
    "deploy/dockerfiles": "各服务容器镜像的构建配方。",
    "deploy/env": "部署变量模板与受控实验配置；真实密钥文件不进教材。",
    "deploy/nginx": "HTTPS、代理、静态资源、SSE 与请求限制配置。",
    "deploy/systemd": "宿主机 Agent 等服务的生命周期管理。",
    "deploy/k8s": "Kubernetes 部署材料，不代表当前云端已用 Kubernetes。",
    "deploy/scripts": "证书、初始化与配置辅助脚本。",
    "deploy": "部署、镜像、网络与环境配置。",
    "docs/assets/learning-guide/20260909": "本次真实云端截图、可见文字/控件清单与图片 hash。",
    "docs/assets/learning-guide": "保留拍摄时间的教材截图与历史证据。",
    "docs/contracts": "公开 API、事件与跨服务语义文档。",
    "docs": "当前权威文档、教程、接口与复盘。",
    "reports": "已执行后产生的历史验收证据；日期与场景边界不可抹除。",
    "output": "交付或审阅材料；不作为业务源码和数据库事实。",
    ".github/workflows": "持续集成工作流与自动门禁。",
    ".github": "仓库平台协作和 CI 配置。",
}


def directory_description(path: str) -> str:
    if path in DIRECTORIES:
        return DIRECTORIES[path]
    parent = next((p for p in sorted(DIRECTORIES, key=len, reverse=True) if path.startswith(p + "/")), None)
    label = Path(path).name
    return f"{label} 子目录；" + (DIRECTORIES[parent] if parent else "归属该顶层模块，按下方文件职责定位。")


def symbols(path: str) -> str:
    file = ROOT / path
    if file.suffix not in (".py", ".jsx", ".js", ".go", ".proto") or "/generated/" in path or "/gen/" in path or "_pb2" in path or ".pb.go" in path:
        return "—"
    if file.stat().st_size > 2_000_000:
        return "—"
    text = file.read_text(encoding="utf-8", errors="replace")
    if file.suffix == ".py":
        try:
            tree = ast.parse(text)
            names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        except SyntaxError:
            names = []
    elif file.suffix == ".go":
        names = re.findall(r"^func\s+(?:\([^\n]*?\)\s+)?(\w+)\s*\(", text, re.M)
    elif file.suffix == ".proto":
        names = re.findall(r"^\s*(?:service|message|enum)\s+(\w+)", text, re.M)
    else:
        names = re.findall(r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)", text, re.M)
        names += re.findall(r"^export\s+const\s+(\w+)", text, re.M)
    names = list(dict.fromkeys(names))
    public = [n for n in names if not n.startswith('_')]
    selected = (public or names)[:5]
    return "、".join(f"`{n}`" for n in selected) + (f" 等 {len(names)} 个声明" if len(names) > 5 else "") if selected else "—"


def render(paths: list[str]) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        grouped[path.split("/", 1)[0] if "/" in path else "(root)"].append(path)
    order = [name for name in GROUP_TITLES if name in grouped]
    order.extend(sorted(set(grouped) - set(order)))
    lines = [
        START,
        "",
        "## 34. 当前仓库逐文件字典（自动生成）",
        "",
        f"本节由 `scripts/generate_learning_guide_file_index.py` 从 Git 已登记文件和未被忽略的新增文件生成，共登记 **{len(paths)} 个实际存在的文件**。`node_modules/`、`.git/`、缓存、密钥、数据库卷、MinIO 对象、临时发布包和本教材导出副本不列入；它不是递归泄露本机所有文件的清单。生成物、测试、报告和样式仍逐项说明，同类职责使用统一口径。源码定位列自动提取部分真实声明，不等于调用链，也不代表每个函数都在运行时被调用。",
        "",
        "阅读原则：先看第 19 节的数据链和第 21 节的核心路线，再到本节查文件；不要按数百个文件从头顺序读。修改协议生成物时回到 `proto/` 或 `contracts/`，修改 Benchmark 数据时回到生成器，修改报告时重新运行验收，不能直接编造结果。",
        "",
    ]
    directories = sorted({str(parent).replace('\\', '/') for path in paths for parent in Path(path).parents if str(parent) != '.'}, key=str.casefold)
    lines.extend(["### 34.0 每个目录负责什么", "", f"共 {len(directories)} 个包含上述文件的目录；更深的目录继承模块职责，并结合后面的逐文件说明阅读。", "", "| 目录 | 职责 |", "|---|---|"])
    lines.extend(f"| `{p}/` | {directory_description(p)} |" for p in directories)
    lines.append("")
    for group in order:
        lines.extend(
            [
                f"### 34.{order.index(group) + 1} {GROUP_TITLES.get(group, group)}",
                "",
                "| 文件 | 用途 | 源码定位（部分声明） |",
                "|---|---|---|",
            ]
        )
        for path in grouped[group]:
            lines.append(f"| `{path}` | {describe(path)} | {symbols(path)} |")
        lines.append("")
    lines.extend([END, ""])
    return "\n".join(lines)


def main() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    block = render(current_files())
    if START in text and END in text:
        before, rest = text.split(START, 1)
        _, after = rest.split(END, 1)
        updated = before.rstrip() + "\n\n" + block + after.lstrip("\r\n")
    else:
        updated = text.rstrip() + "\n\n" + block
    GUIDE.write_text(updated, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
