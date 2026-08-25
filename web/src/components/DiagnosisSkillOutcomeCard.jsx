import { CheckCircleOutlined, ExperimentOutlined, NodeIndexOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Space, Steps, Tag, Typography } from "antd";

const { Text } = Typography;

const CATEGORY_LABELS = {
  CPU_HOTSPOT: "CPU 热点循证诊断",
  MEMORY_GROWTH: "内存持续增长诊断",
  IO_LATENCY: "I/O 延迟诊断",
  LOCK_CONTENTION: "锁竞争诊断",
  NETWORK_DEGRADATION: "网络退化诊断",
  GC_PRESSURE: "垃圾回收压力诊断",
  FD_LEAK: "文件描述符泄漏诊断",
  DEPENDENCY_LATENCY: "依赖服务延迟诊断",
};

const STATUS_META = {
  CANDIDATE: { color: "gold", text: "候选，等待门禁" },
  ACTIVE: { color: "green", text: "已发布复用" },
  QUARANTINED: { color: "red", text: "已隔离" },
  RETIRED: { color: "default", text: "已退役" },
};

const PROBE_LABELS = {
  collect_sys_metrics: "采集系统指标",
  start_perf_profile: "采集 CPU 火焰图",
  collect_memory_profile: "采集内存剖析",
  collect_io_latency: "采集 I/O 延迟",
  collect_ebpf_io: "采集 eBPF I/O 证据",
  collect_network_metrics: "采集网络指标",
  collect_dependency_metrics: "采集依赖指标",
};

function routeText(skill) {
  const route = skill?.strategy?.probe_order || [];
  if (!route.length) return "未记录";
  return route.map((name) => PROBE_LABELS[name] || name).join(" → ");
}

export default function DiagnosisSkillOutcomeCard({
  skill,
  evaluating = false,
  onEvaluate,
  onOpenPlaza,
}) {
  if (!skill) return null;

  const status = STATUS_META[skill.status] || { color: "default", text: skill.status || "未知" };
  const gate = skill.gate_metrics || {};
  const isUpgrade = Boolean(skill.parent_skill_id);

  return (
    <Card
      className="diagnosis-skill-outcome"
      size="small"
      title={(
        <Space wrap>
          <NodeIndexOutlined />
          <span>本次诊断自动沉淀的 Skill</span>
          <Tag color={status.color}>{status.text}</Tag>
        </Space>
      )}
      extra={<Button size="small" onClick={onOpenPlaza}>打开 Skill 广场</Button>}
    >
      <Alert
        showIcon
        type={isUpgrade ? "success" : "info"}
        message={isUpgrade ? `已基于上一版优化为 v${skill.version}` : `已生成首个候选版本 v${skill.version}`}
        description="可信报告完成后，系统会自动提取取证顺序、最低证据要求和停止条件，并立即执行正例、反例与环境迁移门禁。自动生成不等于自动发布，投入复用仍需人工批准。"
      />

      <Steps
        className="diagnosis-skill-outcome-steps"
        size="small"
        responsive
        current={skill.status === "ACTIVE" ? 3 : gate.total ? 2 : 1}
        items={[
          { title: "结论已验证", description: "可信证据与取证轨迹完整" },
          { title: isUpgrade ? "Skill 已升级" : "Skill 已生成", description: `候选版本 v${skill.version || 1}` },
          { title: "门禁评测", description: gate.total ? `${gate.passed || 0}/${gate.total} 通过` : "等待评测" },
          { title: "人工发布", description: skill.status === "ACTIVE" ? "已投入复用" : "等待批准" },
        ]}
      />

      <Descriptions className="diagnosis-skill-outcome-details" size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
        <Descriptions.Item label="能力名称">
          {CATEGORY_LABELS[skill.category] || skill.category || "通用循证诊断"}
        </Descriptions.Item>
        <Descriptions.Item label="版本">v{skill.version || 1}</Descriptions.Item>
        <Descriptions.Item label="来源诊断">
          <Text code copyable>{skill.source_diagnosis_ids?.[0] || "未记录"}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="取证顺序" span={3}>{routeText(skill)}</Descriptions.Item>
        <Descriptions.Item label="最低证据数">{skill.strategy?.minimum_evidence ?? "未记录"}</Descriptions.Item>
        <Descriptions.Item label="置信度下限">
          {Number.isFinite(Number(skill.strategy?.confidence_floor))
            ? `${Math.round(Number(skill.strategy.confidence_floor) * 100)}%`
            : "未记录"}
        </Descriptions.Item>
        <Descriptions.Item label="门禁结果">
          {gate.eligible
            ? <Tag color="green">{gate.passed || 3}/{gate.total || 3} 通过，可发布</Tag>
            : <Tag color="gold">{gate.total ? `${gate.passed || 0}/${gate.total} 通过` : "尚未评测"}</Tag>}
        </Descriptions.Item>
      </Descriptions>

      <div className="diagnosis-skill-outcome-actions">
        <Space wrap>
          {skill.status === "CANDIDATE" && (
            <Button
              type="primary"
              icon={<ExperimentOutlined />}
              loading={evaluating}
              onClick={() => onEvaluate?.(skill)}
            >
              运行三类门禁评测
            </Button>
          )}
          {gate.eligible && <Tag icon={<CheckCircleOutlined />} color="success">正例、反例与环境迁移门禁已通过</Tag>}
          <Text type="secondary">发布仍需人工确认，负反馈会触发隔离或回滚。</Text>
        </Space>
      </div>
    </Card>
  );
}
