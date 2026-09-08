import { useEffect, useMemo, useState } from "react";
import { Alert, Empty, Skeleton } from "antd";
import ReactEChartsCoreImport from "echarts-for-react/lib/core";
import { getTaskArtifactContent } from "../api/client";
import echarts from "../lib/echarts";
import { COLORS } from "../theme";
import { escapeHtml } from "../utils/html";

let ReactEChartsCore = ReactEChartsCoreImport;
for (let depth = 0; depth < 3 && typeof ReactEChartsCore !== "function"; depth += 1) ReactEChartsCore = ReactEChartsCore?.default;

function unwrap(payload) {
  let value = payload;
  for (let depth = 0; depth < 3; depth += 1) {
    if (typeof value === "string") {
      try { value = JSON.parse(value); } catch { return null; }
    } else if (value && typeof value === "object" && value.code === 0 && value.data !== undefined) value = value.data;
    else break;
  }
  return value;
}

export default function CallGraphViewer({ taskId, artifactType = "callgraph_json", artifactIndex = null, height = 520 }) {
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const params = artifactIndex === null || artifactIndex === undefined ? {} : { index: artifactIndex };
    getTaskArtifactContent(taskId, artifactType, params)
      .then((payload) => {
        const value = unwrap(payload);
        if (!value || value.schema_version !== "perf_callgraph.v1") throw new Error("调用图格式不受支持");
        if (active) setGraph(value);
      })
      .catch((err) => { if (active) setError(err.message || "调用图读取失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [taskId, artifactType, artifactIndex]);

  const option = useMemo(() => {
    if (!graph?.nodes?.length) return null;
    const maxSamples = Math.max(...graph.nodes.map((node) => node.inclusive_samples || 0), 1);
    return {
      tooltip: { formatter(params) {
        const data = params.data || {};
        if (params.dataType === "edge") return `<b>${escapeHtml(data.source)}</b> → <b>${escapeHtml(data.target)}</b><br/>边样本 ${Number(data.samples || 0).toLocaleString()} (${data.percent || 0}%)`;
        return `<b>${escapeHtml(data.name || "")}</b><br/>累计样本 ${Number(data.inclusive_samples || 0).toLocaleString()}<br/>自身样本 ${Number(data.self_samples || 0).toLocaleString()}<br/>累计占比 ${data.percent || 0}%`;
      } },
      series: [{
        type: "graph", layout: "force", roam: true, draggable: true, focusNodeAdjacency: true,
        force: { repulsion: 260, edgeLength: [60, 180], gravity: 0.08 },
        label: { show: true, width: 150, overflow: "truncate", fontFamily: "monospace", fontSize: 10 },
        edgeSymbol: ["none", "arrow"], edgeSymbolSize: 7,
        lineStyle: { color: "source", curveness: 0.12, opacity: 0.55 },
        emphasis: { focus: "adjacency", lineStyle: { width: 3, opacity: 0.9 } },
        data: graph.nodes.map((node) => ({
          ...node,
          symbolSize: 14 + Math.sqrt((node.inclusive_samples || 0) / maxSamples) * 42,
          itemStyle: { color: node.self_samples > 0 ? COLORS.primary : COLORS.textSecondary },
        })),
        links: graph.links,
      }],
    };
  }, [graph]);

  if (loading) return <Skeleton.Input active block style={{ height }} />;
  if (error) return <Alert type="warning" showIcon message="调用图加载失败" description={error} />;
  if (!option) return <Empty description="没有可展示的调用关系" />;
  return <div>{graph.truncated && <Alert type="info" showIcon message={`节点过多，按累计样本保留最热 ${graph.node_limit} 个节点`} />}<ReactEChartsCore echarts={echarts} option={option} style={{ width: "100%", height }} notMerge lazyUpdate opts={{ renderer: "canvas" }} /></div>;
}
