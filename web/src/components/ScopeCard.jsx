import { useEffect, useMemo, useState } from "react";
import { Alert, AutoComplete, Button, Card, Col, Form, Input, Row, Select, Space, Typography, message } from "antd";
import { AimOutlined, QuestionCircleOutlined } from "@ant-design/icons";
import { listAgents, listTopProcesses } from "../api/client";

const { Text } = Typography;

function localDateTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function defaultWindow() {
  const end = new Date();
  return { start: localDateTime(new Date(end.getTime() - 5 * 60_000)), end: localDateTime(end) };
}

/** Bind a natural-language problem to a real Agent/PID/time range. */
export default function ScopeCard({ questions, onClarify, submitting, initialTarget = {}, initialTimeRange = {} }) {
  const [form] = Form.useForm();
  const [agents, setAgents] = useState([]);
  const [processes, setProcesses] = useState([]);
  const [loadingTargets, setLoadingTargets] = useState(false);
  const required = useMemo(() => new Set((questions || []).map((item) => item.question_id)), [questions]);

  useEffect(() => {
    const range = defaultWindow();
    form.setFieldsValue({
      service: initialTarget.service || "",
      environment: initialTarget.environment || "",
      agent_id: initialTarget.agent_id || undefined,
      pid: initialTarget.pid || undefined,
      start: initialTimeRange.start ? localDateTime(initialTimeRange.start) : range.start,
      end: initialTimeRange.end ? localDateTime(initialTimeRange.end) : range.end,
    });
  }, [form, initialTarget, initialTimeRange]);

  useEffect(() => {
    let active = true;
    setLoadingTargets(true);
    Promise.all([listAgents(), listTopProcesses(30)])
      .then(([agentRows, processRows]) => {
        if (!active) return;
        setAgents((agentRows || []).filter((item) => item.status === "ONLINE"));
        setProcesses(processRows || []);
      })
      .catch(() => active && (setAgents([]), setProcesses([])))
      .finally(() => active && setLoadingTargets(false));
    return () => { active = false; };
  }, []);

  function useRecommendedTarget() {
    // /api/top-processes is discovered on the control host. Prefer the
    // control-host Agent so the suggested PID is guaranteed to be visible in
    // the same PID namespace; remote worker PIDs must be entered explicitly.
    const agent = agents.find((item) => (
      item.id === "control-campaign-agent"
      || item.hostname === "control-campaign-agent"
      || item.id === "agent_native_cpp"
    )) || agents[0];
    const process = processes[0];
    const values = form.getFieldsValue();
    const range = defaultWindow();
    form.setFieldsValue({
      service: values.service || process?.comm || "demo-service",
      environment: values.environment || "demo",
      agent_id: values.agent_id || agent?.id,
      pid: values.pid || process?.pid,
      start: values.start || range.start,
      end: values.end || range.end,
    });
    if (!agent || !process) {
      message.warning("没有完整的在线 Agent/PID 候选，请先在任务面板确认 Agent 在线");
    } else if (agent.id !== "control-campaign-agent" && agent.hostname !== "control-campaign-agent") {
      message.info("已填入当前候选；多机环境请确认该 PID 确实属于所选 Agent");
    }
  }

  async function handleSubmit(values) {
    const start = new Date(values.start);
    const end = new Date(values.end);
    if (end <= start) {
      message.error("结束时间必须晚于开始时间");
      return;
    }
    await onClarify({
      target: {
        service: values.service.trim(),
        environment: values.environment,
        agent_id: values.agent_id,
        pid: Number(values.pid),
      },
      time_range: { start: start.toISOString(), end: end.toISOString(), timezone: "Asia/Shanghai" },
    });
    message.success("范围已确认，AI 开始生成可证伪假设和取证计划");
  }

  const rule = (id, label) => ({
    required: required.has(id) || ["target.agent_id", "target.pid"].includes(id),
    message: `请选择或填写${label}`,
  });

  return (
    <Card size="small" style={{ marginBottom: 12, background: "#fffaf0", borderColor: "#ffe7ba" }}>
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
          <Space><QuestionCircleOutlined style={{ color: "#fa8c16" }} /><Text strong>AI 需要确认诊断范围</Text></Space>
          <Button icon={<AimOutlined />} onClick={useRecommendedTarget} loading={loadingTargets}>使用在线演示目标</Button>
        </Space>
        <Alert type="info" showIcon message="这一步不是让 AI 猜机器" description="服务和环境用于理解业务；Agent 和 PID 决定去哪里采集；时间窗用于保证证据与故障发生时间一致。带 * 的字段补齐后才会进入真实取证。" />
        {(questions || []).length > 0 && <ul style={{ margin: 0, paddingLeft: 20 }}>{questions.map((q, i) => <li key={q.question_id || i}>{q.prompt}</li>)}</ul>}
        <Form form={form} layout="vertical" onFinish={handleSubmit} requiredMark>
          <Row gutter={12}>
            <Col xs={24} md={12} xl={8}><Form.Item name="service" label="服务" rules={[rule("target.service", "服务")]}><Input placeholder="例如 order-service" /></Form.Item></Col>
            <Col xs={24} md={12} xl={8}><Form.Item name="environment" label="环境" rules={[rule("target.environment", "环境")]}><Select placeholder="选择环境" options={["development", "staging", "production", "demo"].map((value) => ({ value, label: value }))} /></Form.Item></Col>
            <Col xs={24} md={12} xl={8}><Form.Item name="agent_id" label="在线 Agent" rules={[rule("target.agent_id", "在线 Agent")]}><Select loading={loadingTargets} showSearch optionFilterProp="label" placeholder={agents.length ? "选择采集节点" : "暂无在线 Agent"} options={agents.map((agent) => ({ value: agent.id, label: `${agent.hostname || agent.id} · ${agent.id}` }))} /></Form.Item></Col>
            <Col xs={24} md={12} xl={8}><Form.Item name="pid" label="目标 PID" rules={[rule("target.pid", "目标 PID")]}><AutoComplete placeholder={processes.length ? "选择候选进程或直接输入 PID" : "输入正整数 PID"} options={processes.map((item) => ({ value: String(item.pid), label: `${item.pid} · ${item.comm} · CPU ${item.cpu_percent}%` }))} filterOption={(input, option) => String(option?.label || "").toLowerCase().includes(input.toLowerCase())} /></Form.Item></Col>
            <Col xs={24} md={12} xl={8}><Form.Item name="start" label="开始时间" rules={[{ required: true, message: "请选择开始时间" }]}><Input type="datetime-local" /></Form.Item></Col>
            <Col xs={24} md={12} xl={8}><Form.Item name="end" label="结束时间" rules={[{ required: true, message: "请选择结束时间" }]}><Input type="datetime-local" /></Form.Item></Col>
          </Row>
          <Button type="primary" htmlType="submit" loading={submitting}>确认范围并开始取证</Button>
        </Form>
      </Space>
    </Card>
  );
}
