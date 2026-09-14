import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, Space, Table, Tag, message } from "antd";
import { ExperimentOutlined } from "@ant-design/icons";
import { listFixVerifications, verifyDiagnosisFix } from "../api/client";
import { verificationStatusLabel } from "../utils/diagnosisDisplay";

const FIX_OUTCOME_LABELS = {
  VERIFIED: "修复验证通过",
  REJECTED: "修复验证未通过",
};

function fixOutcomeLabel(value) {
  const key = String(value || "").toUpperCase();
  return FIX_OUTCOME_LABELS[key] || verificationStatusLabel(value, "验证状态未知");
}

/**
 * 修复前后验证面板：输入 before/after 任务 ID，调用 /fix/verify，
 * 主界面显示中文验证结论；稳定协议码仍可在接口与审计数据中使用。
 */
export default function FixVerificationPanel({ diagnosisId, canVerify = true }) {
  const [form] = Form.useForm();
  const [records, setRecords] = useState([]);
  const [verifying, setVerifying] = useState(false);
  const [lastResult, setLastResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);

  const load = useCallback(async () => {
    if (!diagnosisId) return;
    setLoading(true);
    setLoadError(false);
    try {
      setRecords(await listFixVerifications(diagnosisId));
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [diagnosisId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleVerify(values) {
    setVerifying(true);
    try {
      const result = await verifyDiagnosisFix(diagnosisId, {
        before_task_id: values.before_task_id,
        after_task_id: values.after_task_id,
        fix_summary: values.fix_summary || "",
      });
      setLastResult(result);
      message.success(`验证结论：${fixOutcomeLabel(result.outcome)}`);
      form.resetFields();
      load();
    } catch (err) {
      message.error(err.message);
    } finally {
      setVerifying(false);
    }
  }

  return (
    <Card title="修复前后验证" size="small" style={{ marginTop: 16 }}>
      <Alert showIcon type={loadError ? "error" : "info"}
        message={loading ? "正在核对修复记录" : loadError ? "修复记录读取失败，当前状态未确认" : records.length ? "已有修复复测记录，请逐条查看结果与适用范围" : "尚无修复复测记录：故障是否解决未验证"}
        description="诊断工具负责取证，不会自动修改业务代码。只有明确实施修复并完成可比复测，才能评价修复效果。当前自动对比支持 TopN 热点变化，不能替代全部业务指标验收。"
        action={loadError ? <Button onClick={load}>重试</Button> : null} style={{ marginBottom: 12 }} />
      {canVerify && <>
      <Alert
        showIcon
        type="info"
        message="用同一目标、同一采集器和相同负载分别采集修复前、修复后任务"
        description="先在任务面板复制根因确认时的任务 ID；应用修复后，以相同参数重新采集并复制新任务 ID。两次任务都必须完成分析并包含 TopN 热点数据。"
        style={{ marginBottom: 12 }}
      />
      <Form form={form} layout="inline" onFinish={handleVerify} style={{ marginBottom: 12 }}>
        <Form.Item name="before_task_id" label="修复前任务" rules={[{ required: true, message: "请输入修复前任务 ID" }]}>
          <Input placeholder="task_xxx_before" style={{ width: 220 }} />
        </Form.Item>
        <Form.Item name="after_task_id" label="修复后任务" rules={[{ required: true, message: "请输入修复后任务 ID" }]}>
          <Input placeholder="task_xxx_after" style={{ width: 220 }} />
        </Form.Item>
        <Form.Item name="fix_summary" label="修复说明">
          <Input placeholder="例如：缓存序列化结果" style={{ width: 220 }} />
        </Form.Item>
        <Button type="primary" icon={<ExperimentOutlined />} htmlType="submit" loading={verifying}>
          对比验证
        </Button>
      </Form>
      </>}

      {lastResult && (
        <Alert
          type={lastResult.outcome === "VERIFIED" ? "success" : "error"}
          showIcon
          message={`${fixOutcomeLabel(lastResult.outcome)} — ${lastResult.comparison?.reason || ""}`}
          style={{ marginBottom: 12 }}
        />
      )}

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={records}
        pagination={false}
        columns={[
          {
            title: "结论",
            dataIndex: "outcome",
            render: (v) => (
              <Tag color={v === "VERIFIED" ? "green" : "red"}>{fixOutcomeLabel(v)}</Tag>
            ),
          },
          { title: "修复前", dataIndex: "before_task_id" },
          { title: "修复后", dataIndex: "after_task_id" },
          {
            title: "说明",
            dataIndex: "comparison",
            render: (v) => <Space>{v?.reason || "-"}</Space>,
          },
        ]}
      />
    </Card>
  );
}
