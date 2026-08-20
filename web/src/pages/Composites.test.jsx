import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import Composites from "./Composites";

vi.mock("../api/client", () => ({
  listCompositeTasks: vi.fn(),
  createCompositeTask: vi.fn(),
  getCompositeTask: vi.fn(),
  aggregateCompositeTask: vi.fn(),
  cancelCompositeTask: vi.fn(),
}));

import * as api from "../api/client";

describe("Composites page", () => {
  beforeEach(() => {
    api.listCompositeTasks.mockResolvedValue([
      {
        id: "composite_1",
        name: "综合巡检",
        strategy: "ALL_REQUIRED",
        status: "RUNNING",
        required_success_count: null,
        items: [],
      },
    ]);
  });

  it("renders the composite list", async () => {
    render(<Composites />);
    expect(await screen.findByText("综合巡检")).toBeInTheDocument();
    expect(screen.getByText("ALL_REQUIRED")).toBeInTheDocument();
    expect(await waitFor(() => expect(screen.getByText("RUNNING")).toBeInTheDocument()));
  });
});
