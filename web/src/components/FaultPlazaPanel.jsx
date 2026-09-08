import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Empty, Segmented, Skeleton, Space, Tag, Typography, message } from "antd";
import {
  BugOutlined,
  ExperimentOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  getFaultPlaza,
  startFaultPlazaScenario,
  stopFaultPlazaScenario,
} from "../api/client";
import { chineseDiagnosticText, diagnosticToolLabel } from "../utils/diagnosisDisplay";
import "./DiagnosisShowcase.css";

const { Paragraph, Text, Title } = Typography;
const ACTIVE_REFRESH_INTERVAL_MS = 2000;
const RUNTIME_ORDER = ["Go", "Java", "C++", "Python"];
const RECOMMENDED_SCENARIO_IDS = {
  Go: "go-cpu-hotspot",
  Java: "java-gc-pressure",
  "C++": "cpp-cpu-hotspot",
  Python: "source-hotspot",
};
const FULL_CHAIN_VALIDATED_SCENARIOS = new Set(["source-hotspot", "go-cpu-hotspot"]);

function runtimeKey(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "go" || normalized.includes("golang")) return "Go";
  if (normalized === "java" || normalized.includes("jvm")) return "Java";
  if (normalized === "cpp" || normalized.includes("c++")) return "C++";
  if (normalized === "python" || normalized.includes("python")) return "Python";
  return "";
}

function maturityMeta(scenario) {
  const level = String(
    scenario?.acceptance_level || scenario?.validation_level || scenario?.maturity_level || "",
  ).toUpperCase();
  if (
    level === "LIVE_DIAGNOSIS_VERIFIED"
    || level === "FULL_CHAIN"
    || level === "LIVE_E2E"
    || FULL_CHAIN_VALIDATED_SCENARIOS.has(scenario?.scenario_id)
  ) {
    return { color: "green", text: "全链路已验收" };
  }
  return { color: "blue", text: "故障注入已验收" };
}

function recommendedScenarios(scenarios) {
  const selected = RUNTIME_ORDER.map((runtime) => {
    const candidates = scenarios.filter((scenario) => runtimeKey(scenario.target_runtime) === runtime);
    const preferredId = RECOMMENDED_SCENARIO_IDS[runtime];
    return candidates.find((scenario) => scenario.scenario_id === preferredId && scenario.available !== false)
      || candidates.find((scenario) => scenario.available !== false)
      || candidates.find((scenario) => scenario.scenario_id === preferredId)
      || candidates[0];
  }).filter(Boolean);
  return selected.length ? selected : scenarios.slice(0, 4);
}

function withActiveScenarios(base, scenarios) {
  const visibleIds = new Set(base.map((scenario) => scenario.scenario_id));
  const activeOutsideFilter = scenarios.filter(
    (scenario) => scenario.active && !visibleIds.has(scenario.scenario_id),
  );
  return [...activeOutsideFilter, ...base];
}

function statusMeta(status) {
  const value = String(status || "DISABLED").toUpperCase();
  if (value === "READY") return { color: "green", text: "实验室就绪" };
  if (value === "UNREACHABLE") return { color: "red", text: "实验室不可达" };
  return { color: "default", text: "实验室未启用" };
}

function runtimeLabel(value) {
  const labels = {
    python: "Python",
    cpp: "C++",
    "c++": "C++",
    java: "Java / JVM",
    go: "Go",
    system: "Linux 系统",
  };
  return labels[String(value || "").toLowerCase()] || value || "服务端未说明";
}

function familyLabel(value) {
  const labels = {
    MEMORY: "内存",
    IO: "I/O",
    NETWORK: "网络",
    QUEUE: "队列",
    DATABASE: "数据库",
    RUNTIME: "运行时",
  };
  return labels[String(value || "").toUpperCase()] || value || "综合故障";
}

