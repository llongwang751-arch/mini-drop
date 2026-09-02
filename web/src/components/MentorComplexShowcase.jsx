import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Col, Descriptions, Divider, Modal, Progress, Row, Segmented, Space, Spin, Statistic, Steps, Tag, Timeline, Typography } from "antd";
import { BranchesOutlined, CheckCircleOutlined, CloseCircleOutlined, ExpandOutlined, ExperimentOutlined, ScissorOutlined, SwapOutlined } from "@ant-design/icons";
import { FitExplorationTree } from "./ActualExplorationTree";
import "./MentorComplexShowcase.css";

const { Paragraph, Text, Title } = Typography;

const STATE_META = {
  visited: { label: "已调查", color: "blue", icon: <BranchesOutlined /> },
  refuted: { label: "证据否定并剪枝", color: "red", icon: <ScissorOutlined /> },
  confirmed: { label: "根因路径", color: "green", icon: <CheckCircleOutlined /> },
  unvisited: { label: "满足停止条件，未调查", color: "default", icon: <CloseCircleOutlined /> },
};

function RunMetrics({ run, title }) {
  return (
    <Card size="small" title={title} className="showcase-metric-card">
      <Row gutter={[12, 12]}>
        <Col span={8}><Statistic title="工具调用" value={run.tool_calls} suffix="次" /></Col>
        <Col span={8}><Statistic title="诊断耗时" value={run.duration_seconds} suffix="秒" /></Col>
        <Col span={8}><Statistic title="结论置信度" value={Math.round((run.confidence || 0) * 100)} suffix="%" /></Col>
      </Row>
    </Card>
  );
}

function TreeNode({ node, compact = false }) {
  const meta = STATE_META[node?.state] || STATE_META.visited;
  if (!node) return null;
  return (
    <div className={`tree-card tree-card-${node.state} ${compact ? "tree-card-compact" : ""}`}>
      <div className="tree-card-heading">
        <span className="tree-icon">{meta.icon}</span>
        <Text strong>{node.title}</Text>
      </div>
      <Space wrap size={[4, 4]}><Tag color={meta.color}>{meta.label}</Tag><Tag>{node.domain}</Tag></Space>
      <Paragraph type="secondary" className="tree-card-evidence">{node.evidence}</Paragraph>
    </div>
  );
}

function ExplorationTree({ nodes = [] }) {
  const { roots, children } = useMemo(() => {
    const byParent = new Map();
    const nodeIds = new Set(nodes.map((node) => node.id));
    nodes.forEach((node) => {
      const parent = node.parent_id && nodeIds.has(node.parent_id) ? node.parent_id : "__root__";
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent).push(node);
    });
    return { roots: byParent.get("__root__") || [], children: byParent };
  }, [nodes]);

  const renderBranch = (node, depth = 0) => (
    <li key={node.id}>
      <TreeNode node={node} compact={depth > 0} />
      {(children.get(node.id) || []).length > 0 && (
        <ul>{children.get(node.id).map((child) => renderBranch(child, depth + 1))}</ul>
      )}
    </li>
  );

  return (
    <FitExplorationTree>
      <div className="exploration-tree exploration-tree-dynamic diagnosis-record-tree showcase-record-tree">
        <ul className="dynamic-tree-root">{roots.map((root) => renderBranch(root))}</ul>
      </div>
    </FitExplorationTree>
  );
}

