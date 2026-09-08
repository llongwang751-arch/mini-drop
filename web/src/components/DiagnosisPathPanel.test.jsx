import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import DiagnosisPathPanel, { groupDiagnosisEvents } from "./DiagnosisPathPanel";

afterEach(cleanup);

const events = [
  { event_id: "e-1", event_type: "lats.search_started", actor: "SYSTEM", occurred_at: "2026-09-06T10:00:00Z", payload: {} },
  { event_id: "e-2", event_type: "lats.node_selected", actor: "REPLAY_AGENT", occurred_at: "2026-09-06T10:00:01Z", payload: { iteration: 1, node_id: "h-1", reason: "选择证据增益最高的分支" } },
  { event_id: "e-3", event_type: "lats.simulation_started", actor: "SYSTEM", occurred_at: "2026-09-06T10:00:02Z", payload: { iteration: 1, node_id: "h-1" } },
  { event_id: "e-3", event_type: "lats.simulation_started", actor: "SYSTEM", occurred_at: "2026-09-06T10:00:02Z", payload: { iteration: 1, node_id: "h-1" } },
  { event_id: "e-4", event_type: "lats.observation_recorded", actor: "FROZEN_ENVIRONMENT", occurred_at: "2026-09-06T10:00:03Z", payload: { node_id: "h-1", summary: "冻结观察完成" } },
  { event_id: "e-5", event_type: "lats.node_selected", actor: "REPLAY_AGENT", occurred_at: "2026-09-06T10:00:04Z", payload: { iteration: 2, node_id: "h-2" } },
];

describe("DiagnosisPathPanel", () => {
  it("groups legal repeated LATS phases by iteration and only removes exact duplicates", () => {
    const groups = groupDiagnosisEvents(events);
    expect(groups.map((group) => group.events.length)).toEqual([1, 3, 1]);
    expect(groups[1].iteration).toBe(1);
    expect(groups[2].iteration).toBe(2);
  });

  it("shows Chinese event and actor labels, with raw protocol codes in secondary details", () => {
    render(<DiagnosisPathPanel events={events} />);
    expect(screen.getByText("LATS 搜索准备")).toBeInTheDocument();
    expect(screen.getByText("LATS 第 1 次搜索迭代")).toBeInTheDocument();
    expect(screen.getByText("LATS 第 2 次搜索迭代")).toBeInTheDocument();

    const firstIterationHeader = screen.getByRole("button", { name: /LATS 第 1 次搜索迭代/ });
    fireEvent.click(firstIterationHeader);
    const firstIteration = firstIterationHeader.closest(".ant-collapse-item").querySelector(".ant-collapse-content");
    expect(within(firstIteration).getByText("回放诊断 Agent")).toBeInTheDocument();
    expect(within(firstIteration).getByText("记录环境观察")).toBeInTheDocument();
    fireEvent.click(within(firstIteration).getAllByText("查看原始事件信息")[0]);
    expect(within(firstIteration).getByText("lats.node_selected")).toBeInTheDocument();
  });
});
