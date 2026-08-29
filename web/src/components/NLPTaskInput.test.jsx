import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import NLPTaskInput from "./NLPTaskInput";

vi.mock("../api/client", () => ({
  createTask: vi.fn(),
  listAgents: vi.fn(),
  listTaskKinds: vi.fn(),
  listTopProcesses: vi.fn(),
  nlpParse: vi.fn(),
}));

import * as api from "../api/client";

const agents = [
  {
    id: "agent-a",
    hostname: "worker-a",
    status: "ONLINE",
    capabilities: ["perf_cpu", "sys_metrics"],
  },
  {
    id: "agent-b",
    hostname: "worker-b",
    status: "ONLINE",
    capabilities: ["pyspy"],
  },
];

const taskKinds = [
  {
    id: "perf_cpu",
    label: "CPU 火焰图",
    result_label: "交互式 CPU 火焰图",
    description: "CPU profile",
    color: "blue",
    default_duration_sec: 15,
    max_duration_sec: 300,
    default_sample_rate: 99,
    flamegraph: true,
  },
  {
    id: "pyspy",
    label: "Python 火焰图",
    result_label: "Python 调用栈火焰图",
    description: "Python profile",
    color: "purple",
    default_duration_sec: 15,
    max_duration_sec: 300,
    default_sample_rate: 99,
    flamegraph: true,
  },
];

describe("NLPTaskInput trusted process discovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listAgents.mockResolvedValue(agents);
    api.listTaskKinds.mockResolvedValue(taskKinds);
    api.listTopProcesses.mockResolvedValue([
      { pid: 321, comm: "orders", service_hint: "order-service" },
    ]);
    api.nlpParse.mockResolvedValue({
      process_name: "order-service",
      selected_pid: null,
      collector_type: "perf_cpu",
      duration_sec: 15,
      sample_rate: 99,
      reasoning: "订单服务 CPU 持续升高",
      candidate_pids: [{ pid: 999, comm: "analysis-engine" }],
      process_candidates_source: "agent_snapshot",
    });
  });

  it("loads process candidates from the selected Agent", async () => {
    render(<NLPTaskInput />);

    await waitFor(() => {
      expect(api.listTopProcesses).toHaveBeenCalledWith("agent-a", 20);
    });
    expect(
      screen.getByText(/选择 Agent 后只显示其实际支持的采集器/),
    ).toBeInTheDocument();

    const collectorColumn = screen.getByText("采集预设").parentElement;
    fireEvent.mouseDown(within(collectorColumn).getByRole("combobox"));
    expect(
      await screen.findAllByText(/CPU 火焰图 · 交互式 CPU 火焰图/),
    ).not.toHaveLength(0);
    expect(screen.queryByText(/Python 火焰图 · Python 调用栈火焰图/)).not.toBeInTheDocument();
  });

  it("ignores Analysis Engine candidate PIDs in the natural-language flow", async () => {
    render(<NLPTaskInput />);
    await waitFor(() => expect(api.listTopProcesses).toHaveBeenCalled());

    fireEvent.click(screen.getByText("自然语言"));
    fireEvent.change(
      screen.getByPlaceholderText(/描述性能问题/),
      { target: { value: "order-service CPU 很高" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "解析意图" }));

    await screen.findByText("确认采集参数");
    expect(screen.getByText(/目标 PID 只从所选 Agent 的最新可信快照中确认/)).toBeInTheDocument();

    const processRow = screen.getByText("Agent 可信进程").closest("tr");
    fireEvent.mouseDown(within(processRow).getByRole("combobox"));
    expect(await screen.findByText("321 · order-service")).toBeInTheDocument();
    expect(screen.queryByText(/999 · analysis-engine/)).not.toBeInTheDocument();
  });
});
