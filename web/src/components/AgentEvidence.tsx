import { useContext, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, dateTime } from "../api";
import { pollingInterval } from "../query-policy";
import { UserContext } from "../auth-context";
import type { Agent, List, TaskKind } from "../types";
import { Badge, ErrorPanel, Busy } from "./ui";
export default function AgentEvidence({ agent }: { agent: Agent }) {
  const user = useContext(UserContext),
    platform = ["admin", "platform_admin"].includes(user?.role || ""),
    qc = useQueryClient();
  const [category, setCategory] = useState("resources");
  const kinds = useQuery({
    queryKey: ["kinds"],
    queryFn: ({ signal }) => api<TaskKind[]>("/task-kinds", { signal }),
  });
  const events = useQuery({
    queryKey: ["agent-events", agent.id],
    queryFn: ({ signal }) =>
      api<
        List<{
          id: string;
          type: string;
          occurred_at: string;
          payload: Record<string, unknown>;
        }>
      >("/agents/" + agent.id + "/events", { signal }),
    refetchInterval: (query) => pollingInterval(query, 15000),
  });
  const diagnostics = useQuery({
    queryKey: ["agent-diagnostics", agent.id, category],
    queryFn: ({ signal }) =>
      api<Record<string, unknown>>(
        "/agents/" + agent.id + "/diagnostics?category=" + category,
        { signal },
      ),
    enabled: false,
    retry: false,
  });
  const toggle = useMutation({
    mutationFn: () =>
      api("/agents/" + agent.id, {
        method: "PATCH",
        body: JSON.stringify({ enabled: agent.status === "disabled" }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent", agent.id] }),
  });
  return (
    <>
      {agent.labels.registration != null && (
        <section className="panel form-section">
          <h2>版本协商与节点身份</h2>
          <pre className="script-output">
            {JSON.stringify(agent.labels.registration, null, 2)}
          </pre>
        </section>
      )}
      <section className="panel form-section">
        <div className="section-heading">
          <div>
            <h2>采样器与工具证据</h2>
            <span className="section-note">
              节点自检通过才接受采集，安装工具不等于拥有内核权限。
            </span>
          </div>
          {platform && (
            <button
              className="button secondary small"
              onClick={() => toggle.mutate()}
              disabled={toggle.isPending}
            >
              {agent.status === "disabled" ? "重新启用节点" : "禁用节点"}
            </button>
          )}
        </div>
        {toggle.error && <ErrorPanel error={toggle.error} />}
        <div className="capability-list">
          {kinds.data
            ?.filter(
              (k) =>
                !["import", "composite"].includes(k.runner) &&
                k.id !== "COMPOSITE",
            )
            .map((k) => {
              const detail = agent.capability_details?.[k.id];
              return (
                <div key={k.id}>
                  <div>
                    <strong>{k.display_name}</strong>
                    <small>
                      {detail?.reason || k.input_format || k.runner}
                    </small>
                    {detail?.tools?.length ? (
                      <details>
                        <summary>工具版本与校验</summary>
                        {detail.tools.map((tool, i) => (
                          <p className="mono tool-evidence" key={i}>
                            {tool.name} · {tool.version || "版本未知"}
                            <br />
                            {tool.path}
                            <br />
                            SHA256 {tool.sha256 || "未探测"}
                            {tool.reason ? (
                              <>
                                <br />
                                {tool.reason}
                              </>
                            ) : null}
                          </p>
                        ))}
                      </details>
                    ) : null}
                  </div>
                  <Badge
                    status={
                      agent.capabilities.includes(k.id) ? "online" : "disabled"
                    }
                  />
                </div>
              );
            })}
        </div>
      </section>
      <section className="panel form-section">
        <div className="section-heading">
          <div>
            <h2>控制面诊断快照</h2>
            <span className="section-note">
              有界查询，仅返回已授权的节点信息。
            </span>
          </div>
        </div>
        <div className="inline-access-form">
          <label className="field">
            <span>诊断类别</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="resources">资源快照</option>
              <option value="capabilities">能力与工具</option>
              <option value="processes">公布的进程</option>
              <option value="events">节点事件</option>
            </select>
          </label>
          <button
            className="button secondary"
            disabled={diagnostics.isFetching}
            onClick={() => diagnostics.refetch()}
          >
            {diagnostics.isFetching ? <Busy /> : "获取快照"}
          </button>
        </div>
        {diagnostics.error && <ErrorPanel error={diagnostics.error} />}
        {diagnostics.data && (
          <pre className="script-output">
            {JSON.stringify(diagnostics.data, null, 2)}
          </pre>
        )}
      </section>
      <section className="panel form-section">
        <h2>节点事件</h2>
        {events.error && <ErrorPanel error={events.error} />}
        {events.data?.items?.length ? (
          events.data.items.map((e) => (
            <div className="access-row" key={e.id}>
              <div>
                <strong>{e.type}</strong>
                <details>
                  <summary>查看证据</summary>
                  <pre className="script-output">
                    {JSON.stringify(e.payload, null, 2)}
                  </pre>
                </details>
              </div>
              <time className="mono dim">{dateTime(e.occurred_at)}</time>
            </div>
          ))
        ) : (
          <p className="dim">暂无节点事件。</p>
        )}
      </section>
    </>
  );
}
