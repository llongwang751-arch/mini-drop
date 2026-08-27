import {
  AimOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  MinusCircleOutlined,
  SwapOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, Card, Space, Tag, Timeline, Tooltip, Typography } from "antd";
import "./MentorComplexShowcase.css";

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

const NODE_META = {
  visited: { label: "已调查", color: "blue", icon: <BranchesOutlined /> },
  refuted: { label: "反证剪枝", color: "red", icon: <CloseCircleOutlined /> },
  confirmed: { label: "根因路径", color: "green", icon: <CheckCircleOutlined /> },
  unvisited: { label: "满足停止条件", color: "default", icon: <MinusCircleOutlined /> },
};

function ExplorationNode({ node, childrenByParent, activeNodeIds }) {
  const meta = NODE_META[node.state] || NODE_META.unvisited;
  const children = childrenByParent.get(node.id) || [];
  const active = activeNodeIds.has(node.id);
  return (
    <li>
      <div className={`tree-card tree-card-${node.state || "unvisited"} ${active ? "is-live-node" : ""}`}>
        <div className="tree-card-heading">
          <span className="tree-icon">{meta.icon}</span>
          <Text strong>{node.title}</Text>
        </div>
        <Space wrap size={[4, 4]}>
          <Tag color={meta.color}>{active ? "刚刚更新" : meta.label}</Tag>
          {node.domain && <Tag>{node.domain}</Tag>}
        </Space>
        {node.tool && <div><Text type="secondary">工具：{node.tool}</Text></div>}
        {node.evidence && <div className="tree-card-evidence"><Text type="secondary">{node.evidence}</Text></div>}
      </div>
      {children.length > 0 && (
        <ul>{children.map((child) => (
          <ExplorationNode
            key={child.id}
            node={child}
            childrenByParent={childrenByParent}
            activeNodeIds={activeNodeIds}
          />
        ))}</ul>
      )}
    </li>
  );
}

export function FitExplorationTree({ children }) {
  const viewportRef = useRef(null);
  const contentRef = useRef(null);
  const dragRef = useRef(null);
  const panRef = useRef({ x: 0, y: 0 });
  const [layout, setLayout] = useState({ scale: 0.72, height: 520 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);

  const measure = useCallback(() => {
    const viewport = viewportRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;
    const naturalWidth = content.scrollWidth;
    const naturalHeight = content.scrollHeight;
    if (!naturalWidth || !naturalHeight) return;
    const availableWidth = Math.max(320, viewport.clientWidth - 16);
    const scale = Math.min(0.82, Math.max(0.34, availableWidth / naturalWidth));
    setLayout({ scale, height: Math.ceil(naturalHeight * scale) + 8 });
  }, []);

  useLayoutEffect(() => {
    measure();
    const frame = window.requestAnimationFrame(measure);
    if (typeof ResizeObserver === "undefined") return () => window.cancelAnimationFrame(frame);
    const observer = new ResizeObserver(measure);
    if (viewportRef.current) observer.observe(viewportRef.current);
    if (contentRef.current) observer.observe(contentRef.current);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [measure]);

  const changeZoom = useCallback((factor) => {
    setZoom((current) => Math.min(2.8, Math.max(0.65, current * factor)));
  }, []);

  const resetView = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  useEffect(() => { panRef.current = pan; }, [pan]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    const handleWheel = (event) => {
      event.preventDefault();
      changeZoom(event.deltaY < 0 ? 1.12 : 0.89);
    };
    const handlePointerDown = (event) => {
      const target = event.target;
      if (event.button !== 0 || (target instanceof Element && target.closest(".diagnosis-tree-controls"))) return;
      dragRef.current = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        pan: panRef.current,
      };
      viewport.setPointerCapture?.(event.pointerId);
      setDragging(true);
    };
    const handlePointerMove = (event) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      setPan({
        x: drag.pan.x + event.clientX - drag.x,
        y: drag.pan.y + event.clientY - drag.y,
      });
    };
    const stopDragging = (event) => {
      if (!dragRef.current || dragRef.current.pointerId !== event.pointerId) return;
      viewport.releasePointerCapture?.(dragRef.current.pointerId);
      dragRef.current = null;
      setDragging(false);
    };
    viewport.addEventListener("wheel", handleWheel, { passive: false });
    viewport.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopDragging);
    window.addEventListener("pointercancel", stopDragging);
    return () => {
      viewport.removeEventListener("wheel", handleWheel);
      viewport.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopDragging);
      window.removeEventListener("pointercancel", stopDragging);
    };
  }, [changeZoom]);

  const actualScale = layout.scale * zoom;

  return (
    <div
      ref={viewportRef}
      className={`diagnosis-tree-fit-viewport ${dragging ? "is-dragging" : ""}`}
      style={{ height: layout.height }}
      onDoubleClick={resetView}
      aria-label="可缩放拖动的诊断探索树"
    >
      <div className="diagnosis-tree-controls" onPointerDown={(event) => event.stopPropagation()}>
        <Tooltip title="缩小">
          <Button size="small" icon={<ZoomOutOutlined />} aria-label="缩小探索树" onClick={() => changeZoom(0.86)} />
        </Tooltip>
        <span className="diagnosis-tree-scale">{Math.round(actualScale * 100)}%</span>
        <Tooltip title="放大">
          <Button size="small" icon={<ZoomInOutlined />} aria-label="放大探索树" onClick={() => changeZoom(1.16)} />
        </Tooltip>
        <Tooltip title="恢复一屏适配">
          <Button size="small" icon={<AimOutlined />} aria-label="复位探索树" onClick={resetView} />
        </Tooltip>
      </div>
      <div className="diagnosis-tree-gesture-hint">滚轮缩放 · 按住拖动 · 双击复位</div>
      <div
        ref={contentRef}
        className="diagnosis-tree-fit-content"
        style={{ transform: `translateX(-50%) translate(${pan.x}px, ${pan.y}px) scale(${actualScale})` }}
      >
        {children}
      </div>
    </div>
  );
}

