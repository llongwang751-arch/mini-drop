import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Col, Collapse, Descriptions, Empty, Progress, Row, Space, Statistic, Steps, Tag, Typography, message } from "antd";
import { DeploymentUnitOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import {
  evaluateDiagnosticSkill,
  getDiagnosticSkill,
  listDiagnosticSkills,
  publishDiagnosticSkill,
  quarantineDiagnosticSkill,
  rollbackDiagnosticSkill,
} from "../api/client";

const { Paragraph, Text } = Typography;

const STATUS_COLOR = {
  CANDIDATE: "processing",
  ACTIVE: "success",
  QUARANTINED: "error",
  RETIRED: "default",
};

const GATE_LABEL = {
  POSITIVE_REPLAY: "相似正例重放",
  MISLEADING_NEGATIVE: "误导反例拒答",
  ENVIRONMENT_DRIFT: "环境漂移降级",
};

export default function SkillEvolutionPanel() {
  const [skills, setSkills] = useState([]);
  const [details, setDetails] = useState({});
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSkills(await listDiagnosticSkills());
    } catch (error) {
      message.error(error.message || "读取诊断技能失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function openDetail(skillId) {
    if (details[skillId]) return;
    try {
      const detail = await getDiagnosticSkill(skillId);
      setDetails((current) => ({ ...current, [skillId]: detail }));
    } catch (error) {
      message.error(error.message);
    }
  }

  async function action(fn, success) {
    try {
      await fn();
      message.success(success);
      setDetails({});
      await load();
    } catch (error) {
      message.error(error.message);
    }
  }

  const candidateCount = skills.filter((item) => item.status === "CANDIDATE").length;
  const activeCount = skills.filter((item) => item.status === "ACTIVE").length;
  const quarantinedCount = skills.filter((item) => item.status === "QUARANTINED").length;
  const activationCount = skills.reduce((total, item) => total + (item.activation_count || 0), 0);

  return (
    <Card
      size="small"
      title={<Space><DeploymentUnitOutlined />诊断策略自进化</Space>}
      extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>}
    >
      <Alert
        showIcon
        type="info"
        message="系统学习的是诊断策略，不是未经验证的答案"
        description="结论经可信证据校验且人工确认正确后，系统才抽取候选技能；候选必须通过相似正例、误导反例和环境漂移门禁，发布后才能影响新诊断。连续负反馈会自动隔离，旧版本可回滚。"
      />
      <Row gutter={[12, 12]} style={{ marginTop: 16 }}>
        <Col xs={12} md={6}><Card size="small"><Statistic title="候选策略" value={candidateCount} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="已发布策略" value={activeCount} valueStyle={{ color: "#389e0d" }} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="真实复用次数" value={activationCount} valueStyle={{ color: "#1677ff" }} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title="已隔离策略" value={quarantinedCount} valueStyle={{ color: quarantinedCount ? "#cf1322" : undefined }} /></Card></Col>
      </Row>
      <Steps
        style={{ margin: "20px 0" }}
        responsive
        size="small"
        items={[
          { title: "验证轨迹", description: "证据完整 + 人工确认" },
          { title: "候选技能", description: "提取探针顺序与停止条件" },
          { title: "三类门禁", description: "正例 / 反例 / 漂移" },
          { title: "发布复用", description: "新事故显示命中理由" },
          { title: "监控回滚", description: "负迁移自动隔离" },
        ]}
      />
      {skills.length === 0 ? (
        <Empty description="还没有候选技能。完成一次有可信证据的诊断并选择“结论正确”后，系统会自动生成候选。" />
      ) : (
        <Collapse
          onChange={(keys) => keys.forEach(openDetail)}
          items={skills.map((skill) => {
            const detail = details[skill.skill_id];
            const metrics = skill.gate_metrics || {};
            const route = skill.strategy?.probe_order || [];
            return {
              key: skill.skill_id,
              label: (
                <Space wrap>
                  <Text strong>{skill.category}</Text>
                  <Tag>v{skill.version}</Tag>
                  <Tag color={STATUS_COLOR[skill.status]}>{skill.status}</Tag>
                  <Text type="secondary">{route.join(" → ") || "待提取路线"}</Text>
                </Space>
              ),
              children: (
                <Space direction="vertical" style={{ width: "100%" }} size={12}>
                  <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
                    <Descriptions.Item label="来源诊断">{(skill.source_diagnosis_ids || []).join("、")}</Descriptions.Item>
                    <Descriptions.Item label="适用环境">{skill.trigger?.environment || "*"}</Descriptions.Item>
                    <Descriptions.Item label="最少证据">{skill.strategy?.minimum_evidence || 1}</Descriptions.Item>
                    <Descriptions.Item label="停止规则">{skill.strategy?.stop_rule || "-"}</Descriptions.Item>
                    <Descriptions.Item label="真实复用">{skill.activation_count || 0} 次</Descriptions.Item>
                    <Descriptions.Item label="反馈结果">
                      正确 {skill.correct_outcome_count || 0} / 错误 {skill.wrong_outcome_count || 0}
                    </Descriptions.Item>
                  </Descriptions>
                  <Progress
                    percent={metrics.total ? Math.round((metrics.passed / metrics.total) * 100) : 0}
                    status={metrics.eligible ? "success" : "normal"}
                    format={() => metrics.total ? `${metrics.passed}/${metrics.total} 门禁` : "未评测"}
                  />
                  {metrics.evaluation_mode === "DETERMINISTIC_CONTRACT_GATE" && (
                    <Alert
                      type="warning"
                      showIcon
                      message="当前通过的是发布前契约门禁"
                      description="它验证匹配、拒绝误用和环境降级逻辑，不等于已经通过真实故障 Campaign。发布后仍需用独立故障集比较准确率、工具调用数和诊断耗时。"
                    />
                  )}
                  {detail?.evaluations?.map((item) => (
                    <Alert
                      key={item.evaluation_id}
                      type={item.passed ? "success" : "error"}
                      showIcon
                      message={`${GATE_LABEL[item.case_kind] || item.case_kind}：${item.passed ? "通过" : "失败"}`}
                      description={<Text code>{JSON.stringify(item.details)}</Text>}
                    />
                  ))}
                  {detail?.activations?.length > 0 && (
                    <Card size="small" type="inner" title="真实复用记录">
                      {detail.activations.map((item) => (
                        <Paragraph key={item.activation_id} style={{ marginBottom: 6 }}>
                          <Text code>{item.diagnosis_id}</Text>：匹配 {Math.round(item.match_score * 100)}%，
                          {item.baseline_tool} → {item.selected_tool}，结果 {item.outcome || "待反馈"}
                        </Paragraph>
                      ))}
                    </Card>
                  )}
                  <Space wrap>
                    {skill.status === "CANDIDATE" && <Button onClick={() => action(() => evaluateDiagnosticSkill(skill.skill_id), "门禁评测完成")}>运行三类门禁</Button>}
                    {skill.status === "CANDIDATE" && metrics.eligible && <Button type="primary" icon={<SafetyCertificateOutlined />} onClick={() => action(() => publishDiagnosticSkill(skill.skill_id), "技能已发布")}>发布技能</Button>}
                    {skill.status === "ACTIVE" && <Button danger onClick={() => action(() => quarantineDiagnosticSkill(skill.skill_id, "人工发现潜在负迁移"), "技能已隔离")}>隔离</Button>}
                    {skill.status === "ACTIVE" && skill.parent_skill_id && <Button onClick={() => action(() => rollbackDiagnosticSkill(skill.skill_id), "已回滚上一版本")}>回滚上一版</Button>}
                  </Space>
                </Space>
              ),
            };
          })}
        />
      )}
    </Card>
  );
}
