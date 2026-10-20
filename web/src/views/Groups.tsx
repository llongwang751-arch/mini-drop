/**
 * 资源组管理：成员角色、节点范围和任务可见性都通过授权 API 读写。
 * 角色选择影响资源边界，页面的隐藏/禁用只能改善体验，后端必须再次校验。
 */
import { useContext, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, UsersThree } from "@phosphor-icons/react";
import { api } from "../api";
import { UserContext } from "../auth-context";
import type { Agent, List, User } from "../types";
import { PageHeading, ErrorPanel, Skeleton, Busy } from "../components/ui";
interface Group {
  id: string;
  name: string;
  description: string;
  members?: {
    user_id: string;
    username: string;
    display_name: string;
    role: string;
  }[];
  agents?: { agent_id: string; permission: string }[];
}
export default function Groups() {
  const user = useContext(UserContext),
    platform = ["admin", "platform_admin"].includes(user?.role || ""),
    baseManager = platform || user?.role === "group_admin";
  const qc = useQueryClient(),
    [selected, setSelected] = useState(""),
    [show, setShow] = useState(false),
    [name, setName] = useState(""),
    [description, setDescription] = useState("");
  const [member, setMember] = useState(""),
    [role, setRole] = useState("viewer"),
    [agent, setAgent] = useState(""),
    [permission, setPermission] = useState("read");
  const q = useQuery({
    queryKey: ["groups"],
    queryFn: ({ signal }) => api<List<Group>>("/groups", { signal }),
  });
  const detail = useQuery({
    queryKey: ["group", selected],
    queryFn: ({ signal }) => api<Group>("/groups/" + selected, { signal }),
    enabled: !!selected,
  });
  const manager =
    baseManager ||
    detail.data?.members?.some(
      (m) => m.user_id === user?.id && m.role === "group_admin",
    ) === true;
  const users = useQuery({
    queryKey: ["users"],
    queryFn: ({ signal }) => api<List<User>>("/users", { signal }),
    enabled: platform,
  });
  const agents = useQuery({
    queryKey: ["agents", "groups"],
    queryFn: ({ signal }) =>
      api<List<Agent>>("/agents?page_size=100", { signal }),
    enabled: manager,
  });
  const create = useMutation({
    mutationFn: () =>
      api<Group>("/groups", {
        method: "POST",
        body: JSON.stringify({ name, description }),
      }),
    onSuccess: (g) => {
      setSelected(g.id);
      setShow(false);
      setName("");
      setDescription("");
      qc.invalidateQueries({ queryKey: ["groups"] });
    },
  });
  const change = useMutation({
    mutationFn: ({
      path,
      body,
      remove,
    }: {
      path: string;
      body?: object;
      remove?: boolean;
    }) =>
      api("/groups/" + selected + path, {
        method: remove ? "DELETE" : "PUT",
        body: body ? JSON.stringify(body) : undefined,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["group", selected] }),
  });
  return (
    <>
      <PageHeading
        eyebrow="WORKSPACE / ACCESS"
        title="让权限跟随团队与资源"
        description="以资源组划定节点操作范围；成员、诊断与共享使用同一套授权。"
        action={
          platform && (
            <button className="button primary" onClick={() => setShow(!show)}>
              <Plus size={17} />
              创建权限组
            </button>
          )
        }
      />
      {show && (
        <form
          className="panel form-section"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="form-grid">
            <label className="field">
              <span>组名称</span>
              <input
                autoFocus
                required
                maxLength={100}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="field">
              <span>说明</span>
              <input
                maxLength={500}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
          </div>
          {create.error && <ErrorPanel error={create.error} />}
          <button className="button primary" disabled={create.isPending}>
            {create.isPending ? <Busy /> : "保存权限组"}
          </button>
        </form>
      )}
      <div className="access-layout">
        <section className="panel group-list">
          <div className="section-heading">
            <h2>权限组</h2>
            <UsersThree size={21} />
          </div>
          {q.error ? (
            <ErrorPanel error={q.error} />
          ) : q.isLoading ? (
            <Skeleton />
          ) : q.data?.items.length ? (
            q.data.items.map((g) => (
              <button
                key={g.id}
                className={
                  "group-pick " + (selected === g.id ? "selected" : "")
                }
                onClick={() => setSelected(g.id)}
              >
                <strong>{g.name}</strong>
                <small>{g.description || "资源组"}</small>
                <span className="mono">#{g.id}</span>
              </button>
            ))
          ) : (
            <div className="quiet-empty">还没有可见的权限组</div>
          )}
        </section>
        <section className="panel form-section">
          {!selected ? (
            <div className="quiet-empty">选择权限组，查看成员与节点范围。</div>
          ) : detail.error ? (
            <ErrorPanel error={detail.error} />
          ) : !detail.data ? (
            <Skeleton />
          ) : (
            <>
              <div className="section-heading">
                <div>
                  <h2>{detail.data.name}</h2>
                  <span className="section-note">
                    {detail.data.description} · #{detail.data.id}
                  </span>
                </div>
              </div>
              <h3>组成员</h3>
              {detail.data.members?.map((m) => (
                <div className="access-row" key={m.user_id}>
                  <div>
                    <strong>{m.display_name || m.username}</strong>
                    <small className="mono">{m.user_id}</small>
                  </div>
                  <span>{m.role}</span>
                  {manager && (
                    <button
                      className="button secondary small"
                      disabled={change.isPending}
                      onClick={() =>
                        change.mutate({
                          path: "/members/" + encodeURIComponent(m.user_id),
                          remove: true,
                        })
                      }
                    >
                      移除
                    </button>
                  )}
                </div>
              ))}
              {manager && (
                <form
                  className="inline-access-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    change.mutate({
                      path: "/members/" + encodeURIComponent(member),
                      body: { role },
                    });
                  }}
                >
                  <label className="field">
                    <span>添加或调整成员</span>
                    {platform ? (
                      <select
                        required
                        value={member}
                        onChange={(e) => setMember(e.target.value)}
                      >
                        <option value="">选择成员</option>
                        {users.data?.items.map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.display_name} · {u.username}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        required
                        value={member}
                        onChange={(e) => setMember(e.target.value)}
                        placeholder="成员 ID"
                      />
                    )}
                  </label>
                  <label className="field">
                    <span>组角色</span>
                    <select
                      value={role}
                      onChange={(e) => setRole(e.target.value)}
                    >
                      <option value="viewer">观察者</option>
                      <option value="operator">操作员</option>
                      <option value="group_admin">组管理员</option>
                    </select>
                  </label>
                  <button
                    className="button secondary"
                    disabled={change.isPending}
                  >
                    保存成员
                  </button>
                </form>
              )}
              <h3>授权节点</h3>
              {detail.data.agents?.map((a) => (
                <div className="access-row" key={a.agent_id}>
                  <strong className="mono">{a.agent_id}</strong>
                  <span>
                    {a.permission === "operate" ? "查看与采集" : "仅查看"}
                  </span>
                  {manager && (
                    <button
                      className="button secondary small"
                      disabled={change.isPending}
                      onClick={() =>
                        change.mutate({
                          path: "/agents/" + encodeURIComponent(a.agent_id),
                          remove: true,
                        })
                      }
                    >
                      撤销
                    </button>
                  )}
                </div>
              ))}
              {manager && (
                <form
                  className="inline-access-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    change.mutate({
                      path: "/agents/" + encodeURIComponent(agent),
                      body: { permission },
                    });
                  }}
                >
                  <label className="field">
                    <span>选择节点</span>
                    <select
                      required
                      value={agent}
                      onChange={(e) => setAgent(e.target.value)}
                    >
                      <option value="">选择节点</option>
                      {agents.data?.items.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.hostname}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span>允许操作</span>
                    <select
                      value={permission}
                      onChange={(e) => setPermission(e.target.value)}
                    >
                      <option value="read">仅查看</option>
                      <option value="operate">查看与采集</option>
                    </select>
                  </label>
                  <button
                    className="button secondary"
                    disabled={change.isPending}
                  >
                    保存节点授权
                  </button>
                </form>
              )}
              {change.error && <ErrorPanel error={change.error} />}
              {change.isSuccess && (
                <p role="status" className="info-line">
                  权限已更新。
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
