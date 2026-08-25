import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Col, Collapse, Descriptions, Empty, Progress, Row, Segmented, Space, Statistic, Steps, Tag, Typography, message } from "antd";
import { DeploymentUnitOutlined, ExportOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import {
  evaluateDiagnosticSkill,
  getDiagnosticSkill,
  listDiagnosticSkills,
  publishDiagnosticSkill,
  quarantineDiagnosticSkill,
  rollbackDiagnosticSkill,
} from "../api/client";
import "./SkillEvolutionPanel.css";

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

const STATUS_LABEL = {
  CANDIDATE: "候选",
  ACTIVE: "已发布",
  QUARANTINED: "已隔离",
  RETIRED: "已退役",
};

const FILTER_OPTIONS = [
  { label: "全部", value: "ALL" },
  { label: "候选", value: "CANDIDATE" },
  { label: "已发布", value: "ACTIVE" },
  { label: "已隔离", value: "QUARANTINED" },
];

const INDEPENDENT_BENCHMARK = [
  { key: "SIMILAR_INCIDENT", label: "相似事故", count: 5, purpose: "验证能否缩短已知问题的取证路径" },
  { key: "MISLEADING_INCIDENT", label: "误导反例", count: 4, purpose: "验证不会因为表面症状相似而误用 Skill" },
  { key: "ENVIRONMENT_DRIFT", label: "环境漂移", count: 2, purpose: "验证采集能力或环境变化时能够降级" },
  { key: "WRONG_FEEDBACK", label: "错误反馈", count: 2, purpose: "验证负迁移会触发自动隔离" },
  { key: "VERSION_ROLLBACK", label: "版本回滚", count: 1, purpose: "验证新版本失效后可恢复上一版" },
  { key: "CONTAMINATED_EVIDENCE", label: "污染证据", count: 1, purpose: "验证缺失来源或校验失败的证据不能生成 Skill" },
];

const REPOSITORY_BRANCH = "release/unified-ai-diagnosis-20260821";
const REPOSITORY_SKILL_ROOT = `https://github.com/llongwang751-arch/mini-drop/tree/${REPOSITORY_BRANCH}/skills`;

const BUILTIN_SKILLS = [
  {
    id: "cpu-hotspot-diagnosis",
    name: "CPU 热点循证诊断",
    category: "CPU 性能",
    description: "先比较系统指标，再用 perf 火焰图定位热点，并用恢复窗口反证结论。",
    route: ["系统指标", "CPU 采样", "热点函数", "恢复验证"],
    evidence: "CPU 变化、热点函数占比、恢复后 CPU 回落",
    scenario: "订单计算、序列化、循环处理等业务函数持续占用 CPU",
  },
  {
    id: "memory-growth-diagnosis",
    name: "内存持续增长诊断",
    category: "内存",
    description: "区分正常缓存、对象保留和真实泄漏，避免只凭 RSS 上升就下结论。",
    route: ["RSS/PSS 趋势", "对象保留", "内存剖析", "停止增长验证"],
    evidence: "连续窗口增长、对象或映射增长、故障停止后的趋势",
    scenario: "缓存、监听器或任务队列持续保留对象，导致进程内存上升",
  },
  {
    id: "io-latency-diagnosis",
    name: "I/O 延迟分层诊断",
    category: "I/O",
    description: "从进程写入、内核延迟到磁盘压力逐层排查，区分应用阻塞与设备瓶颈。",
    route: ["系统 I/O", "进程写入", "内核延迟", "磁盘压力"],
    evidence: "吞吐与延迟、目标进程写入、内核 I/O 分布",
    scenario: "日志写入、同步落盘或文件扫描导致请求延迟升高",
  },
  {
    id: "dependency-latency-diagnosis",
    name: "下游依赖延迟定位",
    category: "服务依赖",
    description: "把本机资源与下游响应时间对齐，判断慢在自身代码还是依赖链路。",
    route: ["本机资源", "调用耗时", "下游健康", "恢复对照"],
    evidence: "本机资源稳定、下游延迟上升、移除延迟后恢复",
    scenario: "本机指标正常，但数据库、缓存或 HTTP 下游响应变慢",
  },
  {
    id: "lock-contention-diagnosis",
    name: "锁竞争与线程等待诊断",
    category: "并发",
    description: "先识别线程忙等和阻塞，再定位锁等待栈，避免把高 CPU 一律归因到业务热点。",
    route: ["线程状态", "上下文切换", "锁等待栈", "解除竞争复测"],
    evidence: "忙等线程占比、锁相关调用栈、降低并发后耗时回落",
    scenario: "高并发下线程在互斥锁、自旋锁或条件变量上反复等待",
  },
  {
    id: "gc-pressure-diagnosis",
    name: "GC 压力循证诊断",
    category: "运行时",
    description: "把分配速率、回收次数和暂停时间关联起来，区分内存泄漏与短命对象抖动。",
    route: ["内存趋势", "GC 次数与停顿", "对象分配", "降低分配复测"],
    evidence: "分配速率、GC 暂停或次数、优化后吞吐和延迟恢复",
    scenario: "Java 或 Python 服务频繁创建临时对象，引起 GC 次数和延迟同步上升",
  },
  {
    id: "fd-leak-diagnosis",
    name: "文件描述符泄漏诊断",
    category: "资源",
    description: "跟踪文件描述符总量和类型，定位未关闭的文件、Socket 或管道。",
    route: ["FD 趋势", "资源类型", "持有进程", "关闭资源复测"],
    evidence: "FD 连续增长、同类资源聚集、修复后数量不再上升",
    scenario: "连接池、文件读取或子进程管道未释放，最终触发 too many open files",
  },
  {
    id: "network-degradation-diagnosis",
    name: "网络劣化与重传诊断",
    category: "网络",
    description: "结合连接、重传和上下游对照，区分应用慢、依赖慢和网络路径异常。",
    route: ["连接指标", "重传与丢包", "上下游对照", "网络恢复验证"],
    evidence: "重传率或丢包变化、对端耗时、恢复后请求延迟回落",
    scenario: "跨节点调用抖动、连接重置或丢包导致 P95/P99 延迟升高",
  },
];

const CATEGORY_LABEL = {
  CPU_HOTSPOT: "CPU 热点",
  MEMORY_LEAK: "内存持续增长",
  MEMORY_PRESSURE: "内存压力",
  IO_LATENCY: "I/O 延迟",
  DOWNSTREAM_LATENCY: "下游依赖延迟",
  NETWORK_DEGRADATION: "网络劣化",
};

export default function SkillEvolutionPanel() {
  const [skills, setSkills] = useState([]);
  const [details, setDetails] = useState({});
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState("ALL");

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
  const visibleSkills = useMemo(
    () => statusFilter === "ALL" ? skills : skills.filter((item) => item.status === statusFilter),
    [skills, statusFilter],
  );

  return (
    <Card
      className="skill-plaza"
      size="small"
      title={(
        <Space direction="vertical" size={0}>
          <Space><DeploymentUnitOutlined /><span>诊断 Skill 广场</span></Space>
          <Text type="secondary" className="skill-plaza-subtitle">把已验证的诊断流程沉淀成可评测、可发布、可回滚的能力</Text>
        </Space>
      )}
      extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>}
    >
      <Alert
        showIcon
        type="info"
        message="这里展示的不是提示词模板，而是经过验证的诊断流程"
        description="结论经可信证据校验且人工确认正确后，系统提取探针顺序、证据要求和停止条件形成候选 Skill。候选通过相似正例、误导反例和环境漂移门禁后才能发布；真实诊断中的负反馈会触发隔离，旧版本可以回滚。"
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
      <Card className="builtin-skill-card" size="small" title="内置参考 Skill · 8 个可演示案例，可直接查看源码">
        <Paragraph type="secondary">
          这些是仓库自带的诊断流程模板，用来说明 Skill 在页面和代码中如何落地。它们不会冒充已通过真实故障评测的运行时 Skill；只有下方由真实诊断生成并通过门禁的策略，才能发布复用。
        </Paragraph>
        <div className="builtin-skill-grid">
          {BUILTIN_SKILLS.map((skill) => (
            <Card
              className="builtin-skill-item"
              key={skill.id}
              size="small"
              title={skill.name}
              extra={<Tag color="blue">{skill.category}</Tag>}
              actions={[
                <Button
                  key="source"
                  type="link"
                  icon={<ExportOutlined />}
                  href={`${REPOSITORY_SKILL_ROOT}/${skill.id}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看仓库源码
                </Button>,
              ]}
            >
              <Paragraph>{skill.description}</Paragraph>
              <Text className="builtin-skill-scenario"><b>适用案例：</b>{skill.scenario}</Text>
              <Text className="builtin-skill-route">{skill.route.join(" → ")}</Text>
              <Text className="builtin-skill-evidence" type="secondary">证据要求：{skill.evidence}</Text>
            </Card>
          ))}
        </div>
      </Card>
      <Card className="skill-benchmark-card" size="small" title="独立难例评测 · 15 个未参与技能生成的案例">
        <Alert
          type="warning"
          showIcon
          message="发布门禁和效果评测是两件事"
          description="三类门禁用于阻止明显不安全的候选发布；独立难例集用于比较启用 Skill 前后的根因准确率、反例拒绝率、工具调用数、诊断耗时、负迁移、隔离和回滚。测试案例与来源诊断严格分离，避免拿训练样本给自己打分。"
        />
        <div className="skill-benchmark-grid">
          {INDEPENDENT_BENCHMARK.map((item) => (
            <div className="skill-benchmark-item" key={item.key}>
              <div><b>{item.count}</b><span>例</span></div>
              <Text strong>{item.label}</Text>
              <Text type="secondary">{item.purpose}</Text>
            </div>
          ))}
        </div>
        <Text className="skill-benchmark-boundary" type="secondary">
          当前仓库提供确定性离线回放；真实根因准确率和实际耗时仍需在 Linux 故障 Campaign 中，用基线、故障、恢复三段快照复核。
        </Text>
      </Card>
      <div className="skill-plaza-toolbar">
        <div>
          <Text strong>能力目录</Text>
          <Text type="secondary"> · 每张卡片都能追溯到来源诊断和门禁记录</Text>
        </div>
        <Segmented value={statusFilter} options={FILTER_OPTIONS} onChange={setStatusFilter} />
      </div>
      {skills.length === 0 ? (
        <Empty description="还没有候选技能。完成一次有可信证据的诊断并选择“结论正确”后，系统会自动生成候选。" />
      ) : visibleSkills.length === 0 ? (
        <Empty description="当前筛选条件下没有 Skill" />
      ) : (
        <Collapse
          className="skill-plaza-list"
          onChange={(keys) => keys.forEach(openDetail)}
          items={visibleSkills.map((skill) => {
            const detail = details[skill.skill_id];
            const metrics = skill.gate_metrics || {};
            const route = skill.strategy?.probe_order || [];
            const gatePercent = metrics.total ? Math.round((metrics.passed / metrics.total) * 100) : 0;
            return {
              key: skill.skill_id,
              label: (
                <div className="skill-card-label">
                  <div className="skill-card-heading">
                    <Space wrap>
                      <Text strong>{CATEGORY_LABEL[skill.category] || skill.category}</Text>
                      <Tag>v{skill.version}</Tag>
                      <Tag color={STATUS_COLOR[skill.status]}>{STATUS_LABEL[skill.status] || skill.status}</Tag>
                    </Space>
                    <Text className="skill-route" type="secondary">{route.join(" → ") || "待提取取证路线"}</Text>
                  </div>
                  <div className="skill-card-metrics">
                    <span><b>{gatePercent}%</b> 门禁</span>
                    <span><b>{skill.activation_count || 0}</b> 次命中</span>
                    <span><b>{skill.wrong_outcome_count || 0}</b> 次负反馈</span>
                  </div>
                </div>
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
                    percent={gatePercent}
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
                      description={<Text className="skill-gate-detail" code>{JSON.stringify(item.details)}</Text>}
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
