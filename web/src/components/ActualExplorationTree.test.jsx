import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ActualExplorationTree from "./ActualExplorationTree";

describe("ActualExplorationTree", () => {
  it("shows the route actually explored, including pruning and direction changes", () => {
    render(
      <ActualExplorationTree
        hypotheses={[
          {
            hypothesis_id: "hyp-cpu",
            statement: "用户态热点函数占用 CPU",
            status: "RULED_OUT",
            round_index: 1,
            generation_reason: "火焰图没有形成热点",
          },
          {
            hypothesis_id: "hyp-io",
            statement: "磁盘写入阻塞导致延迟",
            status: "SUPPORTED",
            round_index: 2,
            generation_reason: "I/O 延迟与故障窗口一致",
          },
        ]}
        toolCalls={[
          {
            tool_call_id: "tool-perf",
            hypothesis_id: "hyp-cpu",
            tool_name: "start_perf_profile",
            status: "COMPLETED",
            created_at: "2026-08-25T10:00:00Z",
          },
          {
            tool_call_id: "tool-io",
            hypothesis_id: "hyp-io",
            tool_name: "start_ebpf_io_profile",
            status: "COMPLETED",
            created_at: "2026-08-25T10:01:00Z",
          },
        ]}
        report={{ hypothesis_id: "hyp-io" }}
      />,
    );

    expect(screen.getByText("实际探索树")).toBeInTheDocument();
    expect(screen.getByRole("tree", { name: "真实父子探索树" })).toBeInTheDocument();
    expect(screen.getByText("剪枝 1 条")).toBeInTheDocument();
    expect(screen.getByText("方向切换 1 次")).toBeInTheDocument();
    expect(screen.getByText((_, element) => (
      element.classList.contains("actual-tree-switch")
      && element.textContent.includes("CPU → I/O")
    ))).toBeInTheDocument();
    expect(screen.getAllByText("根因路径").length).toBeGreaterThan(0);
    expect(screen.getAllByText("反证剪枝").length).toBeGreaterThan(0);
  });

  it("defaults to the parent-child tree, while keeping the round path as an auxiliary view", () => {
    const { container } = render(
      <ActualExplorationTree
        hypotheses={[]}
        toolCalls={[]}
        report={{
          exploration_nodes: [
            { id: "root", parent_id: null, title: "异常入口", state: "visited" },
            { id: "root-cause", parent_id: "root", title: "最终根因", state: "confirmed" },
          ],
          exploration_switches: [],
        }}
      />,
    );

    const tree = screen.getByRole("tree", { name: "真实父子探索树" });
    expect(within(tree).getAllByRole("treeitem")).toHaveLength(2);
    const child = container.querySelector('[data-node-id="root-cause"]');
    expect(child).toHaveAttribute("data-parent-id", "root");
    expect(screen.queryByLabelText("按轮次阅读诊断路径")).not.toBeInTheDocument();

    const viewport = screen.getByLabelText("可缩放拖动的诊断探索树");
    expect(screen.getByText("72%")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("放大探索树"));
    expect(screen.getByText("84%")).toBeInTheDocument();
    fireEvent.wheel(viewport, { deltaY: -100 });
    expect(screen.getByText("94%")).toBeInTheDocument();
    fireEvent.doubleClick(viewport);
    expect(screen.getByText("72%")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("打开探索树全屏弹窗"));
    expect(viewport).toHaveClass("is-fullscreen");
    fireEvent.keyDown(viewport, { key: "Escape" });
    expect(viewport).not.toHaveClass("is-fullscreen");

    fireEvent.click(screen.getByText("轮次路径"));
    expect(screen.getByLabelText("按轮次阅读诊断路径")).toBeInTheDocument();
    expect(screen.queryByRole("tree", { name: "真实父子探索树" })).not.toBeInTheDocument();
  });

  it("renders the current revision and active node from a live snapshot", () => {
    render(
      <ActualExplorationTree
        tree={{
          diagnosis_id: "diag-live",
          revision: 8,
          status: "COLLECTING_EVIDENCE",
          active_node_ids: ["evidence:e-1"],
          stats: { rounds: 2, nodes: 4, pruned: 1, current_round: 2 },
          rounds: [
            { round_index: 1, title: "排查 CPU 热点", status: "REFUTED" },
            { round_index: 2, title: "转向 I/O", status: "INVESTIGATING" },
          ],
          nodes: [
            { id: "diagnosis:diag-live", parent_id: null, kind: "diagnosis", title: "订单服务 P99 升高", state: "visited" },
            { id: "hypothesis:h-1", parent_id: "diagnosis:diag-live", kind: "hypothesis", title: "用户态热点", state: "refuted", round_index: 1 },
            { id: "hypothesis:h-2", parent_id: "diagnosis:diag-live", kind: "hypothesis", title: "I/O 等待", state: "visited", round_index: 2 },
            { id: "evidence:e-1", parent_id: "hypothesis:h-2", kind: "evidence", title: "磁盘队列深度异常", state: "visited", status: "ACCEPT_SUPPORT", round_index: 2 },
          ],
          switches: [{ from: "CPU", to: "I/O", reason: "CPU 证据不足" }],
        }}
      />,
    );

    expect(screen.getByText("实时探索树")).toBeInTheDocument();
    expect(screen.getByText("版本 8")).toBeInTheDocument();
    expect(screen.getAllByText("第 2 轮").length).toBeGreaterThan(0);
    expect(screen.getByText("刚刚更新")).toBeInTheDocument();
    expect(screen.getByText("磁盘队列深度异常")).toBeInTheDocument();
    expect(screen.getByRole("tree", { name: "真实父子探索树" })).toBeInTheDocument();
    expect(screen.getByText("支持证据")).toBeInTheDocument();
    expect(screen.getByText("父子关系 3 条")).toBeInTheDocument();
  });

  it("renders server-provided LATS phase, best path and node scores without flattening the tree", () => {
    const { container } = render(
      <ActualExplorationTree
        tree={{
          diagnosis_id: "diag-lats",
          revision: 9,
          stats: { rounds: 2, nodes: 3, current_round: 2 },
          search: {
            algorithm: "PUCT",
            algorithm_version: "lats-v1",
            execution_mode: "BUDGETED_LATS",
            environment_semantics: "LIVE_PROGRESSIVE",
            rollout_semantics: "REAL_TOOL_SINGLE_STEP_NO_ROLLBACK",
            phase: "SELECTION",
            iteration: 4,
            selected_node_id: "hypothesis:h-2",
            best_path_node_ids: ["diagnosis:diag-lats", "hypothesis:h-2"],
            candidate_count: 2,
            latest_selection: {
              node_id: "hypothesis:h-2",
              score: 1.42,
              reason: "高证据增益且预算充足",
              components: { q: 0.74, exploration: 0.35, prior_bonus: 0.33, virtual_loss: 0 },
            },
            budget: {
              max_iterations: 8,
              used_iterations: 4,
              remaining_iterations: 4,
              max_tool_calls: 6,
              used_tool_calls: 2,
              remaining_tool_calls: 4,
            },
            termination: { stopped: false, reason: "RUNNING" },
          },
          nodes: [
            { id: "diagnosis:diag-lats", parent_id: null, kind: "diagnosis", title: "订单服务延迟", state: "visited" },
            {
              id: "hypothesis:h-1",
              parent_id: "diagnosis:diag-lats",
              kind: "hypothesis",
              title: "CPU 热点",
              state: "visited",
              search_metrics: { visits: 2, value_sum: 0.8, mean_value: 0.4, prior: 0.2, uct_score: 0.81, depth: 1 },
            },
            {
              id: "hypothesis:h-2",
              parent_id: "diagnosis:diag-lats",
              kind: "hypothesis",
              title: "I/O 队列拥塞",
              state: "visited",
              search_metrics: {
                visits: 5,
                value_sum: 3.7,
                mean_value: 0.74,
                prior: 0.33,
                uct_score: 1.42,
                reward: 0.8,
                depth: 1,
                selected: true,
                best_path: true,
                last_observation_summary: "磁盘队列深度与延迟同窗上升",
                last_reflection: "CPU 分支收益较低，转向 I/O",
              },
            },
          ],
          edges: [
            { from: "diagnosis:diag-lats", to: "hypothesis:h-1", kind: "BRANCH" },
            { from: "diagnosis:diag-lats", to: "hypothesis:h-2", kind: "BRANCH" },
          ],
        }}
      />,
    );

    expect(screen.getByRole("tree", { name: "真实父子探索树" })).toBeInTheDocument();
    expect(screen.getByLabelText("LATS 搜索状态")).toBeInTheDocument();
    expect(screen.getByText("预算约束 LATS（实时环境不可回退）")).toBeInTheDocument();
    expect(screen.getByText("真实时间持续推进：兄弟分支不可回滚到完全相同的环境状态。")).toBeInTheDocument();
    expect(screen.getByText(/高证据增益且预算充足/)).toBeInTheDocument();
    expect(screen.getByLabelText("LATS 当前最佳路径")).toHaveTextContent("订单服务延迟");
    expect(screen.getByLabelText("LATS 当前最佳路径")).toHaveTextContent("I/O 队列拥塞");

    const selected = container.querySelector('[data-node-id="hypothesis:h-2"]');
    expect(selected).toHaveClass("is-lats-selected");
    expect(selected).toHaveClass("is-lats-best-path");
    expect(selected).toHaveAccessibleName(/访问 5 次/);
    expect(within(selected).getByText("访问次数")).toBeInTheDocument();
    expect(within(selected).getByText("1.420")).toBeInTheDocument();

    const scoreButton = screen.getByRole("button", { name: "查看 LATS 节点评分：I/O 队列拥塞" });
    fireEvent.pointerDown(scoreButton, { button: 0, pointerId: 1 });
    fireEvent.click(scoreButton);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("LATS 节点评分详情")).toBeInTheDocument();
    expect(within(dialog).getByText("评分字段怎么读")).toBeInTheDocument();
    expect(within(dialog).getByText("磁盘队列深度与延迟同窗上升")).toBeInTheDocument();
    expect(within(dialog).getByText("CPU 分支收益较低，转向 I/O")).toBeInTheDocument();
  });

  it("does not invent LATS metrics for legacy tree nodes", () => {
    render(
      <ActualExplorationTree
        tree={{
          revision: 1,
          nodes: [{ id: "diagnosis:legacy", kind: "diagnosis", title: "旧诊断", state: "visited" }],
          edges: [],
        }}
      />,
    );

    expect(screen.queryByLabelText("LATS 搜索状态")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看 LATS 节点评分/ })).not.toBeInTheDocument();
    expect(screen.queryByText("访问次数")).not.toBeInTheDocument();
  });

  it("shows frozen snapshot resets and simulations separately from live tool calls", () => {
    render(
      <ActualExplorationTree
        tree={{
          revision: 4,
          snapshot: {
            snapshot_id: "snapshot-python-hotspot-v1",
            snapshot_digest: "1234567890abcdef1234567890abcdef",
            reset_count: 3,
            frozen: true,
          },
          search: {
            algorithm: "LATS-UCT",
            execution_mode: "FULL_LATS",
            environment_semantics: "FROZEN_REPLAY",
            rollout_semantics: "REPLAY_SIMULATION",
            phase: "BACKPROPAGATION",
            budget: {
              max_simulations: 6,
              used_simulations: 3,
              remaining_simulations: 3,
              max_tool_calls: 0,
              used_tool_calls: 0,
            },
          },
          stats: { rounds: 2, nodes: 1 },
          nodes: [{ id: "diagnosis:replay-1", kind: "diagnosis", title: "冻结热点", state: "visited" }],
        }}
      />,
    );

    expect(screen.getByText("冻结回放探索树")).toBeInTheDocument();
    expect(screen.getByText("完整 LATS（可回放/受控复现）")).toBeInTheDocument();
    const stats = screen.getByLabelText("冻结回放运行统计");
    expect(within(stats).getByText("snapshot-python-hotspot-v1")).toBeInTheDocument();
    expect(within(stats).getByText("重置 3 次")).toBeInTheDocument();
    expect(within(stats).getByText("已用 3/6")).toBeInTheDocument();
    expect(within(stats).getByText("实时工具调用")).toBeInTheDocument();
    expect(within(stats).getByText("0/0")).toBeInTheDocument();
    expect(screen.queryByText(/工具预算/)).not.toBeInTheDocument();
  });

  it("uses persisted edges when nodes do not carry parent ids", () => {
    const { container } = render(
      <ActualExplorationTree
        tree={{
          revision: 3,
          nodes: [
            { id: "hypothesis:h-1", kind: "hypothesis", title: "CPU 热点", state: "visited", round_index: 1 },
            { id: "tool:t-1", kind: "tool", title: "CPU 火焰图", state: "visited", round_index: 1 },
          ],
          edges: [{ from: "hypothesis:h-1", to: "tool:t-1", kind: "INVESTIGATE" }],
          switches: [],
        }}
      />,
    );

    expect(container.querySelector('[data-node-id="tool:t-1"]')).toHaveAttribute(
      "data-parent-id",
      "hypothesis:h-1",
    );
    expect(screen.getByText("发起取证")).toBeInTheDocument();
    expect(screen.getByText("父子关系 1 条")).toBeInTheDocument();
  });

  it("lets a user redirect or challenge a hypothesis node", () => {
    const onIntervene = vi.fn();
    render(
      <ActualExplorationTree
        tree={{
          revision: 2,
          stats: { rounds: 1, nodes: 2, human_interventions: 1 },
          nodes: [
            { id: "diagnosis:d-1", parent_id: null, kind: "diagnosis", title: "入口", state: "visited" },
            { id: "hypothesis:h-1", parent_id: "diagnosis:d-1", kind: "hypothesis", title: "可能是 I/O", state: "visited" },
          ],
          switches: [],
        }}
        hypotheses={[]}
        toolCalls={[]}
        onIntervene={onIntervene}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "寻找反证：可能是 I/O" }));
    expect(onIntervene).toHaveBeenCalledWith(expect.objectContaining({
      action: "CHALLENGE_HYPOTHESIS",
      hypothesis_id: "h-1",
    }));
    expect(screen.getByText("人工干预 1 次")).toBeInTheDocument();
  });

  it("merges legacy paraphrase nodes but preserves descendants and score history", () => {
    const { container } = render(
      <ActualExplorationTree
        tree={{
          revision: 7,
          stats: { rounds: 3, nodes: 5, current_round: 3 },
          rounds: [
            { round_index: 1, title: "首轮 CPU 排查", status: "INVESTIGATING" },
            { round_index: 3, title: "继续热点取证", status: "INVESTIGATING" },
          ],
          search: {
            algorithm: "PUCT",
            selected_node_id: "hypothesis:hotspot-r3",
            best_path_node_ids: ["diagnosis:diag-merge", "hypothesis:hotspot-r3"],
          },
          nodes: [
            { id: "diagnosis:diag-merge", parent_id: null, kind: "diagnosis", title: "CPU 持续升高", state: "visited" },
            {
              id: "hypothesis:hotspot-r1",
              parent_id: "diagnosis:diag-merge",
              kind: "hypothesis",
              title: "目标 Python 进程可能存在用户态 CPU 热点函数或 GIL 竞争",
              state: "visited",
              round_index: 1,
              search_metrics: { visits: 1, mean_value: 0.2, reward: 0.1, uct_score: 0.42 },
            },
            { id: "tool:profile-r1", parent_id: "hypothesis:hotspot-r1", kind: "tool", title: "Python 调用栈", state: "visited", round_index: 1 },
            {
              id: "hypothesis:hotspot-r3",
              parent_id: "diagnosis:diag-merge",
              kind: "hypothesis",
              title: "Python 用户态 CPU 热点函数与 GIL contention 可能导致 CPU 持续升高",
              state: "supported",
              round_index: 3,
              search_metrics: { visits: 3, mean_value: 0.45, reward: -0.2, uct_score: 0.88 },
            },
            { id: "evidence:e-r3", parent_id: "hypothesis:hotspot-r3", kind: "evidence", title: "热点调用栈", state: "visited", round_index: 3 },
          ],
          edges: [
            { from: "diagnosis:diag-merge", to: "hypothesis:hotspot-r1", kind: "BRANCH" },
            { from: "hypothesis:hotspot-r1", to: "tool:profile-r1", kind: "INVESTIGATE" },
            { from: "diagnosis:diag-merge", to: "hypothesis:hotspot-r3", kind: "BRANCH" },
            { from: "hypothesis:hotspot-r3", to: "evidence:e-r3", kind: "SUPPORT" },
          ],
        }}
      />,
    );

    expect(screen.getByText("语义合并 1 个重复假设")).toBeInTheDocument();
    const tree = screen.getByRole("tree", { name: "真实父子探索树" });
    expect(within(tree).getAllByRole("treeitem")).toHaveLength(4);
    const merged = container.querySelector('[data-node-id="hypothesis:hotspot-r3"]');
    expect(merged).toHaveAttribute("data-parent-id", "diagnosis:diag-merge");
    expect(merged).toHaveAccessibleName(/访问 3 次/);
    expect(container.querySelector('[data-node-id="tool:profile-r1"]')).toHaveAttribute("data-parent-id", "hypothesis:hotspot-r3");
    expect(container.querySelector('[data-node-id="evidence:e-r3"]')).toHaveAttribute("data-parent-id", "hypothesis:hotspot-r3");

    fireEvent.click(screen.getByRole("button", { name: /查看 LATS 节点评分：Python 用户态 CPU 热点函数与 GIL contention/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("跨轮评分更新")).toBeInTheDocument();
    expect(within(dialog).getByText(/第 1 轮 · 访问 1 · Q 0.200 · 奖励 0.100/)).toBeInTheDocument();
    expect(within(dialog).getByText(/第 3 轮 · 访问 3 · Q 0.450 · 奖励 -0.200/)).toBeInTheDocument();
  });

  it("shows Skill retrieval, full-body loading and cross-round reuse inside the dynamic tree", () => {
    render(
      <ActualExplorationTree
        tree={{
          revision: 12,
          stats: { rounds: 3, nodes: 2, current_round: 3 },
          nodes: [
            { id: "diagnosis:d-skill", kind: "diagnosis", title: "Python 服务内存持续增长", state: "visited" },
            { id: "hypothesis:h-skill", parent_id: "diagnosis:d-skill", kind: "hypothesis", title: "进程保留内存", state: "visited", round_index: 3 },
          ],
          edges: [{ from: "diagnosis:d-skill", to: "hypothesis:h-skill", kind: "BRANCH" }],
          skill_trace: {
            state: "REUSED",
            skill_id: "python-runtime-diagnosis",
            skill_name: "Python 运行时诊断",
            version: 4,
            category: "PYTHON_RUNTIME",
            source_path: "skills/python-runtime-diagnosis/SKILL.md",
            load_mode: "FULL_SKILL_MD",
            full_skill_loaded: true,
            content_sha256: "7ad5cc2172b3891d",
            loaded_sections: ["适用边界", "调查路线", "停止条件"],
            retrieval: {
              method: "BM25_HASHED_NGRAM_CONCEPTS",
              score: 0.86,
              margin: 0.18,
              category_corrected: true,
              baseline_category: "SYSTEM_RESOURCE",
              selected_category: "PYTHON_RUNTIME",
              reason: "问题明确提到 Python 进程保留内存，且目标运行时匹配。",
            },
            route_steps: [
              { order: 1, tool: "collect_sys_metrics", status: "COMPLETED", round_index: 1 },
              { order: 2, tool: "start_pyspy_profile", status: "COMPLETED", round_index: 2 },
              { order: 3, tool: "collect_memory_profile", status: "CURRENT", round_index: 3 },
            ],
            current_step: 3,
            timeline: [
              { kind: "skill.retrieved", round_index: 1 },
              { kind: "skill.activated", round_index: 1 },
              { kind: "skill.reused", round_index: 2 },
              { kind: "skill.reused", round_index: 3 },
            ],
          },
        }}
      />,
    );

    const lane = screen.getByLabelText("动态探索树中的 Skill 复用轨迹");
    expect(within(lane).getByText("Skill 调查路线")).toBeInTheDocument();
    expect(within(lane).getByText("Python 运行时诊断")).toBeInTheDocument();
    expect(within(lane).getByText("BM25 + 本地哈希/领域概念特征")).toBeInTheDocument();
    expect(within(lane).getByText("完整 SKILL.md 已加载")).toBeInTheDocument();
    expect(within(lane).getByText("路由已纠偏：系统资源 → Python")).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "查看 Skill 阶段：第 2 轮沿用" })).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: /内存.*当前步骤/ })).toBeInTheDocument();
    expect(screen.getByRole("tree", { name: "真实父子探索树" })).toBeInTheDocument();
    expect(screen.getByText("Skill 路线（非证据）")).toBeInTheDocument();

    fireEvent.click(within(lane).getByRole("button", { name: "查看 Skill 阶段：第 3 轮沿用" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Skill 复用详情")).toBeInTheDocument();
    expect(within(dialog).getByText("Skill 是调查先验，不是根因证据")).toBeInTheDocument();
    expect(within(dialog).getByText(/已在命中后按需读取完整 SKILL\.md/)).toBeInTheDocument();
    expect(within(dialog).getByText("为什么命中这个 Skill")).toBeInTheDocument();
    expect(within(dialog).getByText(/问题明确提到 Python 进程保留内存/)).toBeInTheDocument();
  });

  it("accepts an event-array Skill trace during a rolling backend upgrade", () => {
    render(
      <ActualExplorationTree
        tree={{
          revision: 2,
          nodes: [{ id: "diagnosis:d-array", kind: "diagnosis", title: "I/O 延迟", state: "visited" }],
          skill_trace: [
            { kind: "skill.retrieved", skill_id: "io-diagnosis", skill_name: "I/O 延迟诊断", round_index: 1 },
            { kind: "skill.activated", skill_id: "io-diagnosis", skill_name: "I/O 延迟诊断", round_index: 1 },
            { kind: "skill.reused", skill_id: "io-diagnosis", skill_name: "I/O 延迟诊断", round_index: 2 },
          ],
        }}
      />,
    );

    const lane = screen.getByLabelText("动态探索树中的 Skill 复用轨迹");
    expect(within(lane).getByText("I/O 延迟诊断")).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "查看 Skill 阶段：召回候选" })).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "查看 Skill 阶段：激活路线" })).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "查看 Skill 阶段：第 2 轮沿用" })).toBeInTheDocument();
  });

  it("projects the persisted activation and observed-route overlay returned by the backend", () => {
    render(
      <ActualExplorationTree
        tree={{
          revision: 5,
          nodes: [
            { id: "diagnosis:d-projected", kind: "diagnosis", title: "运行时异常", state: "visited" },
            {
              id: "tool:t-projected",
              parent_id: "diagnosis:d-projected",
              kind: "tool",
              title: "start_pyspy_profile",
              state: "visited",
              skill_route_refs: [{ activation_id: "activation-python", skill_id: "skill-python", route_index: 1, selected_by_skill: true }],
            },
          ],
          edges: [{ from: "diagnosis:d-projected", to: "tool:t-projected", kind: "INVESTIGATE" }],
          skill_trace: [{
            activation_id: "activation-python",
            skill_id: "skill-python",
            skill_version: 2,
            skill_category: "PYTHON_RUNTIME",
            summary: "Python 运行时循证诊断",
            source_path: "skills/python-runtime-diagnosis/SKILL.md",
            instruction_sha256: "a".repeat(64),
            instruction_trust: "REPOSITORY_REVIEWED",
            match_score: 0.812,
            retrieval: "HYBRID_BM25_VECTOR",
            matched_terms: ["python", "gil"],
            category_correction: { from: "IO_LATENCY", to: "PYTHON_RUNTIME", guard: "STRONG_TEXT_SIGNAL" },
            selected_tool: "collect_sys_metrics",
            probe_order: ["start_pyspy_profile", "collect_sys_metrics"],
            reuse_trace: [
              { round_index: 1, phase: "INITIAL_PLAN", selected_tool: "start_pyspy_profile" },
              { round_index: 2, phase: "REPLAN", state: "REUSED", selected_tool: "collect_sys_metrics" },
              { round_index: 3, phase: "COUNTER_EVIDENCE_REPLAN", state: "DEVIATED", selected_tool: null, exit_reason: "证据转向" },
              { round_index: 4, phase: "INSUFFICIENT_EVIDENCE_REPLAN", state: "EXHAUSTED", selected_tool: null, exit_reason: "路线工具已用尽" },
            ],
          }],
          skill_route_overlay: {
            correlation: "TOOL_NAME_OBSERVATION_NOT_CAUSAL_ATTRIBUTION",
            routes: [{
              activation_id: "activation-python",
              skill_id: "skill-python",
              steps: [
                { route_index: 1, tool: "start_pyspy_profile", selected_by_skill: true, selected_rounds: [1], observed_statuses: ["COMPLETED"] },
                { route_index: 2, tool: "collect_sys_metrics", selected_by_skill: true, selected_rounds: [2], observed_statuses: [] },
              ],
            }],
          },
        }}
      />,
    );

    const lane = screen.getByLabelText("动态探索树中的 Skill 复用轨迹");
    expect(within(lane).getByText("Python 运行时循证诊断")).toBeInTheDocument();
    expect(within(lane).getByText("BM25 + 本地哈希/领域概念特征")).toBeInTheDocument();
    expect(within(lane).getByText("完整 SKILL.md 已加载")).toBeInTheDocument();
    expect(within(lane).getByText("路由已纠偏：I/O → Python")).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "查看 Skill 阶段：第 2 轮沿用" })).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "查看 Skill 阶段：偏离路线" })).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "查看 Skill 阶段：路线耗尽" })).toBeInTheDocument();
    expect(within(lane).getByRole("button", { name: "采集系统指标，当前步骤" })).toBeInTheDocument();
    expect(screen.getByText("Skill 路线第 1 步 · 已采用")).toBeInTheDocument();
  });
});