export default function FaultPlazaPanel({ onStartDiagnosis, onPrepareSkillAB }) {
  const [plaza, setPlaza] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [duration, setDuration] = useState(60);
  const [busyKey, setBusyKey] = useState("");
  const [runtimeFilter, setRuntimeFilter] = useState("recommended");

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    setError("");
    try {
      setPlaza(await getFaultPlaza());
    } catch (loadError) {
      setError(loadError?.message || "故障实验室状态加载失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const hasActiveScenario = (plaza?.scenarios || []).some((scenario) => scenario.active);
  useEffect(() => {
    if (!hasActiveScenario) return undefined;

    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void load({ silent: true });
    };
    const intervalId = window.setInterval(refreshWhenVisible, ACTIVE_REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [hasActiveScenario, load]);

  async function startScenario(scenario, intent) {
    if (scenario.available === false) {
      message.warning(scenario.unavailable_reason || "当前运行时尚未准备好，不能启动这个场景");
      return;
    }
    if (intent === "ab" && scenario.supports_skill_ab === false) {
      message.warning(scenario.skill_ab_unavailable_reason || "这个场景暂无可用于对比的已发布 Skill");
      return;
    }
    const key = `${scenario.scenario_id}:${intent}`;
    setBusyKey(key);
    try {
      const started = await startFaultPlazaScenario(scenario.scenario_id, duration);
      await load();
      if (intent === "diagnosis") {
        if (!started?.diagnosis_request) throw new Error("故障场景没有返回诊断请求");
        await onStartDiagnosis?.(started.diagnosis_request, started);
        message.success("受控故障已启动，AI 诊断已创建");
      } else if (intent === "ab") {
        if (!started?.diagnosis_request) throw new Error("故障场景没有返回诊断请求");
        onPrepareSkillAB?.(started.diagnosis_request, started);
        message.success("受控故障已启动，已带入 Skill A/B 对比");
      } else {
        message.success(`${scenario.title}已启动，将在 ${started?.auto_stop_seconds || duration} 秒后自动停止`);
      }
    } catch (actionError) {
      message.error(actionError?.message || "故障场景启动失败");
    } finally {
      setBusyKey("");
    }
  }

  async function stopScenario(scenario) {
    const key = `${scenario.scenario_id}:stop`;
    setBusyKey(key);
    try {
      await stopFaultPlazaScenario(scenario.scenario_id);
      await load();
      message.success(`${scenario.title}已停止`);
    } catch (actionError) {
      message.error(actionError?.message || "故障场景停止失败");
    } finally {
      setBusyKey("");
    }
  }

  const scenarios = useMemo(() => plaza?.scenarios || [], [plaza]);
  const recommendations = useMemo(() => recommendedScenarios(scenarios), [scenarios]);
  const runtimeCounts = useMemo(() => Object.fromEntries(
    RUNTIME_ORDER.map((runtime) => [
      runtime,
      scenarios.filter((scenario) => runtimeKey(scenario.target_runtime) === runtime).length,
    ]),
  ), [scenarios]);
  const visibleScenarios = useMemo(() => {
    let base = scenarios;
    if (runtimeFilter === "recommended") base = recommendations;
    else if (runtimeFilter !== "all") {
      base = scenarios.filter((scenario) => runtimeKey(scenario.target_runtime) === runtimeFilter);
    }
    return withActiveScenarios(base, scenarios);
  }, [recommendations, runtimeFilter, scenarios]);
  const activeCount = scenarios.filter((scenario) => scenario.active).length;
  const runtimeFilterOptions = [
    { value: "recommended", label: `推荐 ${recommendations.length}` },
    { value: "all", label: `全部 ${scenarios.length}` },
    ...RUNTIME_ORDER.map((runtime) => ({ value: runtime, label: `${runtime} ${runtimeCounts[runtime] || 0}` })),
  ];
  const globallyReady = String(plaza?.status || "").toUpperCase() === "READY";
  const hasExplicitlyAvailableScenario = scenarios.some((scenario) => scenario.available === true);
  const ready = globallyReady || hasExplicitlyAvailableScenario;
  const meta = ready && !globallyReady
    ? { color: "orange", text: "部分实验室就绪" }
    : statusMeta(plaza?.status);

  return (
    <section className="fault-plaza" aria-label="受控故障广场">
      <div className="showcase-section-header">
        <div>
          <Title level={4}>故障广场</Title>
          <Paragraph>启动白名单内的真实故障，再让 AI 采集、裁决和验证。所有场景都有自动停止保护。</Paragraph>
        </div>
        <Space wrap>
          <Tag color={meta.color}>{meta.text}</Tag>
          <Segmented
            aria-label="故障持续时间"
            value={duration}
            onChange={setDuration}
            options={[30, 60, 120].map((value) => ({ value, label: `${value} 秒` }))}
          />
          <Button icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>刷新状态</Button>
        </Space>
      </div>

      <Alert
        type={ready ? "info" : "warning"}
        showIcon
        message={ready
          ? (globallyReady ? "仅对 demo-target 实验环境注入故障" : "部分故障场景已就绪")
          : (plaza?.reason || "受控故障实验室尚未启用")}
        description={ready
          ? "页面只能调用服务端固定场景，不能提交 URL、命令、Agent 或 PID。不要把故障注入指向生产环境。"
          : "启动 demo-target profile，并配置服务端的 MINI_DROP_FAULT_LAB_URL 后再刷新。诊断功能本身不受影响。"}
      />
      {error && <Alert type="error" showIcon message="故障广场加载失败" description={error} action={<Button size="small" onClick={() => load()}>重试</Button>} />}

      {scenarios.length > 0 && (
        <div className="fault-plaza-runtime-filter">
          <div>
            <Text strong>按运行时筛选</Text>
            <Text type="secondary" aria-live="polite">
              当前显示 {visibleScenarios.length} / {scenarios.length} 个场景{activeCount ? `，运行中 ${activeCount} 个始终保留` : ""}
            </Text>
          </div>
          <Segmented
            aria-label="故障场景运行时筛选"
            value={runtimeFilter}
            onChange={setRuntimeFilter}
            options={runtimeFilterOptions}
          />
        </div>
      )}

      {loading && !plaza ? (
        <div className="fault-plaza-loading"><Skeleton active paragraph={{ rows: 8 }} /></div>
      ) : scenarios.length === 0 ? (
        <Empty description="服务端未提供可演示故障" />
      ) : (
        <div className="fault-scenario-grid">
          {visibleScenarios.map((scenario) => {
            const supportsSkillAB = scenario.supports_skill_ab !== false;
            const skillABReason = scenario.skill_ab_unavailable_reason || "这个场景暂无可用于对比的已发布 Skill";
            const scenarioAvailable = scenario.available !== false;
            const unavailableReason = scenario.unavailable_reason || "当前运行时尚未准备好";
            const investigationStages = Array.isArray(scenario.investigation_stages) ? scenario.investigation_stages : [];
            const maturity = maturityMeta(scenario);
            return (
            <article className={`fault-scenario ${scenario.active ? "is-active" : ""} ${scenarioAvailable ? "" : "is-unavailable"}`} key={scenario.scenario_id}>
              <div className="fault-scenario-heading">
                <span className="fault-scenario-icon"><BugOutlined /></span>
                <div>
                  <Space wrap size={[6, 4]}>
                    <Text strong>{scenario.title}</Text>
                    <Tag color={scenario.active ? "green" : "blue"}>{scenario.active ? "运行中" : familyLabel(scenario.family)}</Tag>
                    <Tag color={maturity.color}>{maturity.text}</Tag>
                    {!scenarioAvailable && <Tag color="red">当前不可用</Tag>}
                  </Space>
                  <Paragraph>{chineseDiagnosticText(scenario.symptom)}</Paragraph>
                </div>
              </div>
              <dl className="fault-scenario-facts">
                <div><dt>目标运行时</dt><dd>{runtimeLabel(scenario.target_runtime)}</dd></div>
                <div><dt>建议轮次</dt><dd>{scenario.minimum_diagnosis_rounds == null ? "服务端未说明" : `至少 ${scenario.minimum_diagnosis_rounds} 轮`}</dd></div>
                <div><dt>调查阶段</dt><dd>{investigationStages.length
                  ? investigationStages.map((item, index) => {
                      const label = typeof item === "string" ? item : item?.title || item?.name || item?.stage;
                      return <Tag key={`${label || "stage"}:${index}`}>{chineseDiagnosticText(label, `阶段 ${index + 1}`)}</Tag>;
                    })
                  : "由 Agent 动态规划"}</dd></div>
                <div><dt>预期信号</dt><dd>{(scenario.expected_signals || []).map((item) => chineseDiagnosticText(item)).join("；") || "等待服务端说明"}</dd></div>
                <div><dt>推荐采集</dt><dd>{(scenario.recommended_collectors || []).map((item) => <Tag key={item}>{diagnosticToolLabel(item)}</Tag>)}</dd></div>
                <div><dt>关联 Skill</dt><dd><Text code>{scenario.related_skill || "动态规划"}</Text></dd></div>
                {!scenarioAvailable && <div><dt>不可用原因</dt><dd><Text type="danger">{chineseDiagnosticText(unavailableReason)}</Text></dd></div>}
                {!supportsSkillAB && <div><dt>Skill A/B</dt><dd><Text type="secondary">{skillABReason}</Text></dd></div>}
              </dl>
              <div className="fault-scenario-actions">
                <Button
                  icon={<PlayCircleOutlined />}
                  disabled={!ready || !scenarioAvailable}
                  title={!scenarioAvailable ? unavailableReason : undefined}
                  loading={busyKey === `${scenario.scenario_id}:start`}
                  onClick={() => startScenario(scenario, "start")}
                >启动故障</Button>
                <Button
                  type="primary"
                  icon={<ExperimentOutlined />}
                  disabled={!ready || !scenarioAvailable}
                  title={!scenarioAvailable ? unavailableReason : undefined}
                  loading={busyKey === `${scenario.scenario_id}:diagnosis`}
                  onClick={() => startScenario(scenario, "diagnosis")}
                >启动并诊断</Button>
                <Button
                  disabled={!ready || !scenarioAvailable || !supportsSkillAB}
                  title={!scenarioAvailable ? unavailableReason : !supportsSkillAB ? skillABReason : undefined}
                  loading={busyKey === `${scenario.scenario_id}:ab`}
                  onClick={() => startScenario(scenario, "ab")}
                >Skill A/B</Button>
                <Button
                  danger
                  icon={<PauseCircleOutlined />}
                  disabled={!ready || !scenario.active}
                  loading={busyKey === `${scenario.scenario_id}:stop`}
                  onClick={() => stopScenario(scenario)}
                >停止</Button>
              </div>
            </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
