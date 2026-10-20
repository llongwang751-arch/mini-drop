import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, ShieldCheck } from "@phosphor-icons/react";
import { api, dateTime } from "../api";
import type { List, User } from "../types";
import { PageHeading, ErrorPanel, Skeleton, Busy } from "../components/ui";
export default function Settings({ user }: { user: User }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState("users");
  const [show, setShow] = useState(false);
  const [editing, setEditing] = useState<User>();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");
  const [displayName, setDisplayName] = useState("");
  const users = useQuery({
    queryKey: ["users"],
    queryFn: ({ signal }) => api<List<User>>("/users", { signal }),
    enabled: ["admin", "platform_admin"].includes(user.role),
  });
  const audits = useQuery({
    queryKey: ["audit-logs"],
    queryFn: ({ signal }) =>
      api<
        List<{
          id: string;
          action: string;
          user_id: string;
          resource_type: string;
          resource_id: string;
          created_at: string;
        }>
      >("/audit-logs?page_size=50", { signal }),
    enabled: ["admin", "platform_admin"].includes(user.role) && tab === "audit",
  });
  const create = useMutation({
    mutationFn: () =>
      api(editing ? "/users/" + editing.id : "/users", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify({
          ...(editing ? {} : { username }),
          ...(password ? { password } : {}),
          role,
          display_name: displayName || username,
        }),
      }),
    onSuccess: () => {
      setShow(false);
      setEditing(undefined);
      setPassword("");
      qc.invalidateQueries({ queryKey: ["users"] });
    },
  });
  return (
    <>
      <PageHeading
        eyebrow="WORKSPACE / SETTINGS"
        title="工作空间设置"
        description="管理成员权限，追溯每一次关键操作。"
      />
      <div className="info-banner">
        <ShieldCheck size={20} />
        <p>
          当前登录：{user.display_name} · {user.role}
          。平台管理员管理全局资源；其他成员按所属组、资源授权和任务共享范围访问。
        </p>
      </div>
      {!["admin", "platform_admin"].includes(user.role) ? (
        <div className="panel quiet-empty">
          成员管理和审计记录仅对管理员开放。
        </div>
      ) : (
        <>
          <div className="detail-tabs">
            <button
              className={tab === "users" ? "selected" : ""}
              onClick={() => setTab("users")}
            >
              成员管理
            </button>
            <button
              className={tab === "audit" ? "selected" : ""}
              onClick={() => setTab("audit")}
            >
              操作审计
            </button>
          </div>
          <section className="panel">
            {tab === "users" ? (
              <>
                <div className="section-heading">
                  <div>
                    <h2>工作空间成员</h2>
                    <span className="section-note">最小权限原则</span>
                  </div>
                  <button
                    className="button secondary small"
                    onClick={() => {
                      setEditing(undefined);
                      setUsername("");
                      setDisplayName("");
                      setRole("viewer");
                      setPassword("");
                      setShow((v) => !v);
                    }}
                  >
                    <Plus size={16} />
                    添加成员
                  </button>
                </div>
                {show && (
                  <form
                    className="member-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      create.mutate();
                    }}
                  >
                    <div className="form-grid">
                      <label className="field">
                        <span>用户名</span>
                        <input
                          required
                          minLength={3}
                          maxLength={64}
                          pattern="[a-zA-Z0-9_.-]+"
                          disabled={!!editing}
                          value={username}
                          onChange={(e) => setUsername(e.target.value)}
                        />
                      </label>
                      <label className="field">
                        <span>显示名称</span>
                        <input
                          value={displayName}
                          onChange={(e) => setDisplayName(e.target.value)}
                        />
                      </label>
                      <label className="field">
                        <span>
                          {editing
                            ? "更新密码（留空不修改）"
                            : "初始密码（至少 12 位）"}
                        </span>
                        <input
                          type="password"
                          required={!editing}
                          minLength={12}
                          autoComplete="new-password"
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                        />
                      </label>
                      <label className="field">
                        <span>角色</span>
                        <select
                          value={role}
                          onChange={(e) => setRole(e.target.value)}
                        >
                          <option value="viewer">Viewer · 只读</option>
                          <option value="operator">Operator · 执行诊断</option>
                          <option value="group_admin">
                            GroupAdmin · 组管理员
                          </option>
                          <option value="admin">Admin · 兼容角色</option>
                          <option value="platform_admin">
                            PlatformAdmin · 平台管理员
                          </option>
                        </select>
                      </label>
                    </div>
                    {create.error && <ErrorPanel error={create.error} />}
                    <div className="form-footer">
                      <button
                        className="button secondary"
                        type="button"
                        onClick={() => setShow(false)}
                      >
                        取消
                      </button>
                      <button
                        className="button primary"
                        disabled={create.isPending}
                      >
                        {create.isPending ? (
                          <Busy />
                        ) : editing ? (
                          "保存成员"
                        ) : (
                          "添加成员"
                        )}
                      </button>
                    </div>
                  </form>
                )}
                {users.error ? (
                  <ErrorPanel error={users.error} />
                ) : !users.data ? (
                  <Skeleton />
                ) : (
                  <div className="table-scroll">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>成员</th>
                          <th>用户名</th>
                          <th>角色</th>
                          <th>状态</th>
                          <th>管理</th>
                        </tr>
                      </thead>
                      <tbody>
                        {users.data.items.map((u) => (
                          <tr key={u.id}>
                            <td>
                              <span className="member-cell">
                                <span className="avatar">
                                  {u.display_name.slice(0, 1).toUpperCase()}
                                </span>
                                <strong>{u.display_name}</strong>
                              </span>
                            </td>
                            <td className="mono dim">{u.username}</td>
                            <td>
                              <span className="role-label mono">{u.role}</span>
                            </td>
                            <td>{u.id === user.id ? "当前用户" : "已启用"}</td>
                            <td>
                              <button
                                className="button secondary small"
                                onClick={() => {
                                  setEditing(u);
                                  setUsername(u.username);
                                  setDisplayName(u.display_name);
                                  setRole(u.role);
                                  setPassword("");
                                  setShow(true);
                                }}
                              >
                                编辑
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="section-heading">
                  <div>
                    <h2>操作审计</h2>
                    <span className="section-note">最近 50 条记录</span>
                  </div>
                </div>
                {audits.error ? (
                  <ErrorPanel error={audits.error} />
                ) : !audits.data ? (
                  <Skeleton />
                ) : (
                  <div className="table-scroll">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>操作</th>
                          <th>用户</th>
                          <th>资源</th>
                          <th>时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {audits.data.items.map((a) => (
                          <tr key={a.id}>
                            <td className="mono">{a.action}</td>
                            <td className="mono dim">{a.user_id}</td>
                            <td className="mono dim">
                              {a.resource_type} / {a.resource_id || "—"}
                            </td>
                            <td className="mono dim">
                              {dateTime(a.created_at)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </section>
        </>
      )}
    </>
  );
}
