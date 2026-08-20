import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  message,
} from "antd";
import { PlusOutlined, StopOutlined, SyncOutlined } from "@ant-design/icons";
import {
  aggregateCompositeTask,
  cancelCompositeTask,
  createCompositeTask,
  getCompositeTask,
  listCompositeTasks,
} from "../api/client";

const COLLECTORS = ["perf_cpu", "ebpf_io", "pyspy", "go_pprof", "memory_smaps", "sys_metrics"];
const STRATEGIES = [
  { value: "ALL_REQUIRED", label: "ALL_REQUIRED（必选子任务全成功）" },
  { value: "BEST_EFFORT", label: "BEST_EFFORT（至少一个成功）" },
  { value: "QUORUM", label: "QUORUM（达到成功数）" },
];
const STATUS_COLORS = {
  SUCCEEDED: "green",
  PARTIAL: "orange",
  RUNNING: "blue",
  PENDING: "default",
  FAILED: "red",
  CANCELLED: "default",
};

export default function Composites() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [createForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listCompositeTasks());
    } catch (err) {
      message.error(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(values) {
    setSubmitting(true);
    try {
      const children = (values.children || []).map((child) => ({
        role: child.role || "required",
        task_template: {
          name: child.name,
          agent_id: child.agent_id,
          target_pid: child.target_pid,
          collector_type: child.collector_type,
          sample_rate: child.sample_rate ?? 99,
          duration_sec: child.duration_sec ?? 15,
        },
      }));
      await createCompositeTask({
        name: values.name,
        strategy: values.strategy,
        required_success_count: values.required_success_count ?? null,
        children,
      });
      message.success("复合任务已创建");
      createForm.resetFields();
      load();
    } catch (err) {
      message.error(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function openDetail(record) {
    setDetail({ id: record.id, name: record.name });
    setDetailLoading(true);
    try {
      setDetail(await getCompositeTask(record.id));
    } catch (err) {
      message.error(err.message);
    } finally {
      setDetailLoading(false);
    }
  }

  async function handleAggregate() {
    if (!detail) return;
    try {
      const data = await aggregateCompositeTask(detail.id);
      message.success(`聚合状态：${data.status}`);
      openDetail({ id: detail.id, name: detail.name });
    } catch (err) {
      message.error(err.message);
    }
  }

  async function handleCancel() {
    if (!detail) return;
    Modal.confirm({
      title: "取消复合任务？",
      content: "所有非终态子任务也会被取消。",
      onOk: async () => {
        try {
          await cancelCompositeTask(detail.id);
          message.success("已取消");
          openDetail({ id: detail.id, name: detail.name });
          load();
        } catch (err) {
          message.error(err.message);
        }
      },
    });
  }

  const columns = [
    { title: "名称", dataIndex: "name" },
    { title: "策略", dataIndex: "strategy", render: (v) => <Tag>{v}</Tag> },
    {
      title: "状态",
      dataIndex: "status",
      render: (v) => <Tag color={STATUS_COLORS[v] || "default"}>{v}</Tag>,
    },
    {
      title: "操作",
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => openDetail(record)}>详情</Button>
        </Space>
      ),
    },
  ];

  return (
    <Card title="复合任务 (Composite Task / DAG)">
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={() => setCreating(true)}>
        新建复合任务
      </Button>
      <Table rowKey="id" loading={loading} dataSource={items} columns={columns} pagination={false} />

      <Drawer title="新建复合任务" open={creating} width={560} onClose={() => setCreating(false)}>
        <Form form={createForm} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="strategy" label="聚合策略" initialValue="ALL_REQUIRED" rules={[{ required: true }]}>
            <Select options={STRATEGIES} />
          </Form.Item>
          <Form.Item name="required_success_count" label="QUORUM 成功数">
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.List name="children">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <Card key={field.key} size="small" style={{ marginBottom: 8 }}>
                    <Space style={{ display: "flex" }} align="start" wrap>
                      <Form.Item name={[field.name, "name"]} label="任务名" rules={[{ required: true }]}>
                        <Input />
                      </Form.Item>
                      <Form.Item name={[field.name, "role"]} label="角色" initialValue="required">
                        <Select options={[
                          { value: "required", label: "required" },
                          { value: "optional", label: "optional" },
                        ]} />
                      </Form.Item>
                      <Form.Item name={[field.name, "agent_id"]} label="Agent" rules={[{ required: true }]}>
                        <Input />
                      </Form.Item>
                      <Form.Item name={[field.name, "target_pid"]} label="PID" initialValue={1}>
                        <InputNumber min={1} max={4194304} />
                      </Form.Item>
                      <Form.Item name={[field.name, "collector_type"]} label="采集器" initialValue="perf_cpu">
                        <Select options={COLLECTORS.map((v) => ({ value: v, label: v }))} />
                      </Form.Item>
                      <Button type="text" danger onClick={() => remove(field.name)}>移除</Button>
                    </Space>
                  </Card>
                ))}
                <Button type="dashed" block onClick={() => add({ role: "required", collector_type: "perf_cpu", target_pid: 1 })}>
                  添加子任务
                </Button>
              </>
            )}
          </Form.List>
          <Button type="primary" htmlType="submit" block style={{ marginTop: 16 }} loading={submitting}>
            创建
          </Button>
        </Form>
      </Drawer>

      <Drawer
        title={detail?.name || "复合任务详情"}
        open={Boolean(detail)}
        width={560}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="ID">{detail.id}</Descriptions.Item>
              <Descriptions.Item label="策略">{detail.strategy}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLORS[detail.status] || "default"}>{detail.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="成功数要求">{detail.required_success_count ?? "-"}</Descriptions.Item>
            </Descriptions>
            <Space style={{ marginBottom: 16 }}>
              <Button icon={<SyncOutlined />} onClick={handleAggregate}>刷新聚合</Button>
              <Button danger icon={<StopOutlined />} onClick={handleCancel}>取消</Button>
            </Space>
            <Table
              rowKey="id"
              size="small"
              loading={detailLoading}
              dataSource={detail.items || []}
              pagination={false}
              columns={[
                { title: "任务", dataIndex: "task_id", render: (v) => v || "-" },
                { title: "角色", dataIndex: "role" },
                { title: "状态", dataIndex: "status", render: (v) => <Tag color={v === "succeeded" ? "green" : v === "failed" ? "red" : "blue"}>{v}</Tag> },
              ]}
            />
          </>
        )}
      </Drawer>
    </Card>
  );
}
