import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  message,
} from "antd";
import { PlusOutlined, PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  createSchedule,
  deleteSchedule,
  listScheduleRecords,
  listSchedules,
  triggerSchedule,
  updateSchedule,
} from "../api/client";

const COLLECTORS = [
  "perf_cpu",
  "ebpf_io",
  "pyspy",
  "go_pprof",
  "memory_smaps",
  "sys_metrics",
];

export default function Schedules() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [records, setRecords] = useState(null);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [createForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listSchedules());
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
      const template = {
        name: values.task_name,
        agent_id: values.agent_id,
        target_pid: values.target_pid,
        collector_type: values.collector_type,
        sample_rate: values.sample_rate ?? 99,
        duration_sec: values.duration_sec ?? 15,
      };
      await createSchedule({
        name: values.name,
        cron_expression: values.cron_expression,
        timezone: values.timezone || "Asia/Shanghai",
        enabled: values.enabled ?? true,
        task_template: template,
      });
      message.success("计划已创建");
      createForm.resetFields();
      load();
    } catch (err) {
      message.error(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggle(record, enabled) {
    try {
      await updateSchedule(record.id, {
        name: record.name,
        cron_expression: record.cron_expression,
        timezone: record.timezone,
        task_template: record.task_template,
        enabled,
      });
      load();
    } catch (err) {
      message.error(err.message);
    }
  }

  async function handleTrigger(record) {
    try {
      const data = await triggerSchedule(record.id);
      message.success(`已触发，创建任务 ${data.task_id}`);
    } catch (err) {
      message.error(err.message);
    }
  }

  async function handleDelete(record) {
    Modal.confirm({
      title: `删除计划 ${record.name}？`,
      content: "已产生的任务与记录不会被删除。",
      onOk: async () => {
        try {
          await deleteSchedule(record.id);
          message.success("已删除");
          load();
        } catch (err) {
          message.error(err.message);
        }
      },
    });
  }

  async function handleRecords(record) {
    setRecords(record);
    setRecordsLoading(true);
    try {
      const rows = await listScheduleRecords(record.id);
      setRecords({ ...record, rows });
    } catch (err) {
      message.error(err.message);
    } finally {
      setRecordsLoading(false);
    }
  }

  const columns = [
    { title: "名称", dataIndex: "name" },
    { title: "Cron", dataIndex: "cron_expression", render: (v) => <Tag>{v}</Tag> },
    { title: "时区", dataIndex: "timezone" },
    {
      title: "下次触发",
      dataIndex: "next_run_at",
      render: (v) => (v ? new Date(v).toLocaleString() : "-"),
    },
    {
      title: "启用",
      dataIndex: "enabled",
      render: (v, record) => (
        <Switch checked={Boolean(v)} onChange={(next) => handleToggle(record, next)} />
      ),
    },
    {
      title: "操作",
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<PlayCircleOutlined />} onClick={() => handleTrigger(record)}>
            触发
          </Button>
          <Button size="small" onClick={() => handleRecords(record)}>记录</Button>
          <Button size="small" danger onClick={() => handleDelete(record)}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <Card title="计划任务 (Schedule / Cron)">
      <Button
        type="primary"
        icon={<PlusOutlined />}
        style={{ marginBottom: 16 }}
        onClick={() => {
          createForm.resetFields();
          setCreating(true);
        }}
      >
        新建计划
      </Button>
      <Table rowKey="id" loading={loading} dataSource={items} columns={columns} pagination={false} />

      <Drawer
        title="新建计划"
        open={creating}
        width={480}
        onClose={() => setCreating(false)}
      >
        <Form form={createForm} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="计划名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="cron_expression"
            label="Cron 表达式 (5 字段)"
            rules={[{ required: true }, { pattern: /^\S+ \S+ \S+ \S+ \S+$/, message: "需要 5 个字段" }]}
          >
            <Input placeholder="0 3 * * *" />
          </Form.Item>
          <Form.Item name="timezone" label="时区" initialValue="Asia/Shanghai">
            <Input />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
          <Divider>任务模板</Divider>
          <Form.Item name="task_name" label="任务名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="agent_id" label="Agent ID" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="target_pid" label="目标 PID" initialValue={1} rules={[{ required: true }]}>
            <InputNumber min={1} max={4194304} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="collector_type" label="采集器" initialValue="perf_cpu" rules={[{ required: true }]}>
            <Select options={COLLECTORS.map((v) => ({ value: v, label: v }))} />
          </Form.Item>
          <Form.Item name="sample_rate" label="采样率 (Hz)" initialValue={99}>
            <InputNumber min={1} max={10000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="duration_sec" label="时长 (秒)" initialValue={15}>
            <InputNumber min={1} max={600} style={{ width: "100%" }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting} block>
            创建
          </Button>
        </Form>
      </Drawer>

      <Drawer
        title={records ? `执行记录：${records.name}` : "执行记录"}
        open={Boolean(records)}
        width={520}
        onClose={() => setRecords(null)}
      >
        <Table
          rowKey="id"
          loading={recordsLoading}
          dataSource={records?.rows || []}
          pagination={false}
          columns={[
            { title: "计划触发时间", dataIndex: "scheduled_at", render: (v) => new Date(v).toLocaleString() },
            { title: "任务", dataIndex: "task_id", render: (v) => v || "-" },
            { title: "状态", dataIndex: "status", render: (v) => <Tag color={v === "created" ? "green" : "red"}>{v}</Tag> },
            { title: "错误", dataIndex: "error_message", render: (v) => v || "-" },
          ]}
        />
      </Drawer>
    </Card>
  );
}
