import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
            tool_name: "start_perf_profile",
            status: "COMPLETED",
            created_at: "2026-08-25T10:00:00Z",
          },
          {
            tool_call_id: "tool-io",
            tool_name: "start_ebpf_io_profile",
            status: "COMPLETED",
            created_at: "2026-08-25T10:01:00Z",
          },
        ]}
        report={{ hypothesis_id: "hyp-io" }}
      />,
    );

    expect(screen.getByText("实际探索树")).toBeInTheDocument();
    expect(screen.getByText("剪枝 1 条")).toBeInTheDocument();
    expect(screen.getByText("方向切换 1 次")).toBeInTheDocument();
    expect(screen.getByText(/方向切换：CPU → I\/O/)).toBeInTheDocument();
    expect(screen.getByText("最终命中")).toBeInTheDocument();
    expect(screen.getByText("已剪枝")).toBeInTheDocument();
  });

  it("lets the user zoom and reset a structured multi-round tree", () => {
    render(
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

    const viewport = screen.getByLabelText("可缩放拖动的诊断探索树");
    expect(screen.getByText("72%")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("放大探索树"));
    expect(screen.getByText("84%")).toBeInTheDocument();
    fireEvent.wheel(viewport, { deltaY: -100 });
    expect(screen.getByText("94%")).toBeInTheDocument();
    fireEvent.doubleClick(viewport);
    expect(screen.getByText("72%")).toBeInTheDocument();
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
            { id: "diagnosis:diag-live", parent_id: null, title: "订单服务 P99 升高", state: "visited" },
            { id: "hypothesis:h-1", parent_id: "diagnosis:diag-live", title: "用户态热点", state: "refuted" },
            { id: "hypothesis:h-2", parent_id: "diagnosis:diag-live", title: "I/O 等待", state: "visited" },
            { id: "evidence:e-1", parent_id: "hypothesis:h-2", title: "磁盘队列深度异常", state: "visited" },
          ],
          switches: [{ from: "CPU", to: "I/O", reason: "CPU 证据不足" }],
        }}
      />,
    );

    expect(screen.getByText("实时探索树")).toBeInTheDocument();
    expect(screen.getByText("版本 8")).toBeInTheDocument();
    expect(screen.getByText("第 2 轮")).toBeInTheDocument();
    expect(screen.getByText("刚刚更新")).toBeInTheDocument();
    expect(screen.getByText("磁盘队列深度异常")).toBeInTheDocument();
  });
});
