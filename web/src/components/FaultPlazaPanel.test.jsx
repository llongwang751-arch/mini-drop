import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FaultPlazaPanel from "./FaultPlazaPanel";
import engineeringIndex from "../../public/report-assets/engineering-diagnosis/index.json";

vi.mock("../api/client", () => ({
  getFaultPlaza: vi.fn(),
  startFaultPlazaScenario: vi.fn(),
  stopFaultPlazaScenario: vi.fn(),
}));

import * as api from "../api/client";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FaultPlazaPanel", () => {
  it("prefers current engineering localization over a frozen strict failure", async () => {
    api.getFaultPlaza.mockResolvedValue({ status: "READY", scenarios: [{
      scenario_id: "go-cpu-hotspot", title: "Go CPU", target_runtime: "Go", active: false,
      latest_acceptance: { passed: false, root_cause_accepted: false },
    }] });
    render(<FaultPlazaPanel engineeringCases={[{ scenario_id: "go-cpu-hotspot", diagnosis_accepted: true,
      localization_accepted: true, outcome: "LOCALIZED_ANOMALY" }]} />);
    expect(await screen.findByText("工程定位通过")).toBeInTheDocument();
    expect(screen.queryByText("历史严格复验未通过")).not.toBeInTheDocument();
  });

  it("does not count a measured refutation as positive root localization", async () => {
    api.getFaultPlaza.mockResolvedValue({ status: "READY", scenarios: [{
      scenario_id: "go-file-io", title: "Go I/O", target_runtime: "Go", active: false,
    }] });
    render(<FaultPlazaPanel engineeringCases={[{ scenario_id: "go-file-io", diagnosis_accepted: true,
      localization_accepted: false, outcome: "REFUTED" }]} />);
    expect(await screen.findByText("判断通过 · 异常假设被反驳")).toBeInTheDocument();
    expect(screen.queryByText("工程定位通过")).not.toBeInTheDocument();
  });
  it("shows the full performance catalog when requested by the performance entry", async () => {
    api.getFaultPlaza.mockResolvedValue({ status: "READY", scenarios: [
      { scenario_id: "source-hotspot", title: "Python CPU", target_runtime: "Python", family: "CPU" },
      { scenario_id: "io-write-latency", title: "Python 同步写入", target_runtime: "Python", family: "IO" },
      { scenario_id: "memory-growth", title: "Python 内存增长", target_runtime: "Python", family: "MEMORY" },
    ] });
    render(<FaultPlazaPanel initialFilter="all" />);
    expect(await screen.findByText("Python 同步写入")).toBeInTheDocument();
    expect(screen.getByText("Python 内存增长")).toBeInTheDocument();
    expect(screen.getByText(/当前显示 3 \/ 3 个场景/)).toBeInTheDocument();
    expect(api.startFaultPlazaScenario).not.toHaveBeenCalled();
  });
  it("opens the engineering diagnosis rather than a legacy result without injecting a fault", async () => {
    api.getFaultPlaza.mockResolvedValue({ status: "READY", scenarios: [{
      scenario_id: "java-lock-contention", title: "Java 锁等待", target_runtime: "Java",
      acceptance_level: "LIVE_DIAGNOSIS_VERIFIED", latest_acceptance: {
        passed: true, root_cause_accepted: false, lineage_verified: true,
        recovery_observed: true, cleanup_verified: true, tested_release: "test-release",
        diagnosis_id: "insight-acceptance",
      },
    }] });
    const open = vi.fn();
    render(<FaultPlazaPanel onOpenDiagnosis={open} engineeringCases={[{
      scenario_id: "java-lock-contention", diagnosis_id: "insight-engineering",
      diagnosis_accepted: true, localization_accepted: false, outcome: "SUPPORTED_OBSERVATION",
    }]} />);
    expect(await screen.findByText("有证据支持的诊断候选")).toBeInTheDocument();
    expect(screen.queryByText("历史严格复验未通过")).not.toBeInTheDocument();
    expect(screen.queryByText("历史严格复验通过 · 未验证代码修复")).not.toBeInTheDocument();
    expect(screen.queryByText(/test-release|根因未通过|最近复验/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看复验诊断" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看工程诊断" }));
    expect(open).toHaveBeenCalledWith("insight-engineering");
    expect(api.startFaultPlazaScenario).not.toHaveBeenCalled();
  });

  it.each([false, true])("never falls back to a legacy score while engineering data is unavailable (legacy passed=%s)", async passed => {
    api.getFaultPlaza.mockResolvedValue({ status: "READY", scenarios: [{
      scenario_id: "go-cpu-hotspot", title: "Go CPU", target_runtime: "Go", active: false,
      acceptance_level: "LIVE_DIAGNOSIS_VERIFIED", latest_acceptance: {
        passed, root_cause_accepted: passed, lineage_verified: true,
        recovery_observed: true, cleanup_verified: true, tested_release: "legacy-release",
        diagnosis_id: "insight-legacy",
      },
    }] });
    render(<FaultPlazaPanel onOpenDiagnosis={vi.fn()} />);
    expect(await screen.findByText("尚未按工程规则验收")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /启动并诊断/ })).toBeEnabled();
    expect(screen.queryByText(/历史严格|历史链路|根因通过|根因未通过|legacy-release/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看复验诊断" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看工程诊断" })).not.toBeInTheDocument();
  });

  it("keeps all 21 current engineering scenario grades independent of old failures", async () => {
    const scenarios = engineeringIndex.cases.map(item => ({
      scenario_id: item.scenario_id, title: item.title, target_runtime: "Go", active: false,
      latest_acceptance: { passed: false, root_cause_accepted: false },
    }));
    api.getFaultPlaza.mockResolvedValue({ status: "READY", scenarios });
    render(<FaultPlazaPanel initialFilter="all" engineeringCases={engineeringIndex.cases} />);
    expect(await screen.findByText("当前显示 21 / 21 个场景")).toBeInTheDocument();
    expect(document.querySelectorAll("article.fault-scenario")).toHaveLength(21);
    expect(screen.getAllByText("工程定位通过")).toHaveLength(6);
    expect(screen.getAllByText("判断通过 · 异常假设被反驳")).toHaveLength(8);
    expect(screen.getAllByRole("button", { name: "查看工程诊断" })).toHaveLength(21);
    expect(screen.queryByText(/历史严格|根因未通过|原始因果根因/)).not.toBeInTheDocument();
  });

  beforeEach(() => {
    vi.resetAllMocks();
    api.getFaultPlaza.mockResolvedValue({
      status: "READY",
      scenarios: [{
        scenario_id: "cpu-hot-loop",
        title: "CPU 热循环",
        family: "CPU",
        symptom: "CPU 持续升高",
        expected_signals: ["user CPU 上升"],
        recommended_collectors: ["perf_cpu"],
        related_skill: "cpu-hotspot-diagnosis",
        active: false,
      }],
    });
    api.startFaultPlazaScenario.mockResolvedValue({
      scenario_id: "cpu-hot-loop",
      auto_stop_seconds: 60,
      diagnosis_request: { query: "诊断 CPU 热循环", mode: "AUTONOMOUS", auto_scope: true },
    });
  });

  it("starts an allowlisted real fault and forwards its diagnosis request", async () => {
    const onStartDiagnosis = vi.fn().mockResolvedValue(undefined);
    render(<FaultPlazaPanel onStartDiagnosis={onStartDiagnosis} />);

    expect(await screen.findByText("CPU 热循环")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /启动并诊断/ }));

    await waitFor(() => expect(api.startFaultPlazaScenario).toHaveBeenCalledWith("cpu-hot-loop", 60));
    expect(onStartDiagnosis).toHaveBeenCalledWith(
      expect.objectContaining({ query: "诊断 CPU 热循环" }),
      expect.objectContaining({ scenario_id: "cpu-hot-loop" }),
    );
  });

  it("stops an injected fault when diagnosis creation fails", async () => {
    api.stopFaultPlazaScenario.mockResolvedValue({ scenario: { active: false } });
    const onStartDiagnosis = vi.fn().mockRejectedValue(new Error("diagnosis unavailable"));
    render(<FaultPlazaPanel onStartDiagnosis={onStartDiagnosis} />);
    await screen.findByText("CPU 热循环");
    fireEvent.click(screen.getByRole("button", { name: /启动并诊断/ }));
    await waitFor(() => expect(api.stopFaultPlazaScenario).toHaveBeenCalledWith("cpu-hot-loop"));
  });

  it("silently refreshes while a visible fault scenario is active", async () => {
    const intervalSpy = vi.spyOn(window, "setInterval").mockImplementation(() => 17);
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    api.getFaultPlaza
      .mockResolvedValueOnce({
        status: "READY",
        scenarios: [{
          scenario_id: "cpu-hot-loop",
          title: "CPU 热循环",
          family: "CPU",
          symptom: "CPU 持续升高",
          active: true,
        }],
      })
      .mockResolvedValueOnce({
        status: "READY",
        scenarios: [{
          scenario_id: "cpu-hot-loop",
          title: "CPU 热循环",
          family: "CPU",
          symptom: "CPU 持续升高",
          active: false,
        }],
      });

    render(<FaultPlazaPanel />);
    expect(await screen.findByText("运行中")).toBeInTheDocument();
    expect(intervalSpy).toHaveBeenCalledWith(expect.any(Function), 2000);
    const [refreshActiveScenario] = intervalSpy.mock.calls.find(([, delay]) => delay === 2000);

    await act(async () => {
      refreshActiveScenario();
      await Promise.resolve();
    });

    await waitFor(() => expect(api.getFaultPlaza).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("运行中")).not.toBeInTheDocument();
    intervalSpy.mockRestore();
  });

  it("disables Skill A/B when the server reports no published route", async () => {
    const reason = "关联 Skill queue-backlog-diagnosis 尚未发布";
    api.getFaultPlaza.mockResolvedValue({
      status: "READY",
      scenarios: [{
        scenario_id: "queue-backlog",
        title: "生产消费队列堆积",
        family: "Queue",
        symptom: "队列持续增长",
        related_skill: "queue-backlog-diagnosis",
        supports_skill_ab: false,
        skill_ab_unavailable_reason: reason,
        active: false,
      }],
    });

    render(<FaultPlazaPanel />);

    expect(await screen.findByText(reason)).toBeInTheDocument();
    const skillButton = screen.getByRole("button", { name: "Skill A/B" });
    expect(skillButton).toBeDisabled();
    fireEvent.click(skillButton);
    expect(api.startFaultPlazaScenario).not.toHaveBeenCalled();
  });

  it("shows one recommended scenario per runtime with counts and non-Python runtimes first", async () => {
    api.getFaultPlaza.mockResolvedValue({
      status: "READY",
      scenarios: [
        { scenario_id: "source-hotspot", title: "Python 源码热点", target_runtime: "Python", active: false },
        { scenario_id: "go-file-io", title: "Go 文件写入", target_runtime: "Go", active: false },
        { scenario_id: "go-cpu-hotspot", title: "Go 服务 CPU 热点", target_runtime: "Go", acceptance_level: "LIVE_DIAGNOSIS_VERIFIED", active: false },
        { scenario_id: "java-gc-pressure", title: "Java 分配风暴", target_runtime: "Java", active: false },
        { scenario_id: "cpp-cpu-hotspot", title: "C++ 计算热点", target_runtime: "C++", active: false },
      ],
    });

    render(<FaultPlazaPanel />);

    expect(await screen.findByText("推荐 4")).toBeInTheDocument();
    expect(screen.getByText("全部 5")).toBeInTheDocument();
    expect(screen.getByText("Go 2")).toBeInTheDocument();
    expect(screen.getByText("Java 1")).toBeInTheDocument();
    expect(screen.getByText("C++ 1")).toBeInTheDocument();
    expect(screen.getByText("Python 1")).toBeInTheDocument();
    expect(screen.getByText("当前显示 4 / 5 个场景")).toBeInTheDocument();
    expect(screen.queryByText("Go 文件写入")).not.toBeInTheDocument();

    const plaza = screen.getByLabelText("受控故障广场");
    const headings = [...plaza.querySelectorAll("article.fault-scenario")]
      .map((card) => card.querySelector(".fault-scenario-heading")?.textContent || "");
    expect(headings[0]).toContain("Go 服务 CPU 热点");
    expect(headings[1]).toContain("Java 分配风暴");
    expect(headings[2]).toContain("C++ 计算热点");
    expect(headings[3]).toContain("Python 源码热点");

    expect(within(screen.getByText("Go 服务 CPU 热点").closest("article")).getByText("尚未按工程规则验收")).toBeInTheDocument();
    expect(within(screen.getByText("Python 源码热点").closest("article")).getByText("尚未按工程规则验收")).toBeInTheDocument();
    expect(screen.queryByText("全链路已验收")).not.toBeInTheDocument();
  });

  it("filters by runtime while keeping an active scenario visible for safe stopping", async () => {
    vi.spyOn(window, "setInterval").mockImplementation(() => 17);
    api.getFaultPlaza.mockResolvedValue({
      status: "READY",
      scenarios: [
        { scenario_id: "source-hotspot", title: "正在运行的 Python 故障", target_runtime: "Python", active: true },
        { scenario_id: "go-cpu-hotspot", title: "Go 服务 CPU 热点", target_runtime: "Go", active: false },
        { scenario_id: "java-gc-pressure", title: "Java 分配风暴", target_runtime: "Java", active: false },
      ],
    });

    render(<FaultPlazaPanel />);
    expect(await screen.findByText("Go 服务 CPU 热点")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Java 1"));

    expect(screen.getByText("Java 分配风暴")).toBeInTheDocument();
    expect(screen.getByText("正在运行的 Python 故障")).toBeInTheDocument();
    expect(screen.queryByText("Go 服务 CPU 热点")).not.toBeInTheDocument();
    expect(screen.getByText("当前显示 2 / 3 个场景，运行中 1 个始终保留")).toBeInTheDocument();
    expect(within(screen.getByText("正在运行的 Python 故障").closest("article")).getByRole("button", { name: /停止/ })).toBeEnabled();
  });

  it("shows runtime investigation metadata and disables only the unavailable scenario", async () => {
    api.getFaultPlaza.mockResolvedValue({
      status: "DEGRADED",
      scenarios: [
        {
          scenario_id: "java-gc",
          title: "Java GC 抖动",
          family: "RUNTIME",
          symptom: "停顿时间升高",
          target_runtime: "java",
          minimum_diagnosis_rounds: 3,
          investigation_stages: ["确认堆压力", "采集 JVM 证据", "寻找反证"],
          available: false,
          unavailable_reason: "JVM 演示进程尚未启动",
        },
        {
          scenario_id: "cpp-io",
          title: "C++ 同步写阻塞",
          family: "IO",
          symptom: "I/O 等待升高",
          target_runtime: "cpp",
          minimum_diagnosis_rounds: 2,
          investigation_stages: ["系统指标", "调用路径"],
          available: true,
        },
      ],
    });

    render(<FaultPlazaPanel />);

    const javaCard = (await screen.findByText("Java GC 抖动")).closest("article");
    const cppCard = screen.getByText("C++ 同步写阻塞").closest("article");
    expect(within(javaCard).getByText("Java / JVM")).toBeInTheDocument();
    expect(within(javaCard).getByText("至少 3 轮")).toBeInTheDocument();
    expect(within(javaCard).getByText("JVM 演示进程尚未启动")).toBeInTheDocument();
    expect(within(javaCard).getByRole("button", { name: /启动并诊断/ })).toBeDisabled();
    expect(within(cppCard).getByText("C++")).toBeInTheDocument();
    expect(within(cppCard).getByRole("button", { name: /启动并诊断/ })).toBeEnabled();
    expect(screen.getByText("部分实验室就绪")).toBeInTheDocument();
  });
});
