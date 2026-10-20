/**
 * 任务协作入口：分享授权与建议反馈都通过服务端接口保存并更新查询缓存。
 * 分享并不复制制品或永久开放下载，接收者读取时仍由后端验证任务权限。
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { List, Suggestion } from "../types";
import { ErrorPanel } from "./ui";

export function TaskSharing({ id }: { id: string }) {
  const qc = useQueryClient(),
    [subject, setSubject] = useState("user"),
    [subjectId, setSubjectId] = useState(""),
    [permission, setPermission] = useState("read");
  const q = useQuery({
    queryKey: ["shares", id],
    queryFn: ({ signal }) =>
      api<
        List<{
          id: string;
          user_id?: string;
          group_id?: string;
          permission: string;
        }>
      >("/tasks/" + id + "/shares", { signal }),
  });
  const write = useMutation({
    mutationFn: ({ remove }: { remove?: string }) =>
      api("/tasks/" + id + "/shares" + (remove ? "/" + remove : ""), {
        method: remove ? "DELETE" : "POST",
        body: remove
          ? undefined
          : JSON.stringify({
              [subject === "user" ? "user_id" : "group_id"]: subjectId,
              permission,
            }),
      }),
    onSuccess: () => {
      setSubjectId("");
      qc.invalidateQueries({ queryKey: ["shares", id] });
    },
  });
  return (
    <section className="panel form-section">
      <div className="section-heading">
        <div>
          <h2>共享诊断证据</h2>
          <span className="section-note">
            共享权限同时适用于结果、事件和制品；只有任务管理者可以修改。
          </span>
        </div>
      </div>
      {q.error ? (
        <ErrorPanel error={q.error} />
      ) : (
        <>
          <form
            className="share-form"
            onSubmit={(e) => {
              e.preventDefault();
              write.mutate({});
            }}
          >
            <label className="field">
              <span>共享对象</span>
              <select
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
              >
                <option value="user">成员</option>
                <option value="group">权限组</option>
              </select>
            </label>
            <label className="field">
              <span>{subject === "user" ? "成员 ID" : "权限组 ID"}</span>
              <input
                required
                value={subjectId}
                onChange={(e) => setSubjectId(e.target.value)}
                maxLength={64}
              />
            </label>
            <label className="field">
              <span>权限</span>
              <select
                value={permission}
                onChange={(e) => setPermission(e.target.value)}
              >
                <option value="read">查看与下载</option>
                <option value="operate">查看与操作</option>
              </select>
            </label>
            <button className="button primary" disabled={write.isPending}>
              添加共享
            </button>
          </form>
          {q.data?.items?.map((s) => (
            <div className="access-row" key={s.id}>
              <strong>
                {s.user_id ? "成员 " + s.user_id : "权限组 " + s.group_id}
              </strong>
              <span>
                {s.permission === "operate" ? "查看与操作" : "查看与下载"}
              </span>
              <button
                className="button secondary small"
                disabled={write.isPending}
                onClick={() => write.mutate({ remove: s.id })}
              >
                撤销
              </button>
            </div>
          ))}
        </>
      )}
      {write.error && <ErrorPanel error={write.error} />}
    </section>
  );
}

export function SuggestionFeedback({
  id,
  suggestions,
  readOnly,
}: {
  id: string;
  suggestions: Suggestion[];
  readOnly: boolean;
}) {
  const qc = useQueryClient();
  const feedback = useMutation({
    mutationFn: ({
      suggestion,
      rating,
    }: {
      suggestion: string;
      rating: string;
    }) =>
      api("/tasks/" + id + "/suggestions/" + suggestion + "/feedback", {
        method: "POST",
        body: JSON.stringify({ rating }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["task", id] }),
  });
  if (readOnly || !suggestions.some((s) => s.id && s.engine !== "ai"))
    return null;
  return (
    <section className="panel suggestion-feedback-panel">
      <div className="section-heading">
        <div>
          <h2>诊断建议反馈</h2>
          <span className="section-note">
            结合现场证据，记录规则建议的实际效果。
          </span>
        </div>
      </div>
      {suggestions
        .filter((s) => s.id && s.engine !== "ai")
        .map((s) => (
          <article className="suggestion-feedback" key={s.id}>
            <div>
              <strong>{s.title}</strong>
              <small className="mono">
                {s.engine} / {s.version}
                {s.input_digest ? " · " + s.input_digest.slice(0, 12) : ""}
              </small>
            </div>
            <div className="segmented">
              <button
                disabled={feedback.isPending}
                onClick={() =>
                  feedback.mutate({ suggestion: s.id!, rating: "useful" })
                }
              >
                有帮助
              </button>
              <button
                disabled={feedback.isPending}
                onClick={() =>
                  feedback.mutate({ suggestion: s.id!, rating: "not_useful" })
                }
              >
                需改进
              </button>
            </div>
          </article>
        ))}
      {feedback.error && <ErrorPanel error={feedback.error} />}
      {feedback.isSuccess && <p role="status">反馈已保存。</p>}
    </section>
  );
}
