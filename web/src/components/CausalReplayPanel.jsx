import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { CheckCircleOutlined, ExperimentOutlined, StopOutlined } from "@ant-design/icons";

const { Paragraph, Text } = Typography;
const WINDOW_LABELS = {
  baseline: "正常基线",
  incident: "故障窗口",
  intervention: "单变量干预",
  recovery: "清理恢复",
};

const STATUS_LABELS = {
  WAITING_APPROVAL: "等待人工审批",
  APPROVED: "已批准，等待证据",
  REJECTED: "已拒绝",
  EVALUATED: "已完成因果裁决",
};

const VERDICT_LABELS = {
  CAUSALLY_VERIFIED: "因果关系已证实",
  SUPPORTED_SINGLE_NODE: "单机交替实验支持该假设",
  CAUSALLY_REFUTED: "当前假设已被推翻",
  INCONCLUSIVE: "效应不明确",
  INSUFFICIENT_EVIDENCE: "证据不足",
};

function targetDefaults(target = {}) {
  return {
    design_mode: "SINGLE_NODE_CROSSOVER",
    treatment_agent_id: target.agent_id || "",
    treatment_pid: target.pid || undefined,
    treatment_service: target.service || "",
    control_agent_id: "",
    control_pid: undefined,
    control_service: target.service || "",
    duration_seconds: 60,
  };
}

function planRows(plan) {
  if (!plan) return [];
  const policy = plan.resource_policy;
  return [
    ["实验设计", plan.design_mode === "SINGLE_NODE_CROSSOVER" ? "单机交替验证" : "双节点并行对照"],
    ["待验证假设", plan.hypothesis],
    ["唯一干预变量", plan.treatment],
    ["对照组保持不变", plan.control],
    ["主指标", plan.primary_metric],
    ["安全清理", plan.cleanup],
    ["资源准入", policy
      ? `${policy.minimum_online_agents} 个在线 Agent；每个 Worker 至少 ${policy.minimum_available_memory_mb_per_worker}MB 可用内存；${policy.admission_note}`
      : "旧实验未记录资源准入策略"],
  ];
}

function measurementDefaults() {
  return Object.keys(WINDOW_LABELS).reduce((result, name) => ({
    ...result,
    [`${name}_treatment`]: undefined,
    [`${name}_control`]: undefined,
    [`${name}_task_ids`]: "",
    [`${name}_evidence_refs`]: "",
  }), {});
}

