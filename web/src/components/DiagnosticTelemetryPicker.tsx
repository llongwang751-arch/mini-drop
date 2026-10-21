/** 只展示服务器登记且当前用户可使用的数据源，浏览器不接触连接地址或凭据。 */
import "../styles/agent-extensions.css";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { List } from "../types";
import { ErrorPanel } from "./ui";

type Source = { id: string; label: string; signal_types: string[] };
const signalLabels: Record<string, string> = {
  metrics: "业务指标",
  logs: "结构化日志",
  traces: "调用链",
  changes: "部署变更",
};

export default function DiagnosticTelemetryPicker({
  agentId,
  value,
  onChange,
}: {
  agentId: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const sources = useQuery({
    queryKey: ["diagnostic-data-sources", agentId],
    queryFn: ({ signal }) =>
      api<List<Source>>(
        `/diagnostic-data-sources?agent_id=${encodeURIComponent(agentId)}`,
        { signal },
      ),
    enabled: !!agentId,
  });
  return (
    <div className="agent-extension-stack">
      <label>
        关联业务信号（可选）
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={!agentId || sources.isPending}
        >
          <option value="">仅使用采集证据</option>
          {sources.data?.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label} ·{" "}
              {item.signal_types
                .map((type) => signalLabels[type] || type)
                .join(" / ")}
            </option>
          ))}
        </select>
      </label>
      <p className="muted">
        按本次采集时间窗口查询已授权服务与关联依赖。无可用数据源时保留未知，不推断业务正常。
      </p>
      {!!agentId && sources.isPending && (
        <p role="status">正在读取已授权业务信号目录…</p>
      )}
      {sources.isSuccess && sources.data.items.length === 0 && (
        <p>当前节点没有可用的已登记业务数据源；仍可仅使用采集证据。</p>
      )}
      {sources.error && <ErrorPanel error={sources.error} />}
    </div>
  );
}
