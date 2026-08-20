import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, Space, Table, Tag, message } from "antd";
import { ExperimentOutlined } from "@ant-design/icons";
import { listFixVerifications, verifyDiagnosisFix } from "../api/client";

/**
 * 修复前后验证面板：输入 before/after 任务 ID，调用 /fix/verify，
 * 显示 VERIFIED / REJECTED 结论与历史记录（guide #4.6）。
 */
export default function FixVerificationPanel({ diagnosisId }) {
  const [form] = Form.useForm();
  const [records, setRecords] = useState([]);
  const [verifying, setVerifying] = useState(false);
  const [lastResult, setLastResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!diagnosisId) return;
    setLoading(true);
    try {
      setRecords(await listFixVerifications(diagnosisId));
    } catch {
      setRecords([]);
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
      message.success(`验证结论：${result.outcome}`);
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

      {lastResult && (
        <Alert
          type={lastResult.outcome === "VERIFIED" ? "success" : "error"}
          showIcon
          message={`${lastResult.outcome} — ${lastResult.comparison?.reason || ""}`}
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
              <Tag color={v === "VERIFIED" ? "green" : "red"}>{v}</Tag>
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
