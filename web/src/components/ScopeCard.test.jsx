import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ScopeCard from "./ScopeCard";

vi.mock("../api/client", () => ({
  getDropInsightTargetCandidates: vi.fn(),
  listAgents: vi.fn(),
  listTopProcesses: vi.fn(),
}));

import * as api from "../api/client";

function candidate(overrides = {}) {
  return {
    binding_id: "binding-1",
    service: "order-service",
    instance: "order-1",
    process: "order-service",
    collector_capabilities: ["cpu"],
    eligible: true,
    ineligible_reason: null,
    ...overrides,
  };
}

function discovery(overrides = {}) {
  return {
    diagnosis_id: "diag-1",
    diagnosis_version: 7,
    discovery_id: "discovery-1",
    status: "READY",
    reason: null,
    candidates: [candidate()],
    ...overrides,
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

function renderCard(overrides = {}) {
  return render(
    <ScopeCard
      diagnosisId="diag-1"
      diagnosisVersion={7}
      questions={[]}
      onClarify={vi.fn()}
      submitting={false}
      initialTarget={{}}
      initialTimeRange={{}}
      draftKey="diag-1"
      {...overrides}
    />,
  );
}

async function chooseTarget(labelPattern) {
  fireEvent.mouseDown(screen.getByLabelText("诊断目标"));
  fireEvent.click(await screen.findByText(labelPattern));
}

async function fillRequiredScope() {
  fireEvent.change(screen.getByLabelText("服务"), { target: { value: "order-service" } });
  fireEvent.mouseDown(screen.getByLabelText("环境"));
  fireEvent.click(await screen.findByText("production"));
}

describe("ScopeCard secure target discovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    api.getDropInsightTargetCandidates.mockResolvedValue(discovery());
  });

  it("hides raw authority clarification prompts resolved by secure discovery", async () => {
    renderCard({
      questions: [
        { question_id: "target.service", prompt: "要诊断哪个服务？" },
        { question_id: "target.agent_id", prompt: "由哪个在线 Agent 采集？" },
        { question_id: "target.pid", prompt: "要诊断哪个进程 PID？" },
        { question_id: "time_range", prompt: "故障发生在哪个时间范围？" },
      ],
    });

    await screen.findByText("已安全绑定唯一有效目标。");
    expect(screen.getByText("要诊断哪个服务？")).toBeInTheDocument();
    expect(screen.getByText("故障发生在哪个时间范围？")).toBeInTheDocument();
    expect(screen.queryByText("由哪个在线 Agent 采集？")).not.toBeInTheDocument();
    expect(screen.queryByText("要诊断哪个进程 PID？")).not.toBeInTheDocument();
  });

  it("auto-selects exactly one eligible server candidate", async () => {
    renderCard();

    expect(await screen.findByText("已安全绑定唯一有效目标。")).toBeInTheDocument();
    expect(screen.getByText("已选安全目标：binding-1")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("服务"), { target: { value: "orders" } });
    const saved = JSON.parse(window.sessionStorage.getItem("mini-drop-scope-draft:diag-1"));
    expect(Object.keys(saved).sort()).toEqual([
      "binding_id", "discovery_id", "end", "environment", "service", "start",
    ]);
    expect(api.getDropInsightTargetCandidates).toHaveBeenCalledWith("diag-1");
    expect(api.listAgents).not.toHaveBeenCalled();
    expect(api.listTopProcesses).not.toHaveBeenCalled();
  });

  it("requires an explicit closed selection when multiple candidates are eligible", async () => {
    api.getDropInsightTargetCandidates.mockResolvedValue(discovery({
      status: "AMBIGUOUS",
      candidates: [candidate(), candidate({ binding_id: "binding-2", service: "payment-service", instance: "payment-2", process: "payment-service" })],
    }));
    renderCard();

    expect(await screen.findByText("发现多个有效目标，请明确选择一个服务端签发的安全绑定。")).toBeInTheDocument();
    expect(screen.queryByText(/已选安全目标/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认范围并开始取证" })).toBeDisabled();
    expect(screen.queryByPlaceholderText(/输入.*PID/)).not.toBeInTheDocument();

    await chooseTarget(/payment-service · payment-2 · 安全目标 binding-2/);
    expect(screen.getByText("已选安全目标：binding-2")).toBeInTheDocument();
  });

  it.each([
    ["EMPTY", "未发现可用诊断目标，请确认采集 Agent 和目标进程正在运行。"],
    ["STALE", "目标候选信息已过期，请重新发现后再提交。"],
    ["UNAVAILABLE", "目标发现服务暂不可用，请稍后重试。"],
    ["TRUNCATED", "目标候选结果不完整，无法安全确认目标，请缩小范围后重试。"],
  ])("shows a distinct %s state and disables submission", async (status, expectedMessage) => {
    api.getDropInsightTargetCandidates.mockResolvedValue(discovery({ status, candidates: [] }));
    renderCard();

    expect(await screen.findByText(expectedMessage)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认范围并开始取证" })).toBeDisabled();
  });

  it("ignores unsafe legacy draft fields and discards a mismatched discovery draft", async () => {
    window.sessionStorage.setItem("mini-drop-scope-draft:diag-1", JSON.stringify({
      diagnosis_id: "diag-1",
      discovery_id: "old-discovery",
      binding_id: "binding-1",
      service: "draft-service",
      environment: "production",
      agent_id: "attacker-agent",
      pid: 99999,
    }));
    renderCard();

    await screen.findByText("已安全绑定唯一有效目标。");
    expect(screen.getByLabelText("服务")).toHaveValue("");
    expect(screen.getByText("已选安全目标：binding-1")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("mini-drop-scope-draft:diag-1")).toBeNull();
    expect(screen.queryByText("attacker-agent")).not.toBeInTheDocument();
    expect(screen.queryByText("99999")).not.toBeInTheDocument();
  });

  it("restores only a binding from the matching discovery and persists safe draft fields", async () => {
    api.getDropInsightTargetCandidates.mockResolvedValue(discovery({
      status: "AMBIGUOUS",
      candidates: [candidate(), candidate({ binding_id: "binding-2", service: "payment-service", instance: "payment-2", process: "payment-service" })],
    }));
    window.sessionStorage.setItem("mini-drop-scope-draft:diag-1", JSON.stringify({
      diagnosis_id: "diag-1",
      discovery_id: "discovery-1",
      binding_id: "binding-2",
      service: "order-service",
      environment: "production",
      start: "2026-08-24T10:00",
      end: "2026-08-24T10:05",
      agent_id: "ignored-agent",
      pid: 999,
    }));
    renderCard();

    expect(await screen.findByText("已选安全目标：binding-2")).toBeInTheDocument();
    expect(screen.getByLabelText("服务")).toHaveValue("order-service");
    fireEvent.change(screen.getByLabelText("服务"), { target: { value: "orders" } });
    const saved = JSON.parse(window.sessionStorage.getItem("mini-drop-scope-draft:diag-1"));
    expect(saved).toEqual(expect.objectContaining({
      discovery_id: "discovery-1",
      binding_id: "binding-2",
      service: "orders",
    }));
    expect(saved).not.toHaveProperty("diagnosis_id");
    expect(saved).not.toHaveProperty("agent_id");
    expect(saved).not.toHaveProperty("pid");
  });

  it("fails closed when the selected binding disappears on refresh", async () => {
    api.getDropInsightTargetCandidates
      .mockResolvedValueOnce(discovery())
      .mockResolvedValueOnce(discovery({ discovery_id: "discovery-2", candidates: [candidate({ binding_id: "binding-2", service: "payment-service", instance: "payment-2", process: "payment-service" })] }));
    renderCard();
    await screen.findByText("已选安全目标：binding-1");

    fireEvent.click(screen.getByRole("button", { name: /重新发现目标/ }));

    expect(await screen.findByText("先前选择的目标已失效，请从最新候选中重新选择。")).toBeInTheDocument();
    expect(screen.queryByText(/已选安全目标/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认范围并开始取证" })).toBeDisabled();
  });

  it("submits expected version and binding authority without raw agent or PID", async () => {
    const onClarify = vi.fn().mockResolvedValue(undefined);
    renderCard({ onClarify });
    await screen.findByText("已选安全目标：binding-1");
    await fillRequiredScope();

    fireEvent.click(screen.getByRole("button", { name: "确认范围并开始取证" }));

    await waitFor(() => expect(onClarify).toHaveBeenCalledTimes(1));
    const payload = onClarify.mock.calls[0][0];
    expect(payload).toMatchObject({
      expected_version: 7,
      target: {
        service: "order-service",
        environment: "production",
        binding_id: "binding-1",
        discovery_id: "discovery-1",
      },
    });
    expect(payload.target).not.toHaveProperty("agent_id");
    expect(payload.target).not.toHaveProperty("pid");
  });

  it("does not resubmit an immutable server time range", async () => {
    const onClarify = vi.fn().mockResolvedValue(undefined);
    renderCard({
      onClarify,
      questions: [{ question_id: "time_range", prompt: "请重新选择时间窗" }],
      initialTimeRange: { start: "2026-09-05T10:00:31Z", end: "2026-09-05T10:05:45Z" },
    });
    await screen.findByText("诊断时间窗已锁定");
    await fillRequiredScope();

    expect(screen.queryByText("请重新选择时间窗")).not.toBeInTheDocument();
    expect(screen.getByLabelText("开始时间")).toBeDisabled();
    expect(screen.getByLabelText("结束时间")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确认目标并开始取证" }));

    await waitFor(() => expect(onClarify).toHaveBeenCalledTimes(1));
    expect(onClarify.mock.calls[0][0]).not.toHaveProperty("time_range");
  });

  it("ignores a late discovery response after switching diagnoses", async () => {
    const first = deferred();
    api.getDropInsightTargetCandidates.mockImplementation((id) => {
      if (id === "diag-1") return first.promise;
      return Promise.resolve(discovery({
        diagnosis_id: "diag-2",
        diagnosis_version: 8,
        discovery_id: "discovery-2",
        candidates: [candidate({ binding_id: "binding-2", service: "payment-service", instance: "payment-2", process: "payment-service" })],
      }));
    });
    const props = {
      diagnosisId: "diag-1",
      diagnosisVersion: 7,
      questions: [],
      onClarify: vi.fn(),
      submitting: false,
      draftKey: "diag-1",
    };
    const { rerender } = render(<ScopeCard {...props} />);
    rerender(<ScopeCard {...props} diagnosisId="diag-2" diagnosisVersion={8} draftKey="diag-2" />);
    expect(await screen.findByText("已选安全目标：binding-2")).toBeInTheDocument();

    await act(async () => {
      first.resolve(discovery());
      await first.promise;
    });
    await waitFor(() => expect(screen.queryByText("已选安全目标：binding-1")).not.toBeInTheDocument());
    expect(screen.getByText("已选安全目标：binding-2")).toBeInTheDocument();
  });
});
