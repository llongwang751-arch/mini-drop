import { BranchesOutlined, CheckCircleOutlined, CloseCircleOutlined, SwapOutlined } from "@ant-design/icons";
import { Card, Space, Tag, Timeline, Typography } from "antd";

const { Text } = Typography;

const TOOL_LABELS = {
  collect_sys_metrics: "系统指标",
  collect_database_diagnostics: "数据库状态",
  start_perf_profile: "CPU 火焰图",
  start_pyspy_profile: "Python 调用栈",
  start_ebpf_io_profile: "I/O 延迟",
  get_agent_status: "采集节点检查",
};

const CATEGORY_LABELS = {
  CPU_HOTSPOT: "CPU",
  PYTHON_RUNTIME: "Python",
  IO_LATENCY: "I/O",
  SYSTEM_RESOURCE: "系统资源",
  DATABASE_LOCK: "数据库",
  NETWORK_DEGRADATION: "网络",
  GENERAL: "通用",
};

const PRUNED = new Set([
  "REFUTED",
  "FALSIFIED",
  "DISPROVED",
  "RULED_OUT",
  "REJECTED",
  "FAILED",
  "CANCELLED",
  "DENIED",
]);

function inferCategory(name = "") {
  if (name.includes("perf")) return "CPU_HOTSPOT";
  if (name.includes("pyspy")) return "PYTHON_RUNTIME";
  if (name.includes("ebpf") || name.includes("io")) return "IO_LATENCY";
  if (name.includes("database")) return "DATABASE_LOCK";
  if (name.includes("network")) return "NETWORK_DEGRADATION";
  if (name.includes("sys_metrics")) return "SYSTEM_RESOURCE";
  return "GENERAL";
}

function buildLiveModel(hypotheses = [], toolCalls = [], report) {
  const orderedTools = [...toolCalls].sort(
    (a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0),
  );
  const rows = hypotheses.map((hypothesis) => ({
    id: hypothesis.hypothesis_id,
    kind: "HYPOTHESIS",
    label: hypothesis.statement,
    status: String(hypothesis.status || "OPEN").toUpperCase(),
    round: hypothesis.round_index || 1,
    reason: hypothesis.generation_reason || "",
    verified: hypothesis.hypothesis_id === report?.hypothesis_id,
  }));
  orderedTools.forEach((tool, index) => rows.push({
    id: `tool:${tool.tool_call_id || index}`,
    kind: "TOOL",
    label: TOOL_LABELS[tool.tool_name] || tool.tool_name,
    status: String(tool.status || "UNKNOWN").toUpperCase(),
    category: inferCategory(tool.tool_name),
    order: index + 1,
    reason: tool.policy_reason || "",
  }));
  const switches = [];
  for (let index = 1; index < orderedTools.length; index += 1) {
    const previous = inferCategory(orderedTools[index - 1].tool_name);
    const current = inferCategory(orderedTools[index].tool_name);
    if (previous !== current) switches.push({
      from_category: previous,
      to_category: current,
      reason: "上一方向证据不足，转向新的取证分支",
    });
  }
  return { rows, switches };
}

export default function ActualExplorationTree({ hypotheses, toolCalls, report }) {
  const { rows, switches } = buildLiveModel(hypotheses, toolCalls, report);
  if (!rows.length) return null;
  const prunedCount = rows.filter((row) => PRUNED.has(row.status)).length;
  const items = rows.map((row) => {
    const pruned = PRUNED.has(row.status);
    const verified = row.verified || row.status === "SUPPORTED" || row.status === "VERIFIED";
    const title = row.kind === "TOOL" ? `${row.order}. ${row.label}` : `第 ${row.round} 轮假设：${row.label}`;
    return {
      color: verified ? "green" : pruned ? "red" : "blue",
      dot: verified ? <CheckCircleOutlined /> : pruned ? <CloseCircleOutlined /> : <BranchesOutlined />,
      children: (
        <div className={`actual-tree-row ${pruned ? "is-pruned" : ""}`}>
          <Space wrap>
            <Text strong={verified}>{title}</Text>
            {row.kind === "TOOL" && <Tag>{CATEGORY_LABELS[row.category] || row.category}</Tag>}
            {verified && <Tag color="green">最终命中</Tag>}
            {pruned && <Tag color="red">已剪枝</Tag>}
          </Space>
          {row.reason && <div><Text type="secondary">原因：{row.reason}</Text></div>}
        </div>
      ),
    };
  });

  return (
    <Card className="actual-exploration-tree" size="small"
      title={<Space><BranchesOutlined /><span>实际探索树</span></Space>}
      extra={<Text type="secondary">记录真实走过的路，不是预设模板</Text>}
    >
      <Space wrap className="actual-tree-summary">
        <Tag color="blue">探索 {rows.length} 个节点</Tag>
        <Tag color={prunedCount ? "red" : "default"}>剪枝 {prunedCount} 条</Tag>
        <Tag color={switches.length ? "purple" : "default"}>方向切换 {switches.length} 次</Tag>
      </Space>
      {switches.map((item, index) => (
        <div className="actual-tree-switch" key={`${item.from_category}-${item.to_category}-${index}`}>
          <SwapOutlined /> 方向切换：{CATEGORY_LABELS[item.from_category]} → {CATEGORY_LABELS[item.to_category]}
          <Text type="secondary">，{item.reason}</Text>
        </div>
      ))}
      <Timeline items={items} />
    </Card>
  );
}
