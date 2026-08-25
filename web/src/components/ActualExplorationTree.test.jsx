import { render, screen } from "@testing-library/react";
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
});
