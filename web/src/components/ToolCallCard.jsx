import { useState } from "react";
import { Card, Tag, Space, Button, Typography, Spin, Modal, Form, InputNumber, message } from "antd";
import { CheckOutlined, CloseOutlined, EditOutlined, ExperimentOutlined } from "@ant-design/icons";
import TaskVisualizationPreview from "./TaskVisualizationPreview";
import {
  chineseDiagnosticText,
  diagnosticErrorText,
  diagnosticStatusLabel,
  diagnosticToolLabel,
  isKnownDiagnosticStatus,
  isKnownDiagnosticTool,
  isKnownPolicyDecision,
  policyDecisionLabel,
} from "../utils/diagnosisDisplay";

const { Text } = Typography;

const STATUS_COLORS = {
  PENDING_APPROVAL: "gold",
  APPROVED: "blue",
  REJECTED: "red",
  DENIED: "red",
  TASK_CREATED: "geekblue",
  RUNNING: "processing",
  COMPLETED: "green",
  FAILED: "red",
};

const RISK_COLORS = { R0: "default", R1: "blue", R2: "volcano", R3: "red" };

// 与 server/app/drop_insight/tools.py 保持一致的工具元数据。
const TOOL_META = {
  get_agent_status: { description: "读取 Agent 心跳、能力与资源开销", risk: "R0" },
  collect_sys_metrics: { description: "采集主机与目标进程的低开销系统指标", risk: "R1" },
  collect_database_diagnostics: { description: "只读采集 PostgreSQL 锁等待、阻塞关系与事务等待时长", risk: "R1" },
  start_perf_profile: { description: "对指定 Linux PID 执行 CPU Profile，识别热点函数", risk: "R2" },
  start_ebpf_io_profile: { description: "采集内核块设备 I/O 延迟分布，确认 I/O 争抢", risk: "R2" },
  start_pyspy_profile: { description: "用 py-spy 采集 Python 用户态调用栈，定位热点", risk: "R2" },
  start_jvm_profile: { description: "用 async-profiler 采集 JVM CPU 调用栈", risk: "R2" },
  collect_memory_profile: { description: "采集目标进程的 RSS、PSS 与 Swap 内存指标", risk: "R1" },
  collect_go_profile: { description: "从已登记的 pprof 端点采集 Go CPU Profile", risk: "R2" },
  start_continuous_profile: { description: "分窗口连续采集 perf 数据，用于比较热点漂移", risk: "R2" },
};

/** 把人话参数渲染成一句可读描述（方案 §6.2：目标/时长/采样率/风险）。 */
function humanReadableArgs(toolName, args) {
  const agent = args.agent_id || "未知 Agent";
  if (toolName === "get_agent_status") {
    return `查询 Agent「${agent}」的在线状态与采集能力`;
  }
  const pid = args.pid ? `PID ${args.pid}` : "未知进程";
  const parts = [`在「${agent}」上对 ${pid}`];
  if (args.duration_seconds) parts.push(`采集 ${args.duration_seconds}s`);
  if (args.sample_rate) parts.push(`采样率 ${args.sample_rate}Hz`);
  return parts.join("，") + (parts.length > 1 ? "。" : "。");
}

/**
 * 工具调用卡：Codex 式的"一个工具动作"块。
 * PENDING_APPROVAL 时显示通过/拒绝/修改参数；有任务时显示结果与内联可视化。
 * 原始参数 JSON 只在该卡的 expert 模式展示，简单模式只看人话说明。
 */
