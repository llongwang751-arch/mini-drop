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

const BENCHMARK_REPORT_URL = "/report-assets/skill-evolution/benchmark-report.json";

const BENCHMARK_FAMILY = {
  POSITIVE_REUSE: { label: "新措辞相似事故", purpose: "验证生产检索能否选择正确的已发布 Skill" },
  MISLEADING_OR_UNDERSPECIFIED: { label: "误导与信息不足", purpose: "验证表面相似或信息不足时不会强行复用" },
  CAPABILITY_OR_ENVIRONMENT_DRIFT: { label: "能力与环境漂移", purpose: "验证探针不可用或环境变化时安全降级" },
};

function percent(value) {
  return Math.round(Number(value || 0) * 1000) / 10;
}

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
  {
    id: "python-runtime-diagnosis",
    name: "Python 运行时热点诊断",
    category: "运行时",
    description: "优先使用用户态 Python 栈采样，在无法使用 perf 时仍能识别 GIL、协程和解释器热点。",
    route: ["运行时识别", "用户态采样", "Python 调用栈", "优化后复测"],
    evidence: "Python 栈样本、热点函数占比、优化前后对照",
    scenario: "Python 服务 CPU 升高、协程阻塞或 GIL 竞争，但宿主机未授予 perf 权限",
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
  const [loadError, setLoadError] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [benchmark, setBenchmark] = useState(null);
  const [benchmarkError, setBenchmarkError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const items = await listDiagnosticSkills();
      setSkills(Array.isArray(items) ? items : []);
    } catch (error) {
      setLoadError(error.message || "读取诊断 Skill 失败");
      message.error(error.message || "读取诊断技能失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let active = true;
    fetch(BENCHMARK_REPORT_URL, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((report) => {
        if (report?.schema !== "mini-drop.skill-reuse-report.v2") {
          throw new Error("报告 schema 不受支持");
        }
        if (active) setBenchmark(report);
      })
      .catch((error) => {
        if (active) setBenchmarkError(error.message || "离线评测报告读取失败");
      });
    return () => { active = false; };
  }, []);

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
  const benchmarkFamilies = Object.entries(benchmark?.family_results || {});
  const baselineRate = percent(benchmark?.baseline_no_skill?.accuracy);
  const enabledRate = percent(benchmark?.skill_enabled?.accuracy);
  const positiveRate = percent(benchmark?.skill_enabled?.positive_reuse_rate);
  const rejectionRate = percent(benchmark?.skill_enabled?.negative_rejection_rate);

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
      {loadError && (
        <Alert
          className="skill-runtime-error"
          type="error"
          showIcon
          message="运行实例的 Skill 数据读取失败"
          description={loadError}
          action={<Button size="small" onClick={load}>重新读取</Button>}
        />
      )}
      <Card className="skill-overview-card" size="small" title="已验证能力概览">
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}><Statistic title="内置诊断流程" value={BUILTIN_SKILLS.length} suffix="个" /></Col>
          <Col xs={12} md={6}><Statistic title="新提示词案例" value={benchmark?.dataset?.case_count ?? "—"} suffix={benchmark ? "个" : ""} valueStyle={{ color: "#087a5b" }} /></Col>
          <Col xs={12} md={6}><Statistic title="Skill 路由准确率" value={benchmark ? enabledRate : "—"} suffix={benchmark ? "%" : ""} valueStyle={{ color: "#1677ff" }} /></Col>
          <Col xs={12} md={6}><Statistic title="安全拒绝率" value={benchmark ? rejectionRate : "—"} suffix={benchmark ? "%" : ""} /></Col>
        </Row>
        <div className="skill-runtime-strip">
          <Text>
            当前运行实例策略库：候选 {loadError ? "不可用" : candidateCount} 个 · 已发布 {loadError ? "不可用" : activeCount} 个 · 真实复用 {loadError ? "不可用" : activationCount} 次 · 已隔离 {loadError ? "不可用" : quarantinedCount} 个
          </Text>
          <Text type="secondary">
            这里为当前数据库实时状态；显示 0 只表示本实例尚未从真实诊断生成策略，不代表内置流程或离线评测不存在。
          </Text>
          <Text type="secondary">离线报告：<Text code>{BENCHMARK_REPORT_URL}</Text></Text>
        </div>
      </Card>
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
      <Card className="builtin-skill-card" size="small" title={`内置参考 Skill · ${BUILTIN_SKILLS.length} 条可复用路线`}>
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
      <Card className="skill-benchmark-card" size="small" title={`盲测路线评测 · ${benchmark?.dataset?.case_count ?? "读取中"} 个新提示词案例`}>
        <Alert
          type="warning"
          showIcon
          message="发布门禁和效果评测是两件事"
          description="公开题面与私有标准答案（Oracle）分离，固定种子生成后直接调用生产混合检索和兼容性门禁。它只测路线记忆的选择与拒绝，不把 Skill 命中冒充根因证据，也不测线上诊断耗时。"
        />
        {benchmarkError && <Alert type="error" showIcon message="离线报告不可用，页面不会显示替代数字" description={benchmarkError} />}
        <div className="skill-benchmark-grid">
          {benchmarkFamilies.map(([key, result]) => (
            <div className="skill-benchmark-item" key={key}>
              <div><b>{result.total}</b><span>例</span></div>
              <Text strong>{BENCHMARK_FAMILY[key]?.label || key}</Text>
              <Text type="secondary">{BENCHMARK_FAMILY[key]?.purpose || "路线评测案例"}</Text>
            </div>
          ))}
        </div>
        {benchmark && <Card className="skill-result-card" size="small" type="inner" title="已验证效果 · 无 Skill 与启用 Skill 对照">
          <Alert
            showIcon
            type="success"
            message="数字由仓库脚本调用当前生产路由代码生成，页面只读取报告"
            description={<>数据集版本 <Text code>{benchmark.dataset.version}</Text>，合并哈希 <Text code>{benchmark.dataset.combined_sha256}</Text>。旧测试集未参与生成或计分。</>}
          />
          <div className="skill-result-grid">
            <div className="skill-result-metric">
              <Text type="secondary">全部案例路由准确率</Text>
              <b><del>{baselineRate}%</del> → {enabledRate}%</b>
              <span>{benchmark.baseline_no_skill.correct}/{benchmark.baseline_no_skill.total} → {benchmark.skill_enabled.correct}/{benchmark.skill_enabled.total}</span>
            </div>
            <div className="skill-result-metric">
              <Text type="secondary">新措辞相似事故正确复用</Text>
              <b>{positiveRate}%</b>
              <span>{benchmark.skill_enabled.positive_correct}/{benchmark.skill_enabled.positive_total} 选择正确路线</span>
            </div>
            <div className="skill-result-metric">
              <Text type="secondary">反例与漂移安全拒绝</Text>
              <b>{rejectionRate}%</b>
              <span>{benchmark.skill_enabled.negative_correct}/{benchmark.skill_enabled.negative_total} 未错误激活</span>
            </div>
            <div className="skill-result-metric">
              <Text type="secondary">错误激活率</Text>
              <b>{percent(benchmark.skill_enabled.false_activation_rate)}%</b>
              <span>{benchmark.skill_enabled.false_activations} 个案例触发失败，逐例结果保存在报告</span>
            </div>
            <div className="skill-result-metric">
              <Text type="secondary">生产检索实现</Text>
              <b>BM25 + 向量 + 门禁</b>
              <span>同一套选择函数，不维护页面专用判题逻辑</span>
            </div>
            <div className="skill-result-metric skill-result-metric-muted">
              <Text type="secondary">真实根因准确率 / 诊断耗时</Text>
              <b>需 Linux 实机诊断验证（Campaign）</b>
              <span>离线路由报告明确不覆盖这两项</span>
            </div>
          </div>
          <div className="skill-proof-rounds">
            <div className="skill-proof-round">
              <Tag color="default">数据边界</Tag>
              <div><Text strong>公开题面 / 私有答案</Text><Text>案例 ID 对齐，答案不进入公开输入，哈希写入报告。</Text></div>
            </div>
            <div className="skill-proof-round">
              <Tag color="success">正例</Tag>
              <div><Text strong>跨措辞复用</Text><Text>{benchmark.skill_enabled.positive_correct}/{benchmark.skill_enabled.positive_total} 个新提示词正例选择正确 Skill。</Text></div>
            </div>
            <div className="skill-proof-round">
              <Tag color="warning">负例</Tag>
              <div><Text strong>误导与漂移</Text><Text>{benchmark.skill_enabled.negative_correct}/{benchmark.skill_enabled.negative_total} 个案例安全拒绝复用。</Text></div>
            </div>
            <div className="skill-proof-round">
              <Tag color="blue">复现</Tag>
              <div><Text strong>固定生成器与生产代码</Text><Text>运行 make diagnosis-benchmark-v2 可重建数据和报告。</Text></div>
            </div>
          </div>
        </Card>}
        <Text className="skill-benchmark-boundary" type="secondary">
          当前数字只表示 Skill 路线记忆的选择与拒绝能力。真实根因准确率和实际耗时仍需在 Linux 实机故障验证（Campaign）中，用基线、故障、恢复三段快照复核。
        </Text>
      </Card>
      <div className="skill-plaza-toolbar">
        <div>
          <Text strong>能力目录</Text>
          <Text type="secondary"> · 每张卡片都能追溯到来源诊断和门禁记录</Text>
        </div>
        <Segmented value={statusFilter} options={FILTER_OPTIONS} onChange={setStatusFilter} />
      </div>
      {loadError ? (
        <Empty description="运行实例数据暂不可用，请重新读取；上方离线评测结果仍可查看" />
      ) : skills.length === 0 ? (
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
                      正确 {skill.correct_outcome_count || 0} / 部分正确 {skill.partial_outcome_count || 0} / 错误 {skill.wrong_outcome_count || 0}
                    </Descriptions.Item>
                    <Descriptions.Item label="反馈覆盖率">
                      {Math.round(Number(skill.outcome_coverage || 0) * 100)}%
                    </Descriptions.Item>
                    <Descriptions.Item label="复用可信度">
                      {Math.round(Number(skill.posterior_reliability ?? 0.5) * 100)}%
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
                      description="它验证匹配、拒绝误用和环境降级逻辑，不等于已经通过真实故障验证（Campaign）。发布后仍需用独立故障集比较准确率、工具调用数和诊断耗时。"
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
