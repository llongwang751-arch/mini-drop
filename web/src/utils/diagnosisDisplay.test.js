import { describe, expect, it } from "vitest";
import {
  checkpointLabel,
  chineseDiagnosticText,
  dedupeDiagnosticRows,
  diagnosisActorLabel,
  diagnosisEventLabel,
  diagnosticErrorText,
  diagnosticStatusLabel,
  diagnosticToolLabel,
  evidenceDecisionLabel,
  evidenceRoleLabel,
  policyDecisionLabel,
  runtimeStatusLabel,
  skillPolicyLabel,
  verificationStatusLabel,
} from "./diagnosisDisplay";

describe("diagnosis display helpers", () => {
  it("renders protocol codes as readable Chinese labels", () => {
    expect(diagnosisEventLabel("lats.backpropagated")).toBe("回传路径价值");
    expect(diagnosisActorLabel("REPLAY_AGENT")).toBe("回放诊断 Agent");
    expect(diagnosticStatusLabel("INSUFFICIENT_EVIDENCE")).toBe("证据不足");
    expect(skillPolicyLabel("DISABLED")).toBe("不使用 Skill");
    expect(runtimeStatusLabel("HEALTHY")).toBe("健康");
    expect(checkpointLabel("postgres")).toBe("PostgreSQL 持久化");
    expect(evidenceRoleLabel("SUPPORTS")).toBe("支持证据");
    expect(evidenceDecisionLabel("ACCEPT_COUNTER")).toBe("采信为反证");
    expect(policyDecisionLabel("REQUIRE_APPROVAL")).toBe("需要人工审批");
    expect(verificationStatusLabel("PARTIAL_WITHOUT_COUNTER")).toBe("部分支持，缺少独立反证或对照");
  });

  it("covers every allow-listed diagnosis tool with a Chinese primary label", () => {
    const tools = [
      "get_agent_status",
      "collect_sys_metrics",
      "collect_database_diagnostics",
      "start_perf_profile",
      "start_ebpf_io_profile",
      "start_pyspy_profile",
      "start_jvm_profile",
      "collect_memory_profile",
      "collect_go_profile",
      "start_continuous_profile",
    ];
    tools.forEach((tool) => {
      expect(diagnosticToolLabel(tool)).not.toBe(tool);
      expect(diagnosticToolLabel(tool)).not.toBe("未知诊断工具");
    });
    expect(diagnosticToolLabel("collect_go_profile")).toContain("Go pprof");
  });

  it("keeps unknown protocol codes out of the primary label", () => {
    expect(diagnosticStatusLabel("FUTURE_INTERNAL_STATE")).toBe("状态未知");
    expect(evidenceRoleLabel("FUTURE_EVIDENCE_ROLE")).toBe("证据角色未知");
    expect(evidenceDecisionLabel("FUTURE_GATE_RESULT")).toBe("门禁判定未知");
    expect(policyDecisionLabel("FUTURE_POLICY_RESULT")).toBe("策略判定未知");
    expect(verificationStatusLabel("FUTURE_VERIFICATION")).toBe("验证状态未知");
    expect(diagnosticToolLabel("future_internal_tool")).toBe("未知诊断工具");
  });

  it("renders known and unknown errors with a Chinese primary explanation", () => {
    expect(diagnosticErrorText("NO_PROFILE_SAMPLES")).toBe("性能剖析未采集到有效样本");
    expect(diagnosticErrorText("permission denied")).toBe("权限不足");
    expect(diagnosticErrorText("opaque driver failure 991")).toContain("请展开技术详情");
  });

  it("translates recurring model-generated diagnostic prose while preserving technical tokens", () => {
    const text = chineseDiagnosticText(
      "Continuous profiling over an extended window captures the synchronous write/fsync call path in the python3.12 target that single-shot probes missed due to transient I/O activity.",
    );
    expect(text).toContain("延长连续性能采集窗口");
    expect(text).toContain("write/fsync");
    expect(text).toContain("python3.12");
  });

  it("deduplicates repeated unknown hypotheses by displayed semantics", () => {
    expect(dedupeDiagnosticRows([
      { statement: "其他未知原因（OTHER/UNKNOWN）" },
      { statement: "其他未知原因（OTHER/UNKNOWN）" },
      { statement: "CPU 热点" },
    ])).toHaveLength(2);
  });
});