export default function ToolCallCard({ tool, onApprove, onReject, onUpdateArgs, mode = "expert", readOnly = false }) {
  const [editOpen, setEditOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm] = Form.useForm();
  const status = tool.status;
  const needsApproval = status === "PENDING_APPROVAL";
  const isRunning = ["APPROVED", "TASK_CREATED", "RUNNING"].includes(status);
  const args = tool.arguments_json || {};
  const meta = TOOL_META[tool.tool_name] || {};
  const isExpert = mode === "expert";
  const canEditDuration = Object.hasOwn(TOOL_META, tool.tool_name) && tool.tool_name !== "get_agent_status";
  const canEditSampleRate = [
    "start_perf_profile",
    "start_pyspy_profile",
    "start_jvm_profile",
    "collect_go_profile",
    "start_continuous_profile",
  ].includes(tool.tool_name);
  const hasEditableArguments = canEditDuration || canEditSampleRate;
  const rawError = String(tool.error_message || "").trim();
  const displayedError = rawError ? diagnosticErrorText(rawError) : "";
  const hasUnknownProtocolCode = (
    !isKnownDiagnosticTool(tool.tool_name)
    || !isKnownDiagnosticStatus(status)
    || (tool.policy_decision && !isKnownPolicyDecision(tool.policy_decision))
  );

  async function handleSaveArgs(values) {
    setSaving(true);
    try {
      // Agent/PID 来自服务端签发的安全目标绑定。人工审批只能收窄采样参数，
      // 不能借由修改 Tool Call 把探针转向另一个 Agent 或进程。
      const permitted = {};
      if (canEditDuration && values.duration_seconds != null) {
        permitted.duration_seconds = values.duration_seconds;
      }
      if (canEditSampleRate && values.sample_rate != null) {
        permitted.sample_rate = values.sample_rate;
      }
      const next = { ...args, ...permitted };
      for (const key of Object.keys(next)) {
        if (typeof next[key] === "number" && Number.isNaN(next[key])) delete next[key];
      }
      await onUpdateArgs(tool.tool_call_id, next);
      message.success("参数已更新");
      setEditOpen(false);
    } catch (err) {
      message.error(diagnosticErrorText(err?.message, "参数更新失败，请稍后重试"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card size="small" style={{ marginBottom: 10 }} title={null}>
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
          <Space wrap>
            <ExperimentOutlined />
            <Text strong>{diagnosticToolLabel(tool.tool_name)}</Text>
            <Tag color={STATUS_COLORS[status] || "default"}>{diagnosticStatusLabel(status, "状态未知")}</Tag>
            {meta.risk && <Tag color={RISK_COLORS[meta.risk] || "default"}>{meta.risk}</Tag>}
            {tool.policy_decision && <Tag>{policyDecisionLabel(tool.policy_decision)}</Tag>}
          </Space>
          {needsApproval && !readOnly && (
            <Space>
              {hasEditableArguments && <Button size="small" icon={<EditOutlined />} onClick={() => setEditOpen(true)}>
                修改参数
              </Button>}
              <Button
                size="small"
                type="primary"
                icon={<CheckOutlined />}
                onClick={() => onApprove(tool.tool_call_id)}
              >
                通过
              </Button>
              <Button
                size="small"
                danger
                icon={<CloseOutlined />}
                onClick={() => onReject(tool.tool_call_id)}
              >
                拒绝
              </Button>
            </Space>
          )}
        </Space>

        {/* 人话说明：要执行什么、在哪个进程、多久、风险 */}
        {meta.description && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {meta.description}
          </Text>
        )}
        {Object.keys(args).length > 0 && (
          <Text style={{ fontSize: 12 }}>{humanReadableArgs(tool.tool_name, args)}</Text>
        )}
        {tool.policy_reason && needsApproval && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            原因：{chineseDiagnosticText(tool.policy_reason)}
          </Text>
        )}

        {isRunning && (
          <Space size={6}>
            <Spin size="small" />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {status === "PENDING_APPROVAL" ? "等待审批" : "采集执行中…"}
            </Text>
          </Space>
        )}
        {tool.task_id && status === "COMPLETED" && (
          <TaskVisualizationPreview taskId={tool.task_id} />
        )}
        {tool.error_message && (
          <Text type="danger" style={{ fontSize: 12 }}>
            {displayedError}
          </Text>
        )}

        {/* 原始工具标识、协议状态、参数与未翻译错误只在折叠的专家详情中出现。 */}
        {isExpert && (Object.keys(args).length > 0 || hasUnknownProtocolCode || rawError) && (
          <details className="diagnosis-protocol-details">
            <summary>查看技术详情</summary>
            <Space direction="vertical" size={2} style={{ width: "100%", marginTop: 6 }}>
              <Text type="secondary" style={{ fontSize: 11 }}>
                工具标识：<Text code>{tool.tool_name || "未返回"}</Text>
              </Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                原始状态：<Text code>{status || "未返回"}</Text>
              </Text>
              {tool.policy_decision && (
                <Text type="secondary" style={{ fontSize: 11 }}>
                  原始策略码：<Text code>{tool.policy_decision}</Text>
                </Text>
              )}
              {Object.keys(args).length > 0 && (
                <Text type="secondary" style={{ fontSize: 11, overflowWrap: "anywhere" }}>
                  原始参数：{JSON.stringify(args)}
                </Text>
              )}
              {rawError && rawError !== displayedError && (
                <Text type="secondary" style={{ fontSize: 11, overflowWrap: "anywhere" }}>
                  原始错误：{rawError}
                </Text>
              )}
            </Space>
          </details>
        )}
      </Space>

      <Modal
        title="修改工具参数"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
        confirmLoading={saving}
        width={420}
      >
        <Form
          form={editForm}
          layout="vertical"
          initialValues={{
            duration_seconds: args.duration_seconds,
            sample_rate: args.sample_rate,
          }}
          onFinish={handleSaveArgs}
        >
          {tool.tool_name !== "get_agent_status" && (
            <>
              <Form.Item label="安全目标（不可修改）">
                <Text code>
                  {args.agent_id || "未知 Agent"}{args.pid ? ` · PID ${args.pid}` : ""}
                </Text>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    Agent 与 PID 已由服务端绑定；如需更换目标，请回到诊断范围重新选择。
                  </Text>
                </div>
              </Form.Item>
              <Form.Item name="duration_seconds" label="时长（秒）">
                <InputNumber min={1} max={60} style={{ width: "100%" }} />
              </Form.Item>
            </>
          )}
          {canEditSampleRate && (
            <Form.Item name="sample_rate" label="采样率（Hz）">
              <InputNumber min={1} max={999} style={{ width: "100%" }} />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </Card>
  );
}
