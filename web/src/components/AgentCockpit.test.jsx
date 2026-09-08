import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentCockpit from "./AgentCockpit";

const detail = {
  diagnosis_id: "diag-agent-1",
  query: "定位订单服务 CPU 热点",
  status: "COLLECTING",
  classification: { category: "CPU_HOTSPOT" },
  target: { service: "order-service", environment: "demo" },
  agent_runtime: {
    framework: "langchain-create-agent/langgraph",
    version: "diagnosis-agent-v3",
    checkpoint_backend: "postgres",
    thread_id: "diag-agent-1",
  },
};

const resources = {
  hypotheses: [{
    hypothesis_id: "hyp-1",
    statement: "用户态热点函数占用 CPU",
    status: "OPEN",
    source: "LANGGRAPH_AGENT",
    round_index: 2,
  }],
  toolCalls: [
    { tool_call_id: "tool-1", tool_name: "start_pyspy_profile", status: "DONE", policy_decision: "ALLOW", task_id: "task-1" },
    { tool_call_id: "tool-2", tool_name: "collect_sys_metrics", status: "FAILED", policy_decision: "ALLOW", task_id: "task-2" },
  ],
  evidence: [{
    evidence_id: "evidence-1",
    role: "SUPPORTS",
    classification: { decision: "USABLE", can_support_conclusion: true },
    envelope: { source: { task_id: "task-1", tool_call_id: "tool-1" } },
  }],
  interventions: [{
    intervention_id: "intervention-1",
    action: "CHALLENGE_HYPOTHESIS",
    message: "先寻找 I/O 等待的反证",
    round_index: 2,
  }],
  skillActivations: [{
    activation_id: "activation-1",
    skill_id: "python-runtime-v1",
    match_score: 0.81,
    outcome: "COMPLETED",
  }],
  reports: [{ report_id: "report-1", version: 1 }],
  events: [
    { event_id: "event-1", event_type: "diagnosis.created", payload: { reason: "识别 CPU 诊断意图" } },
    { event_id: "event-2", event_type: "adaptive_probe_planned", payload: { probe_id: "pyspy", reason: "验证 Python 热点" } },
  ],
  explorationTree: {
    stats: { current_round: 2 },
    nodes: [
      { id: "diagnosis:diag-agent-1", kind: "diagnosis", title: "定位订单服务 CPU 热点" },
      { id: "hypothesis:hyp-1", kind: "hypothesis", title: "用户态热点函数占用 CPU" },
    ],
    search: {
      algorithm: "PUCT",
      algorithm_version: "lats-v1",
      execution_mode: "BUDGETED_LATS",
      environment_semantics: "LIVE_PROGRESSIVE",
      rollout_semantics: "REAL_TOOL_SINGLE_STEP_NO_ROLLBACK",
      phase: "REFLECTION",
      iteration: 3,
      selected_node_id: "hypothesis:hyp-1",
      best_path_node_ids: ["diagnosis:diag-agent-1", "hypothesis:hyp-1"],
      candidate_count: 4,
      latest_selection: {
        node_id: "hypothesis:hyp-1",
        score: 1.27,
        reason: "证据增益高且仍满足工具预算",
        components: { q: 0.72, exploration: 0.31, prior_bonus: 0.24, virtual_loss: 0 },
      },
      budget: {
        max_iterations: 8,
        used_iterations: 3,
        remaining_iterations: 5,
        max_tool_calls: 6,
        used_tool_calls: 2,
        remaining_tool_calls: 4,
        max_diagnosis_rounds: 5,
        used_diagnosis_rounds: 2,
        remaining_diagnosis_rounds: 3,
      },
      termination: { stopped: false, reason: "RUNNING", detail: "等待下一轮工具观察" },
    },
  },
  retrievals: [{
    event_id: "event-rag-1",
    round_index: 2,
    phase: "INTERVENTION_REPLAN",
    retrieval_trace: {
      query: "定位 Python CPU 热点",
      query_hash: "abcdef1234567890",
      retriever: "knowledge-hybrid-lexical-v1",
      catalog: "knowledge/catalog.json",
      matches: [{
        knowledge_id: "python-runtime",
        title: "Python 运行时诊断",
        document: "knowledge/python-runtime.md",
        content_hash: "1234567890abcdef",
        score: 2.345,
        matched_terms: ["python", "热点"],
        required_evidence: ["当前时间窗的 py-spy 调用栈"],
        caveats: ["旧案例不能作为本次证据"],
        excerpt: "先确认 CPU 异常与目标进程时间窗一致。",
      }],
      evidence_contract: { is_evidence: false },
    },
  }],
};

