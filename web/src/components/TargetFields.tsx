/**
 * 受控目标表单：把进程、容器或主机选择转换成结构化参数。
 * UID 与启动时间等身份信息用于防止 PID 复用选错对象；最终校验在采集端和服务端完成。
 */
import type { Agent, TaskKind } from "../types";
import { useEffect } from "react";
export interface TargetForm {
  expected_uid?: string;
  agent_id: string;
  pid: string;
  type: string;
  runtime: string;
  container_id: string;
  start_time_ticks: string;
}
export const emptyTarget = (): TargetForm => ({
  expected_uid: "",
  agent_id: "",
  pid: "",
  type: "process",
  runtime: "docker",
  container_id: "",
  start_time_ticks: "",
});
export function targetPayload(value: TargetForm) {
  const pid =
    value.type === "container" || value.type === "host"
      ? 0
      : Number(value.pid) || 0;
  return {
    pid,
    scope: "process",
    target: {
      type: value.type,
      pid,
      ...(value.expected_uid !== undefined && value.expected_uid !== ""
        ? { expected_uid: Number(value.expected_uid), check_uid: true }
        : {}),
      ...(value.start_time_ticks
        ? { start_time_ticks: Number(value.start_time_ticks) }
        : {}),
      ...(value.type === "container"
        ? {
            runtime: value.runtime,
            container_id: value.container_id,
            container_pid: Number(value.pid) || 1,
          }
        : {}),
    },
  };
}
export default function TargetFields({
  value,
  onChange,
  agents,
  kind,
}: {
  value: TargetForm;
  onChange: (value: TargetForm) => void;
  agents: Agent[];
  kind?: TaskKind;
}) {
  const update = (part: Partial<TargetForm>) => onChange({ ...value, ...part });
  useEffect(() => {
    if (
      kind?.supported_targets?.length &&
      !kind.supported_targets.includes(value.type)
    )
      onChange({ ...value, type: kind.supported_targets[0] });
  }, [kind?.id, value.type]);
  const agent = agents.find((a) => a.id === value.agent_id);
  return (
    <div className="target-fields">
      <label className="field">
        <span>
          Agent 节点 <em>*</em>
        </span>
        <select
          required
          value={value.agent_id}
          onChange={(e) =>
            update({ agent_id: e.target.value, pid: "", start_time_ticks: "" })
          }
        >
          <option value="">选择在线节点</option>
          {agents
            .filter((a) => a.status === "online")
            .map((a) => (
              <option
                key={a.id}
                value={a.id}
                disabled={!!kind && !a.capabilities.includes(kind.id)}
              >
                {a.hostname}
                {kind && !a.capabilities.includes(kind.id)
                  ? "（采样器未就绪）"
                  : ""}
              </option>
            ))}
        </select>
      </label>
      <div className="form-grid">
        <label className="field">
          <span>目标类型</span>
          <select
            value={value.type}
            onChange={(e) => update({ type: e.target.value })}
          >
            {(kind?.supported_targets || ["process"])
              .filter((t) => ["process", "host", "container"].includes(t))
              .map((t) => (
                <option value={t} key={t}>
                  {t === "host"
                    ? "宿主机资源"
                    : t === "container"
                      ? "业务容器"
                      : "授权进程"}
                </option>
              ))}
          </select>
        </label>
        {value.type !== "host" && (
          <label className="field">
            <span>
              {value.type === "container" ? "容器内 PID" : "目标 PID"}
            </span>
            <input
              type="number"
              required
              min={value.type === "container" ? 1 : 2}
              max="2147483647"
              value={value.pid}
              onChange={(e) => update({ pid: e.target.value })}
            />
          </label>
        )}
      </div>
      {value.type === "container" && (
        <div className="form-grid">
          <label className="field">
            <span>容器运行时</span>
            <select
              value={value.runtime}
              onChange={(e) => update({ runtime: e.target.value })}
            >
              <option value="docker">Docker</option>
              <option value="containerd">containerd</option>
            </select>
          </label>
          <label className="field">
            <span>容器 ID</span>
            <input
              required
              value={value.container_id}
              pattern="[A-Fa-f0-9]{12,64}"
              maxLength={64}
              onChange={(e) => update({ container_id: e.target.value })}
              placeholder="12～64 位容器 ID"
            />
          </label>
        </div>
      )}
      {value.type === "process" && !!agent?.resources.processes?.length && (
        <div className="process-picks">
          <span>节点公布的进程</span>
          {agent.resources.processes.map((p) => (
            <button
              type="button"
              key={p.pid}
              className={value.pid === String(p.pid) ? "selected" : ""}
              onClick={() =>
                update({
                  pid: String(p.pid),
                  expected_uid: p.uid === undefined ? "" : String(p.uid),
                  start_time_ticks:
                    p.start_time_ticks === undefined
                      ? ""
                      : String(p.start_time_ticks),
                })
              }
            >
              {p.name}
              <span className="mono">{p.pid}</span>
            </button>
          ))}
        </div>
      )}
      <small className="dim">
        节点策略与资源组授权共同决定采集范围；启动前重新校验
        PID、UID、启动时间和 namespace。
      </small>
      <details className="target-identity">
        <summary>固定目标启动时间</summary>
        <label className="field">
          <span>/proc starttime ticks</span>
          <input
            inputMode="numeric"
            pattern="[0-9]*"
            value={value.start_time_ticks}
            onChange={(e) => update({ start_time_ticks: e.target.value })}
            maxLength={16}
          />
          <small>可选；用于拒绝同一 PID 被重用后的后继进程。</small>
        </label>
        <label className="field">
          <span>目标 UID</span>
          <input
            type="number"
            min="0"
            max="4294967295"
            value={value.expected_uid || ""}
            onChange={(e) => update({ expected_uid: e.target.value })}
          />
          <small>
            目标不在节点的进程快照内时填写。实际 UID 必须与节点白名单一致。
          </small>
        </label>
      </details>
    </div>
  );
}
