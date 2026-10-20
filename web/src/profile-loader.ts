/**
 * 主线程的制品载入门面：负责生命周期和 Worker 消息，不在 UI 线程解压大型 profile。
 * 路由退出会取消请求并终止 Worker，防止旧任务的数据在新页面载入后继续回写。
 */
import { APIError, newIdempotencyKey } from "./api";
import type { AnalysisResult, Artifact, FlameNode } from "./types";
import {
  COMPRESSED_LIMIT,
  ERROR_BODY_LIMIT,
  EXPANDED_LIMIT,
  PROFILE_DEADLINE_MS,
  ProfileDataError,
  readBounded,
} from "./profile-data";

export type ProfileWorkerRequest =
  | { op: "result"; buffer: ArrayBuffer; status: number; requestId: string }
  | {
      op: "artifact";
      taskId: string;
      artifactId: string;
      expectedSize: number;
      requestIds: [string, string];
    };

export function parseInProfileWorker<T>(
  message: ProfileWorkerRequest,
  signal?: AbortSignal,
  transfer: Transferable[] = [],
): Promise<T> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const worker = new Worker(new URL("./profile.worker.ts", import.meta.url), {
      type: "module",
    });
    // 任意结束路径都终止专用 Worker；已卸载页面不再收到迟到的解析结果。
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
      worker.terminate();
    };
    const cancel = () => {
      cleanup();
      reject(signal?.reason || new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(
        new APIError(
          "火焰图载入超时，请稍后重新载入",
          "REQUEST_TIMEOUT",
          message.op === "artifact" ? message.requestIds[0] : message.requestId,
          0,
        ),
      );
    }, PROFILE_DEADLINE_MS);
    signal?.addEventListener("abort", cancel, { once: true });
    worker.onmessage = (event) => {
      cleanup();
      if (event.data.error) {
        const e = event.data.error;
        const problem = new ProfileDataError(e.code, e.status, e.requestId);
        if (e.status === 401)
          window.dispatchEvent(new Event("drop:unauthorized"));
        reject(
          new APIError(
            problem.message,
            problem.code,
            problem.requestId,
            problem.status,
          ),
        );
      } else resolve(event.data.data as T);
    };
    worker.onerror = () => {
      cleanup();
      reject(
        new APIError(
          "火焰图解析进程无法完成，请重新载入",
          "PROFILE_WORKER_FAILED",
          "",
          0,
        ),
      );
    };
    try {
      // ArrayBuffer 可按 transfer 所有权转移，避免大型制品在主线程和 Worker 间再复制一份。
      worker.postMessage(message, transfer);
    } catch {
      cleanup();
      reject(
        new APIError(
          "火焰图解析进程无法完成，请重新载入",
          "PROFILE_WORKER_FAILED",
          "",
          0,
        ),
      );
    }
  });
}
export async function loadAnalysisResult(
  taskId: string,
  signal?: AbortSignal,
): Promise<AnalysisResult> {
  if (!/^[1-9]\d{0,18}$/.test(taskId))
    throw new ProfileDataError("PROFILE_RESULT_INVALID");
  // 下载、限额读取和 Worker 解析共享同一总期限，而不是每个阶段重新获得完整超时时间。
  const requestId = newIdempotencyKey(),
    controller = new AbortController();
  const cancel = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(
    () => controller.abort(new DOMException("Timed out", "TimeoutError")),
    PROFILE_DEADLINE_MS,
  );
  try {
    const response = await fetch(`/api/v1/tasks/${taskId}/result`, {
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
      headers: { "X-Request-ID": requestId },
      signal: controller.signal,
    });
    const buffer = await readBounded(
      response.body,
      response.ok ? EXPANDED_LIMIT : ERROR_BODY_LIMIT,
    );
    return await parseInProfileWorker<AnalysisResult>(
      {
        op: "result",
        buffer,
        status: response.status,
        requestId: response.headers.get("X-Request-ID") || requestId,
      },
      controller.signal,
      [buffer],
    );
  } catch (error) {
    if (signal?.aborted) throw signal.reason;
    if (controller.signal.aborted)
      throw new APIError(
        "火焰图载入超时，请稍后重新载入",
        "REQUEST_TIMEOUT",
        requestId,
        0,
      );
    if (error instanceof APIError) throw error;
    if (error instanceof ProfileDataError)
      throw new APIError(error.message, error.code, requestId, error.status);
    throw new APIError(
      "分析结果下载失败，请稍后重试",
      "PROFILE_DOWNLOAD_FAILED",
      requestId,
      0,
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
export function loadFlameArtifact(
  taskId: string,
  artifact: Artifact,
  signal?: AbortSignal,
): Promise<FlameNode> {
  if (
    !Number.isSafeInteger(artifact.size) ||
    artifact.size <= 0 ||
    artifact.size > COMPRESSED_LIMIT
  )
    return Promise.reject(new ProfileDataError("PROFILE_TOO_LARGE"));
  // 这里只传任务/制品身份与登记大小；短期下载授权由 Worker 在当前会话下重新获取。
  return parseInProfileWorker<FlameNode>(
    {
      op: "artifact",
      taskId,
      artifactId: artifact.id,
      expectedSize: artifact.size,
      requestIds: [newIdempotencyKey(), newIdempotencyKey()],
    },
    signal,
  );
}
