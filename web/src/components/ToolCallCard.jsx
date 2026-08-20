import { useState } from "react";
import { Card, Tag, Space, Button, Typography, Spin, Modal, Form, InputNumber, message } from "antd";
import { CheckOutlined, CloseOutlined, EditOutlined, ExperimentOutlined } from "@ant-design/icons";
import TaskVisualizationPreview from "./TaskVisualizationPreview";

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

const TOOL_LABELS = {
  get_agent_status: "查询 Agent 状态",
  collect_sys_metrics: "采集系统指标",
  start_perf_profile: "perf CPU 采样",
  start_ebpf_io_profile: "eBPF I/O 采样",
  start_pyspy_profile: "py-spy Python 采样",
};

// 与 server/app/drop_insight/tools.py 保持一致的工具元数据。
const TOOL_META = {
  get_agent_status: { description: "读取 Agent 心跳、能力与资源开销", risk: "R0" },
  collect_sys_metrics: { description: "采集主机与目标进程的低开销系统指标", risk: "R1" },
  start_perf_profile: { description: "对指定 Linux PID 执行 CPU Profile，识别热点函数", risk: "R2" },
  start_ebpf_io_profile: { description: "采集内核块设备 IO 延迟分布，确认 I/O 争抢", risk: "R2" },
  start_pyspy_profile: { description: "用 py-spy 采集 Python 用户态调用栈，定位热点", risk: "R2" },
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
export default function ToolCallCard({ tool, onApprove, onReject, onUpdateArgs, mode = "expert" }) {
  const [editOpen, setEditOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm] = Form.useForm();
  const status = tool.status;
  const needsApproval = status === "PENDING_APPROVAL";
  const isRunning = ["APPROVED", "TASK_CREATED", "RUNNING"].includes(status);
  const args = tool.arguments_json || {};
  const meta = TOOL_META[tool.tool_name] || {};
  const isExpert = mode === "expert";

  async function handleSaveArgs(values) {
    setSaving(true);
    try {
      const next = { ...args, ...values };
      for (const key of Object.keys(next)) {
        if (typeof next[key] === "number" && Number.isNaN(next[key])) delete next[key];
      }
      await onUpdateArgs(tool.tool_call_id, next);
      message.success("参数已更新");
      setEditOpen(false);
    } catch (err) {
      message.error(err.message);
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
            <Text strong>{TOOL_LABELS[tool.tool_name] || tool.tool_name}</Text>
            <Tag color={STATUS_COLORS[status] || "default"}>{status}</Tag>
            {meta.risk && <Tag color={RISK_COLORS[meta.risk] || "default"}>{meta.risk}</Tag>}
            {tool.policy_decision && <Tag>{tool.policy_decision}</Tag>}
          </Space>
          {needsApproval && (
            <Space>
              <Button size="small" icon={<EditOutlined />} onClick={() => setEditOpen(true)}>
                修改参数
              </Button>
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
            原因：{tool.policy_reason}
          </Text>
        )}

        {/* 原始参数 JSON 仅专家模式展示 */}
        {isExpert && Object.keys(args).length > 0 && (
          <Text type="secondary" style={{ fontSize: 11 }}>
            原始参数：{JSON.stringify(args)}
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
            {tool.error_message}
          </Text>
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
            pid: args.pid,
            duration_seconds: args.duration_seconds,
            sample_rate: args.sample_rate,
          }}
          onFinish={handleSaveArgs}
        >
          {tool.tool_name !== "get_agent_status" && (
            <>
              <Form.Item name="pid" label="目标 PID">
                <InputNumber min={1} max={4194304} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item name="duration_seconds" label="时长（秒）">
                <InputNumber min={1} max={60} style={{ width: "100%" }} />
              </Form.Item>
            </>
          )}
          {(tool.tool_name === "start_perf_profile" || tool.tool_name === "start_pyspy_profile") && (
            <Form.Item name="sample_rate" label="采样率（Hz）">
              <InputNumber min={1} max={999} style={{ width: "100%" }} />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </Card>
  );
}