function StructuredExplorationTree({ nodes, switches, snapshot = null }) {
  const childrenByParent = new Map();
  for (const node of nodes) {
    const key = node.parent_id || "__root__";
    childrenByParent.set(key, [...(childrenByParent.get(key) || []), node]);
  }
  const roots = childrenByParent.get("__root__") || [];
  const prunedCount = nodes.filter((node) => node.state === "refuted").length;
  const activeNodeIds = new Set(snapshot?.active_node_ids || []);
  const stats = snapshot?.stats || {};
  return (
    <Card className="actual-exploration-tree" size="small"
      title={(
        <Space>
          <BranchesOutlined />
          <span>{snapshot ? "实时探索树" : "实际探索树"}</span>
          {snapshot && <span className="live-tree-pulse" aria-label="实时更新中" />}
        </Space>
      )}
      extra={snapshot ? <Text type="secondary">版本 {snapshot.revision || 0}</Text> : <Text type="secondary">由真实路径生成</Text>}
    >
      <Space wrap className="actual-tree-summary">
        <Tag color="blue">{stats.rounds || 0} 轮</Tag>
        <Tag color="blue">探索 {stats.nodes || nodes.length} 个节点</Tag>
        <Tag color="red">剪枝 {stats.pruned ?? prunedCount} 条</Tag>
        <Tag color="purple">方向切换 {(switches || []).length} 次</Tag>
        {snapshot?.status && <Tag>{snapshot.status}</Tag>}
      </Space>
      {snapshot?.rounds?.length > 0 && (
        <div className="live-tree-rounds" aria-label="诊断轮次">
          {snapshot.rounds.map((round) => (
            <div className={`live-tree-round is-${String(round.status || "").toLowerCase()}`} key={round.round_index}>
              <b>{round.round_index}</b>
              <span>第 {round.round_index} 轮</span>
              <small>{round.tool_call_count} 工具 · {round.evidence_count} 证据</small>
            </div>
          ))}
        </div>
      )}
      {(switches || []).map((item, index) => (
        <div className="actual-tree-switch" key={`${item.from || item.from_category}-${item.to || item.to_category}-${index}`}>
          <SwapOutlined /> 第 {index + 1} 次转向：{item.from || item.from_category} → {item.to || item.to_category}
          <Text type="secondary">，{item.reason}</Text>
        </div>
      ))}
      <FitExplorationTree>
        <div className="exploration-tree exploration-tree-dynamic diagnosis-record-tree">
          <ul className="dynamic-tree-root">
            {roots.map((root) => (
              <ExplorationNode
                key={root.id}
                node={root}
                childrenByParent={childrenByParent}
                activeNodeIds={activeNodeIds}
              />
            ))}
          </ul>
        </div>
      </FitExplorationTree>
    </Card>
  );
}

export default function ActualExplorationTree({ tree, hypotheses, toolCalls, report }) {
  if (tree?.nodes?.length) {
    return (
      <StructuredExplorationTree
        nodes={tree.nodes}
        switches={tree.switches || []}
        snapshot={tree}
      />
    );
  }
  if (report?.exploration_nodes?.length) {
    return (
      <StructuredExplorationTree
        nodes={report.exploration_nodes}
        switches={report.exploration_switches || []}
      />
    );
  }
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
