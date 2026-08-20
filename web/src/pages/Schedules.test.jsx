import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import Schedules from "./Schedules";

vi.mock("../api/client", () => ({
  listSchedules: vi.fn(),
  createSchedule: vi.fn(),
  updateSchedule: vi.fn(),
  deleteSchedule: vi.fn(),
  triggerSchedule: vi.fn(),
  listScheduleRecords: vi.fn(),
}));

import * as api from "../api/client";

describe("Schedules page", () => {
  beforeEach(() => {
    api.listSchedules.mockResolvedValue([
      {
        id: "schedule_1",
        name: "夜间巡检",
        cron_expression: "0 3 * * *",
        timezone: "Asia/Shanghai",
        task_template: {},
        enabled: true,
        next_run_at: "2026-08-07T03:00:00Z",
      },
    ]);
  });

  it("renders the schedule list", async () => {
    render(<Schedules />);
    expect(await screen.findByText("夜间巡检")).toBeInTheDocument();
    expect(screen.getByText("0 3 * * *")).toBeInTheDocument();
    expect(await waitFor(() => expect(screen.getByText("Asia/Shanghai")).toBeInTheDocument()));
  });

  it("renders an empty state when no schedules exist", async () => {
    api.listSchedules.mockResolvedValue([]);
    render(<Schedules />);
    await waitFor(() => expect(screen.getByText("计划任务 (Schedule / Cron)")).toBeInTheDocument());
  });

  it("opens the creation drawer from the primary action", async () => {
    api.listSchedules.mockResolvedValue([]);
    render(<Schedules />);
    fireEvent.click(await screen.findByRole("button", { name: /新建计划/ }));
    expect(await screen.findByLabelText("计划名称")).toBeInTheDocument();
    expect(screen.getByLabelText("Cron 表达式 (5 字段)")).toBeInTheDocument();
  });
});
