import {
  WarningCircle,
  ArrowUpRight,
  DownloadSimple,
} from "@phosphor-icons/react";
import type { Artifact } from "../types";
import type { FailureAction, FailureDetails } from "../task-failure";
import { bytes } from "../api";
import { ErrorPanel } from "./ui";
import "./TaskFailurePanel.css";

const actionLabels = {
  retry: "重新采集",
  reanalyze: "重新分析原始数据",
  reimport: "重新导入",
};
export default function TaskFailurePanel({
  failure,
  taskId,
  onTimeline,
  onAction,
  onDownload,
  busy,
  downloadBusy,
  downloadError,
}: {
  failure: FailureDetails;
  taskId: string;
  onTimeline: () => void;
  onAction: (action: Exclude<FailureAction, null>) => void;
  onDownload: (artifact: Artifact) => void;
  busy: boolean;
  downloadBusy: boolean;
  downloadError: unknown;
}) {
  return (
    <section className="panel task-failure-panel" aria-label="本次执行诊断">
      <div className="failure-heading">
        <WarningCircle size={23} aria-hidden="true" />
        <div>
          <h2>
            {failure.code === "TASK_CANCELED"
              ? "本次执行已取消"
              : "本次执行未完成"}
          </h2>
          <p>{failure.message}</p>
        </div>
      </div>
      <dl className="failure-evidence">
        <div>
          <dt>失败阶段</dt>
          <dd>{failure.stage}</dd>
        </div>
        <div>
          <dt>错误码</dt>
          <dd className="mono">{failure.code}</dd>
        </div>
        <div className="failure-retry">
          <dt>重试判定</dt>
          <dd>{failure.retryLabel}</dd>
        </div>
        <div>
          <dt>任务 / 执行尝试</dt>
          <dd className="mono">
            #{taskId} / {failure.attemptId || "未记录"}
          </dd>
        </div>
        <div className="failure-request">
          <dt>请求追踪 ID</dt>
          <dd className="mono">{failure.requestId || "服务端未记录"}</dd>
        </div>
      </dl>
      <p className="failure-next-step">{failure.recommendation}</p>
      <div className="failure-actions">
        {failure.action && (
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => onAction(failure.action!)}
          >
            {actionLabels[failure.action]}
          </button>
        )}
        <button className="button ghost" onClick={onTimeline}>
          查看本次时间线
          <ArrowUpRight size={15} />
        </button>
      </div>
      <div className="failure-saved">
        <h3>本次执行已保存制品</h3>
        <p>
          仅列出当前采集尝试中仍可读取的原始文件、日志和二进制附件，不代表分析成功。
        </p>
        {!!downloadError && <ErrorPanel error={downloadError} />}
        {failure.saved.length ? (
          <ul>
            {failure.saved.map((artifact) => (
              <li key={artifact.id}>
                <div>
                  <strong>{artifact.name}</strong>
                  <small className="mono">
                    {artifact.kind} · {bytes(artifact.size)} · SHA256{" "}
                    {artifact.sha256.slice(0, 12)}…
                  </small>
                </div>
                <button
                  className="icon-button bordered"
                  disabled={downloadBusy}
                  aria-label={`下载本次执行制品${artifact.name}`}
                  onClick={() => onDownload(artifact)}
                >
                  <DownloadSimple size={18} />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="failure-no-artifacts">
            本次执行尚未生成可安全读取的制品，或制品已过期。
          </p>
        )}
      </div>
    </section>
  );
}
