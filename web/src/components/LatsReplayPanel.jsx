import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, Skeleton, Space, Tag, Typography, message } from "antd";
import {
  BranchesOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { getLatsReplayShowcases, runLatsReplayShowcase } from "../api/client";
import "./LatsReplayPanel.css";

const { Paragraph, Text, Title } = Typography;

function scenariosOf(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.scenarios)) return payload.scenarios;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

function newClientRunId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  // The fallback keeps the UUID contract for older secure-browser contexts.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (token) => {
    const random = Math.floor(Math.random() * 16);
    const value = token === "x" ? random : ((random & 0x3) | 0x8);
    return value.toString(16);
  });
}

function boundaryText(value) {
  if (!value) return "只验证冻结快照内已经记录的观察，不代表当前线上状态";
  if (typeof value === "string") return value;
  return value.summary || value.description || value.label
    || "只验证冻结快照内已经记录的观察，不代表当前线上状态";
}

export default function LatsReplayPanel({ onOpenDiagnosis, onCasesChanged }) {
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [runningId, setRunningId] = useState("");
  const [lastRun, setLastRun] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setCatalog(await getLatsReplayShowcases());
    } catch (loadError) {
      setError(loadError?.message || "冻结回放场景加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function runScenario(scenario) {
    const scenarioId = scenario?.scenario_id || scenario?.id;
    if (!scenarioId || runningId) return;
    setRunningId(scenarioId);
    try {
      const clientRunId = newClientRunId();
      const result = await runLatsReplayShowcase(scenarioId, clientRunId);
      if (!result?.diagnosis_id) throw new Error("回放服务没有返回 diagnosis_id");
      setLastRun({ ...result, client_run_id: clientRunId, scenario_id: scenarioId });
      // Invoke navigation as soon as the new session id exists. List refresh is
      // deliberately fire-and-forget so it cannot delay opening the diagnosis.
      await onOpenDiagnosis?.(result.diagnosis_id);
      void Promise.resolve(onCasesChanged?.()).catch(() => undefined);
      message.success("已创建新的 FULL_LATS 冻结回放会话");
    } catch (runError) {
      message.error(runError?.message || "冻结回放启动失败");
    } finally {
      setRunningId("");
    }
  }

  const scenarios = scenariosOf(catalog);
  return (
    <section className="lats-replay-panel" aria-labelledby="lats-replay-title">
      <div className="lats-replay-heading">
        <div>
          <Space wrap size={[7, 7]}>
            <span className="lats-replay-icon" aria-hidden="true"><BranchesOutlined /></span>
            <Title level={4} id="lats-replay-title">完整 LATS 冻结回放</Title>
            <Tag color="geekblue">FULL_LATS</Tag>
            <Tag color="cyan">FROZEN_REPLAY</Tag>
          </Space>
          <Paragraph>在同一个不可变观察快照上重置并比较兄弟分支试探（rollout），查看选择、反思与价值回传。</Paragraph>
        </div>
        <Button aria-label="刷新冻结回放场景" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新场景</Button>
      </div>

      <Alert
        type="info"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message="冻结算法回放，不是实时故障证据"
        description="回放不会启动故障、不会连接目标采集节点，也不会调用实时采集器。结论只在固定观察快照（snapshot）的真值边界内成立；故障实验室未启用时仍可独立运行。"
      />
      {error && (
        <Alert
          type="error"
          showIcon
          role="alert"
          message="冻结回放场景加载失败"
          description={error}
          action={<Button aria-label="重试" size="small" onClick={load}>重试</Button>}
        />
      )}

      {loading && !catalog ? (
        <div className="lats-replay-loading"><Skeleton active paragraph={{ rows: 4 }} /></div>
      ) : scenarios.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="服务端暂未提供冻结回放场景" />
      ) : (
        <div className="lats-replay-grid">
          {scenarios.map((scenario) => {
            const scenarioId = scenario.scenario_id || scenario.id;
            const unavailable = scenario.available === false || String(scenario.status || "").toUpperCase() === "DISABLED";
            return (
              <article className="lats-replay-card" key={scenarioId}>
                <div className="lats-replay-card-head">
                  <div>
                    <Text strong>{scenario.title || scenario.name || scenarioId}</Text>
                    <Paragraph>{scenario.summary || scenario.description || "使用服务端冻结观察执行可重复的 LATS 搜索。"}</Paragraph>
                  </div>
                  <Tag>{scenario.selection_policy || "UCT"}</Tag>
                </div>
                <dl className="lats-replay-facts">
                  <div><dt>执行语义</dt><dd>{scenario.execution_mode || "FULL_LATS"} · {scenario.environment_semantics || "FROZEN_REPLAY"}</dd></div>
                  <div><dt>真值边界</dt><dd>{boundaryText(scenario.truth_boundary)}</dd></div>
                </dl>
                <Button
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  disabled={unavailable || Boolean(runningId && runningId !== scenarioId)}
                  loading={runningId === scenarioId}
                  onClick={() => runScenario(scenario)}
                  aria-label={`运行冻结回放：${scenario.title || scenarioId}`}
                >
                  新建回放会话
                </Button>
              </article>
            );
          })}
        </div>
      )}

      {lastRun && (
        <div className="lats-replay-last-run" aria-live="polite">
          <Text strong>最近创建</Text>
          <Text code>{lastRun.diagnosis_id}</Text>
          {lastRun.snapshot?.snapshot_id && <Tag>观察快照 {lastRun.snapshot.snapshot_id}</Tag>}
          {lastRun.snapshot?.frozen === true && <Tag color="cyan">快照已冻结</Tag>}
        </div>
      )}
    </section>
  );
}
