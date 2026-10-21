/**
 * 用户主动复核后才保存经验。固定类别代替自由文本，减少历史指令注入入口。
 * 同一表单重试复用幂等键，切换来源由父组件 key 隔离；前端不能自证修复已通过。
 */
import { useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, newIdempotencyKey } from "../api";
import { caseInput, correctionOptions } from "../diagnostic-knowledge";
import type { CaseOutcome, DiagnosticCase } from "../diagnostic-knowledge";
import { Busy, ErrorPanel } from "./ui";
import "../styles/knowledge.css";

export default function SaveDiagnosticCase({
  taskID,
  diagnosisID,
  repairID,
  outcome = "reviewed_correction",
}: {
  taskID: string;
  diagnosisID: string;
  repairID?: string;
  outcome?: CaseOutcome;
}) {
  const qc = useQueryClient();
  const correctionID = useId();
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [correction, setCorrection] = useState("");
  const key = useRef<{ fingerprint: string; value: string } | undefined>(
    undefined,
  );
  const save = useMutation({
    mutationFn: () => {
      const input = caseInput(outcome, confirmed, correction, repairID);
      const fingerprint = JSON.stringify({ taskID, diagnosisID, input });
      if (key.current?.fingerprint !== fingerprint)
        key.current = { fingerprint, value: newIdempotencyKey() };
      return api<DiagnosticCase>(
        `/tasks/${encodeURIComponent(taskID)}/diagnoses/${encodeURIComponent(diagnosisID)}/cases`,
        {
          method: "POST",
          body: JSON.stringify(input),
          idempotencyKey: key.current.value,
        },
      );
    },
    onSuccess: () => {
      key.current = undefined;
      void qc.invalidateQueries({ queryKey: ["diagnostic-cases"] });
    },
  });
  const verified = outcome === "verified_repair";
  return (
    <section
      className="knowledge-save"
      aria-label={verified ? "保存受控修复案例" : "保存复核纠正案例"}
    >
      {save.isSuccess ? (
        <p role="status">
          案例 #{save.data.id} 已保存。
          <Link
            to={`/knowledge?tab=cases#case-${encodeURIComponent(save.data.id)}`}
          >
            查看或撤销案例
          </Link>
        </p>
      ) : !open ? (
        <button
          type="button"
          className="button secondary"
          onClick={() => setOpen(true)}
        >
          {verified ? "保存受控修复案例" : "保存复核纠正案例"}
        </button>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <strong>
            {verified
              ? "将这次受控修复保存为经验"
              : "记录这次人工复核的纠正类别"}
          </strong>
          <p>
            {verified
              ? "服务端会重新核对关联修复、原功能回归与受控性能对照。保存成功不代表整站或生产性能提升。"
              : "只记录固定纠正类别与本次诊断来源，不保存自由备注。人工纠正本身不等于根因已验证。"}
          </p>
          {!verified && (
            <div className="knowledge-save-label">
              <label htmlFor={correctionID}>纠正类别</label>
              <select
                id={correctionID}
                value={correction}
                disabled={save.isPending}
                onChange={(event) => setCorrection(event.target.value)}
                required
              >
                <option value="">请选择纠正类别</option>
                {correctionOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          )}
          <label className="knowledge-confirm">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={save.isPending}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            我已复核当前记录及其证据边界，同意将它作为后续排查的历史先验。
          </label>
          {save.error && <ErrorPanel error={save.error} />}
          <div className="knowledge-save-actions">
            <button
              type="submit"
              className="button primary"
              disabled={
                save.isPending || !confirmed || (!verified && !correction)
              }
            >
              {save.isPending ? <Busy /> : "确认保存案例"}
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={save.isPending}
              onClick={() => {
                setOpen(false);
                save.reset();
              }}
            >
              暂不保存
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
