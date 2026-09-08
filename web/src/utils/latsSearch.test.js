import { describe, expect, it } from "vitest";
import { latsPhaseGroup, latsPhaseLabel } from "./latsSearch";

describe("LATS phase presentation", () => {
  it.each([
    ["ACTION_PROPOSED", "提出工具行动"],
    ["AWAITING_APPROVAL", "等待人工审批"],
    ["ACTION_BLOCKED", "行动被策略拦截"],
    ["SIMULATION", "环境模拟 / 执行"],
  ])("renders durable sub-phase %s in Chinese", (phase, label) => {
    expect(latsPhaseLabel(phase)).toBe(label);
  });

  it.each(["ACTION_PROPOSED", "AWAITING_APPROVAL", "ACTION_BLOCKED", "SIMULATION"])(
    "highlights the compact action step for %s",
    (phase) => {
      expect(latsPhaseGroup(phase)).toBe("ACTION");
    },
  );
});