export default function MentorComplexShowcase({ open, loading, data, onClose }) {
  const cases = useMemo(() => data?.cases?.length ? data.cases : (data ? [data] : []), [data]);
  const [activeCaseId, setActiveCaseId] = useState("");
  const [treeOpen, setTreeOpen] = useState(false);
  useEffect(() => {
    if (!cases.length) return;
    setActiveCaseId((current) => cases.some((item) => item.case_id === current)
      ? current
      : (data?.default_case_id || cases[0].case_id));
  }, [cases, data?.default_case_id]);
  useEffect(() => {
    if (!open) setTreeOpen(false);
  }, [open]);
  const showcase = cases.find((item) => item.case_id === activeCaseId) || cases[0] || data;
  const first = showcase?.first_run || {};
  const skill = showcase?.generated_skill || {};
  const second = showcase?.second_run || {};
  const counter = showcase?.counterexample || {};

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      width="min(1500px, 96vw)"
      centered
      className="mentor-showcase-modal"
      title={<Space><ExperimentOutlined /><span>复杂案例回放：从探索树沉淀诊断 Skill</span></Space>}
    >
      <Spin spinning={loading} tip="正在读取已完成案例">
      {!data ? <div style={{ minHeight: 240 }} /> : (
        <div className="mentor-showcase-content">
          {cases.length > 1 && (
            <Card size="small" className="showcase-case-switcher" title="选择多轮复杂案例">
              <Segmented
                block
                value={showcase?.case_id}
                onChange={setActiveCaseId}
                options={cases.map((item, index) => ({
                  value: item.case_id,
                  label: `${index + 1}. ${item.short_title || item.title}`,
                }))}
              />
              <Text type="secondary">每个案例都包含多轮取证、反证剪枝、方向切换和恢复验证，切换后探索树与轮次同步更新。</Text>
            </Card>
          )}
          <Alert
            type="warning"
            showIcon
            message={`${showcase.replay_label}，不是实时事故`}
            description={`${showcase.source}。页面展示的是已保存的真实调查轨迹，用于稳定答辩，不会伪装成当前正在运行的诊断。`}
          />

          <Card size="small" className="showcase-conclusion">
            <Space direction="vertical" size={4}>
              <Space wrap><Tag color="green">诊断成功</Tag><Tag color="blue">置信度 {Math.round((first.confidence || 0) * 100)}%</Tag><Text strong>{showcase.title}</Text></Space>
              <Text><b>最终根因：</b>{showcase.root_cause}</Text>
              <Text><b>处理与验证：</b>{showcase.fix}</Text>
            </Space>
          </Card>

          <Title level={4}>第一次诊断：没有 Skill，AI 走错、转向并找到根因</Title>
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={16}>
              <Card
                size="small"
                title="AI 实际走过的探索树"
                extra={(
                  <Space wrap>
                    <Tag color="blue">蓝：调查</Tag><Tag color="red">红：剪枝</Tag><Tag color="green">绿：根因</Tag><Tag>灰：未走</Tag>
                    <Button type="primary" ghost size="small" icon={<ExpandOutlined />} onClick={() => setTreeOpen(true)}>大屏查看完整树</Button>
                  </Space>
                )}
              >
                <ExplorationTree nodes={first.nodes || []} />
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <RunMetrics run={first} title="首次诊断代价" />
              <Card size="small" title="为什么转弯" style={{ marginTop: 16 }}>
                <Timeline items={(first.switches || []).map((item) => ({
                  dot: <SwapOutlined />,
                  children: <><Text strong>{item.from} → {item.to}</Text><br /><Text type="secondary">{item.reason}</Text></>,
                }))} />
              </Card>
            </Col>
          </Row>

          <Card size="small" title={`${(first.rounds || []).length} 轮真实诊断：每一轮为什么继续、剪枝或转向`}>
            <Timeline
              mode="left"
              items={(first.rounds || []).map((round) => ({
                color: round.round === (first.rounds || []).length ? "green" : round.round === 2 || round.round === 3 ? "red" : "blue",
                label: `${round.duration_seconds} 秒`,
                children: (
                  <div className="diagnosis-round">
                    <Space wrap><Tag color="blue">第 {round.round} 轮</Tag><Text strong>{round.direction}</Text><Tag>{round.tool}</Tag></Space>
                    <Paragraph><b>观测与判断：</b>{round.decision}</Paragraph>
                    <Paragraph type="secondary"><b>下一步：</b>{round.outcome}</Paragraph>
                  </div>
                ),
              }))}
            />
          </Card>

          <Card size="small" title="诊断结束后生成候选 Skill v1" className="generated-skill-card">
            <Steps size="small" current={3} items={["可信结论", "提取真实轨迹", "正例/反例/迁移门禁", "等待人工发布"].map((title) => ({ title }))} />
            <Descriptions size="small" column={{ xs: 1, md: 2 }} bordered style={{ marginTop: 16 }}>
              <Descriptions.Item label="Skill 名称">{skill.name} {skill.version}</Descriptions.Item>
              <Descriptions.Item label="当前状态">{skill.status}</Descriptions.Item>
              <Descriptions.Item label="本次动作">{skill.action}</Descriptions.Item>
              <Descriptions.Item label="来源报告">{skill.source_report_id}</Descriptions.Item>
              <Descriptions.Item label="候选 Skill ID">{skill.candidate_id}</Descriptions.Item>
              <Descriptions.Item label="生成依据">{skill.source_trace}</Descriptions.Item>
              <Descriptions.Item label="沉淀的取证顺序">{(skill.probe_order || []).join(" → ")}</Descriptions.Item>
              <Descriptions.Item label="沉淀的错误路径">{(skill.negative_paths || []).join("；")}</Descriptions.Item>
              <Descriptions.Item label="切换条件">{(skill.switch_conditions || []).join("；")}</Descriptions.Item>
              <Descriptions.Item label="结论停止条件">{(skill.stop_conditions || []).join("；")}</Descriptions.Item>
            </Descriptions>
            <Space wrap style={{ marginTop: 12 }}>
              {(skill.gate_results || []).map((gate) => <Tag color="green" key={gate.name}>{gate.name}：{gate.passed}/{gate.total} 通过</Tag>)}
            </Space>
            <Alert type="info" showIcon style={{ marginTop: 12 }} message="生成不等于发布" description="系统自动提取的是候选 Skill。通过三类门禁并由人确认后才能发布；后续若出现错误迁移，会自动隔离并允许回滚到旧版本。" />
          </Card>

          <Title level={4}>第二次复用与第三次反例：证明 Skill 不是口号</Title>
          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}>
              <RunMetrics run={second} title="相似故障自动命中 Skill" />
              <Alert type="success" showIcon message="正确复用" description={`${second.incident}。路径缩短为：${(second.path || []).join(" → ")}。`} />
            </Col>
            <Col xs={24} lg={12}>
              <RunMetrics run={{ ...counter, confidence: 0.9 }} title="相似症状但不同根因" />
              <Alert type="info" showIcon message="正确拒绝旧 Skill" description={`${counter.rejection_reason}。最终转向并定位：${counter.root_cause}。`} />
            </Col>
          </Row>

          <Card size="small" title="量化对照" className="showcase-comparison">
            <Row gutter={[24, 16]}>
              <Col xs={24} md={8}><Statistic title="工具调用减少" value={showcase.comparison?.improvement?.tool_calls_percent} suffix="%" /></Col>
              <Col xs={24} md={8}><Statistic title="诊断耗时减少" value={showcase.comparison?.improvement?.duration_percent} suffix="%" /></Col>
              <Col xs={24} md={8}><Statistic title="错误经验迁移率" value={showcase.comparison?.improvement?.false_transfer_rate} suffix="%" /></Col>
            </Row>
            <Divider />
            <Progress percent={100} format={() => "基线、故障、恢复证据闭环"} />
          </Card>

          <Modal
            open={treeOpen}
            onCancel={() => setTreeOpen(false)}
            footer={<Button type="primary" onClick={() => setTreeOpen(false)}>关闭</Button>}
            width="96vw"
            centered
            zIndex={1100}
            className="mentor-tree-modal"
            title={<Space><BranchesOutlined /><span>{showcase.title} · 完整探索树</span></Space>}
          >
            <Alert
              type="info"
              showIcon
              message="蓝色为调查路径，红色为反证剪枝，绿色为根因和恢复验证，灰色为满足停止条件后未继续调查的分支"
              description="可使用鼠标滚轮缩放、按住空白区域拖动；右上角按钮可缩小、放大或复位，双击画布也可复位。"
            />
            <div className="mentor-tree-modal-canvas">
              <ExplorationTree nodes={first.nodes || []} />
            </div>
          </Modal>
        </div>
      )}
      </Spin>
    </Modal>
  );
}
