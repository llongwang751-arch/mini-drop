import { describe, expect, it } from "vitest";
import { getFrozenReplayMeta } from "./latsReplay";

describe("getFrozenReplayMeta", () => {
  it("recognizes the frozen showcase target before any LATS event exists", () => {
    expect(getFrozenReplayMeta({
      status: "PLANNING",
      target: {
        kind: "FROZEN_LATS_SHOWCASE",
        snapshot_id: "snapshot-1",
      },
    })).toMatchObject({
      targetKind: "FROZEN_LATS_SHOWCASE",
      isReplaySession: true,
      isFrozenReplay: true,
    });
  });

  it("reads replay metadata nested under the target", () => {
    expect(getFrozenReplayMeta({
      target: {
        replay: {
          mode: "REPLAY",
          execution_mode: "FULL_LATS",
          environment_semantics: "FROZEN_REPLAY",
        },
      },
    })).toMatchObject({
      mode: "REPLAY",
      executionMode: "FULL_LATS",
      environmentSemantics: "FROZEN_REPLAY",
      isFrozenReplay: true,
    });
  });

  it("does not promote an unproven REPLAY label to FULL_LATS", () => {
    expect(getFrozenReplayMeta({ mode: "REPLAY", execution_mode: "BUDGETED_LATS" })).toMatchObject({
      isReplaySession: true,
      isFrozenReplay: false,
    });
  });
});
