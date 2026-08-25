import { Alert, Card, Col, Descriptions, Divider, Modal, Progress, Row, Space, Spin, Statistic, Steps, Tag, Timeline, Typography } from "antd";
import { BranchesOutlined, CheckCircleOutlined, CloseCircleOutlined, ExperimentOutlined, ScissorOutlined, SwapOutlined } from "@ant-design/icons";
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

export default function MentorComplexShowcase({ open, loading, data, onClose }) {
  const first = data?.first_run || {};
  const skill = data?.generated_skill || {};
  const second = data?.second_run || {};
  const counter = data?.counterexample || {};
  const nodes = Object.fromEntries((first.nodes || []).map((node) => [node.id, node]));

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
          <Alert
            type="warning"
            showIcon
            message={`${data.replay_label}，不是实时事故`}
            description={`${data.source}。页面展示的是已保存的真实调查轨迹，用于稳定答辩，不会伪装成当前正在运行的诊断。`}
          />

          <Card size="small" className="showcase-conclusion">
            <Space direction="vertical" size={4}>
              <Space wrap><Tag color="green">诊断成功</Tag><Tag color="blue">置信度 94%</Tag><Text strong>{data.title}</Text></Space>
              <Text><b>最终根因：</b>{data.root_cause}</Text>
              <Text><b>处理与验证：</b>{data.fix}</Text>
            </Space>
          </Card>

          <Title level={4}>第一次诊断：没有 Skill，AI 走错、转向并找到根因</Title>
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={16}>
              <Card size="small" title="AI 实际走过的探索树" extra={<Space wrap><Tag color="blue">蓝：调查</Tag><Tag color="red">红：剪枝</Tag><Tag color="green">绿：根因</Tag><Tag>灰：未走</Tag></Space>}>
                <div className="exploration-tree">
                  <div className="tree-root"><TreeNode node={nodes.symptom} /></div>
                  <div className="tree-trunk" />
                  <div className="tree-branches">
                    <TreeNode node={nodes["user-hotspot"]} compact />
                    <TreeNode node={nodes.lock} compact />
                    <div className="tree-success-chain">
                      <TreeNode node={nodes["target-io"]} compact />
                      <div className="tree-chain-arrow">跨进程转向 ↓</div>
                      <TreeNode node={nodes["peer-scan"]} compact />
                      <div className="tree-chain-arrow">恢复反证 ↓</div>
                      <TreeNode node={nodes.recovery} compact />
                    </div>
                    <TreeNode node={nodes.network} compact />
                  </div>
                </div>
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

          <Card size="small" title="5 轮真实诊断：每一轮为什么继续、剪枝或转向">
            <Timeline
              mode="left"
              items={(first.rounds || []).map((round) => ({
                color: round.round === 5 ? "green" : round.round === 2 || round.round === 3 ? "red" : "blue",
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
              <Col xs={24} md={8}><Statistic title="工具调用减少" value={data.comparison?.improvement?.tool_calls_percent} suffix="%" /></Col>
              <Col xs={24} md={8}><Statistic title="诊断耗时减少" value={data.comparison?.improvement?.duration_percent} suffix="%" /></Col>
              <Col xs={24} md={8}><Statistic title="错误经验迁移率" value={data.comparison?.improvement?.false_transfer_rate} suffix="%" /></Col>
            </Row>
            <Divider />
            <Progress percent={100} format={() => "基线、故障、恢复证据闭环"} />
          </Card>
        </div>
      )}
      </Spin>
    </Modal>
  );
}