const sourceSkill = {
  skill_id: "python-runtime-v1",
  version: 1,
  status: "CANDIDATE",
  gate_metrics: {
    passed: 2,
    total: 3,
    eligible: false,
    evaluation_mode: "DETERMINISTIC_CONTRACT_GATE",
    negative_transfer_guard: true,
    environment_drift_guard: false,
  },
};

afterEach(cleanup);

describe("AgentCockpit", () => {
  it("shows eight real metric buttons, including LATS, and the persisted runtime identity", () => {
    render(<AgentCockpit detail={detail} resources={resources} sourceSkill={sourceSkill} connected />);

    expect(screen.getAllByRole("button", { name: /^查看/ })).toHaveLength(8);
    expect(screen.getByText("运行框架 langchain-create-agent/langgraph")).toBeInTheDocument();
    expect(screen.getByText("会话保存 PostgreSQL 持久化")).toBeInTheDocument();
    expect(screen.getByText("diag-agent-1")).toBeInTheDocument();
    expect(screen.getByText("事件流实时")).toBeInTheDocument();
    expect(screen.getByText("1/1")).toBeInTheDocument();
    expect(screen.getByText("反思重规划")).toBeInTheDocument();
  });

  it("shows live LATS phase, selection, best path, budget and termination without claiming rollback", () => {
    render(<AgentCockpit detail={detail} resources={resources} sourceSkill={sourceSkill} />);

    fireEvent.click(screen.getByRole("button", { name: "查看LATS 搜索" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("LATS 搜索、评估与价值回传")).toBeInTheDocument();
    expect(within(dialog).getByText("预算约束 LATS（实时环境不可回退）")).toBeInTheDocument();
    expect(within(dialog).getByText(/兄弟分支可能来自不同墙钟状态/)).toBeInTheDocument();
    expect(within(dialog).getAllByText("反思重规划").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("证据增益高且仍满足工具预算")).toBeInTheDocument();
    expect(within(dialog).getAllByText("用户态热点函数占用 CPU").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("已用 3/8 · 剩余 5")).toBeInTheDocument();
    expect(within(dialog).getByText("停止条件：仍在搜索")).toBeInTheDocument();
    expect(within(dialog).getByText("分支试探语义（rollout）")).toBeInTheDocument();
  });

  it("keeps old diagnoses readable when LATS metadata is absent", () => {
    render(
      <AgentCockpit
        detail={detail}
        resources={{ ...resources, explorationTree: { stats: { current_round: 2 } } }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看LATS 搜索" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("该案例未返回 LATS 搜索元数据")).toBeInTheDocument();
    expect(within(dialog).getByText(/不会把缺失的访问次数、价值或 UCT\/PUCT 分数补成 0/)).toBeInTheDocument();
    expect(within(dialog).queryByText("0.000")).not.toBeInTheDocument();
  });

  it("shows auditable RAG sources while keeping knowledge outside the Evidence layer", () => {
    render(<AgentCockpit detail={detail} resources={resources} sourceSkill={sourceSkill} />);

    fireEvent.click(screen.getByRole("button", { name: "查看主动知识检索" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("主动知识检索（Agentic RAG）只提供调查先验，不是当前故障证据")).toBeInTheDocument();
    expect(within(dialog).getByText("Python 运行时诊断")).toBeInTheDocument();
    expect(within(dialog).getByText("knowledge/python-runtime.md")).toBeInTheDocument();
    expect(within(dialog).getByText(/SHA-256 1234567890ab/)).toBeInTheDocument();
    expect(within(dialog).getByText("仍需现场证据：当前时间窗的 py-spy 调用栈")).toBeInTheDocument();
  });

  it.each([
    ["查看当前阶段 / 轮次", "阶段与意图", "定位订单服务 CPU 热点"],
    ["查看规划假设", "任务规划与动态改写", "用户态热点函数占用 CPU"],
    ["查看工具调用", "受控工具调用链", "采集 Python 调用栈"],
    ["查看证据", "可信证据事实层", "只有可信证据（Evidence）可以支撑事实结论"],
  ])("opens %s in an inspectable modal", (buttonName, modalTitle, expectedText) => {
    render(<AgentCockpit detail={detail} resources={resources} sourceSkill={sourceSkill} />);

    fireEvent.click(screen.getByRole("button", { name: buttonName }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(modalTitle)).toBeInTheDocument();
    expect(within(dialog).getByText(expectedText)).toBeInTheDocument();
  });

  it("separates session context and Skill route memory from factual Evidence", () => {
    render(<AgentCockpit detail={detail} resources={resources} sourceSkill={sourceSkill} />);

    fireEvent.click(screen.getByRole("button", { name: "查看记忆" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("会话记忆用于续写当前调查")).toBeInTheDocument();
    expect(within(dialog).getByText("先寻找 I/O 等待的反证")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: /长期路线记忆/ }));
    expect(within(dialog).getByText("长期记忆保存的是已发布 Skill 路线，不缓存旧根因")).toBeInTheDocument();
    expect(within(dialog).getByText("本轮匹配分 81%")).toBeInTheDocument();
  });

  it("renders evidence roles and gate states as Chinese labels", () => {
    render(<AgentCockpit detail={detail} resources={resources} sourceSkill={sourceSkill} />);

    fireEvent.click(screen.getByRole("button", { name: "查看证据" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("支持证据")).toBeInTheDocument();
    expect(within(dialog).getByText("可用于结论")).toBeInTheDocument();
    expect(within(dialog).queryByText("SUPPORTS")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("USABLE")).not.toBeInTheDocument();
  });

  it("shows derived evaluation rates and keeps Skill publication human-controlled", () => {
    const onOpenEvaluation = vi.fn();
    render(
      <AgentCockpit
        detail={detail}
        resources={resources}
        sourceSkill={sourceSkill}
        onOpenEvaluation={onOpenEvaluation}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "查看评测" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Agent 评测与 Skill 演进")).toBeInTheDocument();
    expect(within(dialog).getByText("1/2 次已结束调用成功")).toBeInTheDocument();
    expect(within(dialog).getByText("1/1 个成功工具产出可关联证据")).toBeInTheDocument();
    expect(within(dialog).getByText("自进化不等于自动修改生产")).toBeInTheDocument();
    expect(within(dialog).getByText("等待人工确认")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "打开完整评测中心" }));
    expect(onOpenEvaluation).toHaveBeenCalledOnce();
  });

  it("uses 暂无 instead of inventing rates when there are no terminal samples", () => {
    render(<AgentCockpit detail={{ ...detail, agent_runtime: undefined }} resources={{}} />);

    expect(screen.getByText("运行框架信息尚未随本次诊断返回")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看评测" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getAllByText("暂无").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("暂无已结束工具样本")).toBeInTheDocument();
  });

  it("shows the actual checkpoint backend when PostgreSQL has degraded to memory", () => {
    render(
      <AgentCockpit
        detail={detail}
        resources={{
          ...resources,
          runtimeStatus: {
            framework: "langchain-create-agent/langgraph",
            version: "diagnosis-agent-v3",
            requested_backend: "postgres",
            actual_backend: "memory",
            status: "DEGRADED",
            degraded: true,
            fallback_reason: "PostgreSQL checkpoint initialization failed; using process-local memory",
            persistence_guarantee: "process-local only",
            survives_process_restart: false,
          },
        }}
      />,
    );

    expect(screen.getByText("会话保存 内存临时保存")).toBeInTheDocument();
    expect(screen.getByText("降级运行")).toBeInTheDocument();
    expect(screen.queryByText("会话保存 PostgreSQL 持久化")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看当前阶段 / 轮次" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("请求 PostgreSQL 持久化")).toBeInTheDocument();
    expect(within(dialog).getByText("不支持")).toBeInTheDocument();
  });
});
