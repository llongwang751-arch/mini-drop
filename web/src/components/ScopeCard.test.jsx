import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ScopeCard from "./ScopeCard";

vi.mock("../api/client", () => ({
  listAgents: vi.fn(),
  listTopProcesses: vi.fn(),
}));

import * as api from "../api/client";

describe("ScopeCard", () => {
  beforeEach(() => {
    api.listAgents.mockResolvedValue([
      { id: "control-campaign-agent", hostname: "control", status: "ONLINE" },
    ]);
    api.listTopProcesses.mockResolvedValue([
      { pid: 321, comm: "python-hotspot", cpu_percent: 80 },
    ]);
  });

  it("keeps user input when polling supplies new prop objects", async () => {
    const props = {
      questions: [],
      onClarify: vi.fn(),
      submitting: false,
      initialTarget: {},
      initialTimeRange: {},
    };
    const { rerender } = render(<ScopeCard {...props} />);
    const serviceInput = screen.getByLabelText("服务");
    fireEvent.change(serviceInput, { target: { value: "mini-drop-control" } });
    expect(serviceInput).toHaveValue("mini-drop-control");

    rerender(
      <ScopeCard
        {...props}
        initialTarget={{}}
        initialTimeRange={{}}
      />,
    );

    await waitFor(() => expect(screen.getByLabelText("服务")).toHaveValue("mini-drop-control"));
  });
});