function splitRefs(value) {
  return String(value || "").split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

function measurementPayload(values) {
  return Object.keys(WINDOW_LABELS).reduce((result, name) => ({
    ...result,
    [name]: {
      treatment: values[`${name}_treatment`],
      control: values[`${name}_control`],
      task_ids: splitRefs(values[`${name}_task_ids`]),
      evidence_refs: splitRefs(values[`${name}_evidence_refs`]),
    },
  }), {});
}

export default function CausalReplayPanel({
  diagnosis,
  hypotheses = [],
  experiments = [],
  cases = [],
  readOnly = false,
  onCreate,
  onDecision,
  onEvaluate,
}) {
  const [form] = Form.useForm();
  const [measurementForm] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const designMode = Form.useWatch("design_mode", form) || "SINGLE_NODE_CROSSOVER";
  const availableCases = useMemo(
    () => cases.filter((item) => item.implementation_status === "AVAILABLE"),
    [cases],
  );
  const current = experiments[0] || null;
  const plan = current?.plan;

  useEffect(() => {
    form.setFieldsValue(targetDefaults(diagnosis?.target));
  }, [diagnosis?.diagnosis_id, diagnosis?.target, form]);

  async function createExperiment() {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await onCreate({
        hypothesis_id: values.hypothesis_id,
        case_id: values.case_id,
        design_mode: values.design_mode,
        duration_seconds: values.duration_seconds,
        treatment_target: {
          agent_id: values.treatment_agent_id,
          pid: values.treatment_pid,
          service: values.treatment_service || undefined,
        },
        control_target: values.design_mode === "DUAL_NODE_CONTROL" ? {
          agent_id: values.control_agent_id,
          pid: values.control_pid,
          service: values.control_service || undefined,
        } : undefined,
      });
      message.success("反事实实验方案已创建，等待人工审批");
    } catch (error) {
      if (error?.errorFields) return;
      message.error(error?.message || "创建反事实实验失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function decide(approved) {
    try {
      setSubmitting(true);
      await onDecision(current.experiment_id, {
        approved,
        reason: approved
          ? "用户已核对处理组、对照组、单一干预和自动清理范围"
          : "用户拒绝当前实验范围或风险",
      });
      message.success(approved ? "实验已批准，可按所选资源模式开始取证" : "实验已拒绝，不会执行干预");
    } catch (error) {
      message.error(error?.message || "审批失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function evaluate() {
    try {
      const values = await measurementForm.validateFields();
      setSubmitting(true);
      await onEvaluate(current.experiment_id, measurementPayload(values));
      message.success("四窗口证据已裁决");
    } catch (error) {
      if (error?.errorFields) return;
      message.error(error?.message || "因果裁决失败");
    } finally {
      setSubmitting(false);
    }
  }

  const measurementColumns = [
    { title: "窗口", dataIndex: "label", width: 120 },
    {
      title: "处理组指标", dataIndex: "name", width: 150,
      render: (name) => <Form.Item name={`${name}_treatment`} rules={[{ required: true, message: "必填" }]} noStyle><InputNumber aria-label={`${WINDOW_LABELS[name]}处理组指标`} style={{ width: "100%" }} /></Form.Item>,
    },
    ...(plan?.design_mode === "DUAL_NODE_CONTROL" ? [{
      title: "对照组指标", dataIndex: "name", width: 150,
      render: (name) => <Form.Item name={`${name}_control`} rules={[{ required: true, message: "必填" }]} noStyle><InputNumber aria-label={`${WINDOW_LABELS[name]}对照组指标`} style={{ width: "100%" }} /></Form.Item>,
    }] : []),
    {
      title: "任务 ID / 证据引用", dataIndex: "name",
      render: (name) => (
        <Space.Compact block>
          <Form.Item name={`${name}_task_ids`} noStyle><Input aria-label={`${WINDOW_LABELS[name]}任务ID`} placeholder="task_id，可逗号分隔" /></Form.Item>
          <Form.Item name={`${name}_evidence_refs`} noStyle><Input aria-label={`${WINDOW_LABELS[name]}证据引用`} placeholder="evidence_ref，可逗号分隔" /></Form.Item>
        </Space.Compact>
      ),
    },
  ];

  return (
    <Card
      size="small"
      className="causal-replay-panel"
      title={<Space><ExperimentOutlined /><span>资源自适应反事实根因验证</span></Space>}
      extra={current && <Tag color={current.status === "EVALUATED" ? "green" : "gold"}>{STATUS_LABELS[current.status] || current.status}</Tag>}
    >
      <Alert
        type="info"
        showIcon
        message="这不是删除上下文，也不是让 AI 自己改生产环境"
        description="默认适配 2 核 4GB 节点：同一节点依次比较基线、故障、干预和恢复，不常驻第二套负载；有空闲节点时再升级为双节点对照。任何干预都来自白名单开关并须先由人批准。"
      />

      {!current && !readOnly && (
        <Form form={form} layout="vertical" className="causal-replay-form">
          <div className="causal-replay-form-grid">
            <Form.Item name="design_mode" label="实验设计" rules={[{ required: true }]}>
              <Select options={[
                { value: "SINGLE_NODE_CROSSOVER", label: "单机交替验证（推荐，省资源）" },
                { value: "DUAL_NODE_CONTROL", label: "双节点并行对照（证据更强）" },
              ]} />
            </Form.Item>
            <Form.Item name="hypothesis_id" label="选择待验证假设" rules={[{ required: true, message: "请选择假设" }]}>
              <Select options={hypotheses.map((item) => ({ value: item.hypothesis_id, label: item.statement }))} />
            </Form.Item>
            <Form.Item name="case_id" label="选择对照实验模板" rules={[{ required: true, message: "请选择实验模板" }]}>
              <Select options={availableCases.map((item) => ({ value: item.case_id, label: `${item.case_id} · ${item.title}` }))} />
            </Form.Item>
            <Form.Item name="duration_seconds" label="每个窗口时长（秒）" rules={[{ required: true }]}>
              <InputNumber min={15} max={600} style={{ width: "100%" }} />
            </Form.Item>
          </div>
          <Alert
            type={designMode === "SINGLE_NODE_CROSSOVER" ? "success" : "warning"}
            showIcon
            message={designMode === "SINGLE_NODE_CROSSOVER" ? "当前资源建议：单机交替验证" : "双节点模式必须先通过资源预检"}
            description={designMode === "SINGLE_NODE_CROSSOVER"
              ? "只需 1 个在线 Agent 和至少 512MB 可用内存，四个窗口串行执行；结论最高为单机支持，不能冒充双机因果确认。"
              : "需要 2 个独立且空闲的 Agent，每个 Worker 至少 768MB 可用内存、采集能力和负载一致；不满足时请切回单机模式。"}
            style={{ marginBottom: 16 }}
          />
          <div className="causal-replay-targets">
            <Card size="small" title={designMode === "SINGLE_NODE_CROSSOVER" ? "目标节点：分时执行唯一干预" : "处理组：执行唯一干预"}>
              <Form.Item name="treatment_agent_id" label="Agent" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item name="treatment_pid" label="PID" rules={[{ required: true }]}><InputNumber min={1} style={{ width: "100%" }} /></Form.Item>
              <Form.Item name="treatment_service" label="服务"><Input /></Form.Item>
            </Card>
            {designMode === "DUAL_NODE_CONTROL" && (
              <Card size="small" title="对照组：保持原状">
                <Form.Item name="control_agent_id" label="Agent" rules={[{ required: true }]}><Input /></Form.Item>
                <Form.Item name="control_pid" label="PID" rules={[{ required: true }]}><InputNumber min={1} style={{ width: "100%" }} /></Form.Item>
                <Form.Item name="control_service" label="服务"><Input /></Form.Item>
              </Card>
            )}
          </div>
          <Button type="primary" icon={<ExperimentOutlined />} onClick={createExperiment} loading={submitting}>生成待审批实验方案</Button>
        </Form>
      )}

      {current && (
        <div className="causal-replay-current">
          <Steps
            size="small"
            current={current.status === "WAITING_APPROVAL" ? 0 : current.status === "APPROVED" ? 1 : 2}
            status={current.status === "REJECTED" ? "error" : "process"}
            items={[{ title: "人工审批" }, { title: "四窗口取证" }, { title: "因果裁决" }]}
          />
          <Descriptions size="small" column={1} bordered items={planRows(plan).map(([label, children]) => ({ key: label, label, children }))} />

          {current.status === "WAITING_APPROVAL" && !readOnly && (
            <Space wrap>
              <Button type="primary" icon={<CheckCircleOutlined />} loading={submitting} onClick={() => decide(true)}>核对无误并批准</Button>
              <Button danger icon={<StopOutlined />} loading={submitting} onClick={() => decide(false)}>拒绝实验</Button>
              <Text type="secondary">批准记录、理由和操作者会写入不可变事件流。</Text>
            </Space>
          )}

          {current.status === "APPROVED" && !readOnly && (
            <Collapse
              defaultActiveKey={["measurements"]}
              items={[{
                key: "measurements",
                label: "录入已完成采集任务的四窗口证据",
                children: (
                  <Form form={measurementForm} initialValues={measurementDefaults()}>
                    <Paragraph type="secondary">指标必须统一为越低越好，例如 P99 延迟或 CPU 压力；每行至少填写任务 ID 或证据引用之一。单机模式无需填写对照组指标。</Paragraph>
                    <Table rowKey="name" size="small" pagination={false} scroll={{ x: 760 }} columns={measurementColumns} dataSource={Object.entries(WINDOW_LABELS).map(([name, label]) => ({ name, label }))} />
                    <Button type="primary" onClick={evaluate} loading={submitting} style={{ marginTop: 12 }}>根据透明门槛计算结论</Button>
                  </Form>
                ),
              }]}
            />
          )}

          {current.status === "EVALUATED" && current.judgement && (
            <Alert
              type={current.judgement.verdict === "CAUSALLY_VERIFIED" ? "success" : current.judgement.verdict === "CAUSALLY_REFUTED" ? "error" : "warning"}
              showIcon
              message={`${VERDICT_LABELS[current.judgement.verdict] || current.judgement.verdict} · 置信度 ${Math.round((current.judgement.confidence || 0) * 100)}%`}
              description={(
                <div>
                  <Paragraph>{current.judgement.reason}</Paragraph>
                  <Text code>差中之差 {current.judgement.metrics?.difference_in_differences ?? "-"}</Text>
                  <Text type="secondary"> 结论来自处理组与对照组的四窗口规则，不由模型自由生成。</Text>
                </div>
              )}
            />
          )}
        </div>
      )}
    </Card>
  );
}
