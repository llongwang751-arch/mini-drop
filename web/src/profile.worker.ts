/// <reference lib="webworker" />
/**
 * 性能制品专用 Worker：完成授权链接获取、限额下载、gzip 解压和结构校验。
 * 对象请求不携带应用 Cookie；预签名 URL 只在本流程临时使用，不放进错误消息或日志。
 */
import {
  COMPRESSED_LIMIT,
  ERROR_BODY_LIMIT,
  EXPANDED_LIMIT,
  PROFILE_DEADLINE_MS,
  ProfileDataError,
  decodeFlameGzip,
  expiredSignature,
  parseAnalysisEnvelope,
  parseUTF8JSON,
  profileError,
  readBounded,
  safeRequestId,
  signedURL,
} from "./profile-data";
import type { ProfileWorkerRequest } from "./profile-loader";

async function artifact(
  message: Extract<ProfileWorkerRequest, { op: "artifact" }>,
  signal: AbortSignal,
) {
  let refreshed = false,
    requestId = "";
  try {
    // 短期授权只允许刷新一次；反复过期说明需要用户重载，不能无限申请新链接。
    while (true) {
      const grant = await fetch(
        `/api/v1/tasks/${message.taskId}/artifacts/${message.artifactId}/download`,
        {
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          signal,
          headers: { "X-Request-ID": message.requestIds[refreshed ? 1 : 0] },
        },
      );
      requestId =
        safeRequestId(grant.headers.get("X-Request-ID")) ||
        message.requestIds[refreshed ? 1 : 0];
      let envelope;
      try {
        envelope = parseUTF8JSON(
          await readBounded(grant.body, ERROR_BODY_LIMIT),
        );
      } catch (error) {
        if (!grant.ok) throw profileError(grant.status, requestId);
        throw error;
      }
      requestId = safeRequestId(envelope?.request_id) || requestId;
      if (!grant.ok) throw profileError(grant.status, requestId);
      // 校验同源 /objects/ 路径后才下载；授权接口和对象接口的凭据策略必须分开。
      const url = signedURL(envelope?.data?.url, self.location.origin);
      const expires = Date.parse(envelope?.data?.expires_at);
      if (!Number.isFinite(expires))
        throw new ProfileDataError("PROFILE_URL_INVALID");
      if (expires <= Date.now()) {
        if (!refreshed) {
          refreshed = true;
          continue;
        }
        throw new ProfileDataError("PROFILE_URL_EXPIRED");
      }
      // 对象下载显式 omit Cookie 且拒绝重定向，避免把应用会话带到存储链路。
      const response = await fetch(url, {
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal,
      });
      if (!response.ok) {
        let body;
        try {
          body = new TextDecoder().decode(
            await readBounded(response.body, ERROR_BODY_LIMIT),
          );
        } catch {
          throw profileError(response.status, requestId);
        }
        if (expiredSignature(response.status, body)) {
          if (!refreshed) {
            refreshed = true;
            continue;
          }
          throw new ProfileDataError("PROFILE_URL_EXPIRED", response.status);
        }
        throw profileError(response.status, requestId);
      }
      const length = response.headers.get("Content-Length");
      if (
        length &&
        (!/^\d+$/.test(length) || Number(length) > COMPRESSED_LIMIT)
      )
        throw new ProfileDataError("PROFILE_TOO_LARGE");
      const compressed = await readBounded(response.body, COMPRESSED_LIMIT);
      // 实际压缩字节数须匹配登记元数据，再进入解压和整棵调用树校验。
      if (compressed.byteLength !== message.expectedSize)
        throw new ProfileDataError("PROFILE_SIZE_MISMATCH");
      return await decodeFlameGzip(compressed);
    }
  } catch (error) {
    if (error instanceof ProfileDataError) {
      error.requestId ||= requestId;
      throw error;
    }
    if (signal.aborted)
      throw new ProfileDataError("REQUEST_TIMEOUT", 0, requestId);
    throw new ProfileDataError("PROFILE_DOWNLOAD_FAILED", 0, requestId);
  }
}
// Worker 只接受两类受限操作；失败只返回受控错误码与请求 ID，不序列化 URL 或原生异常。
self.onmessage = async (event: MessageEvent<ProfileWorkerRequest>) => {
  const message = event.data;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROFILE_DEADLINE_MS - 250);
  try {
    let data;
    if (message.op === "result") {
      if (
        !(message.buffer instanceof ArrayBuffer) ||
        message.buffer.byteLength > EXPANDED_LIMIT
      )
        throw new ProfileDataError("PROFILE_TOO_LARGE");
      data = parseAnalysisEnvelope(
        message.buffer,
        message.status,
        message.requestId,
      );
    } else {
      if (
        !/^[1-9]\d{0,18}$/.test(message.taskId) ||
        !/^[1-9]\d{0,18}$/.test(message.artifactId) ||
        !Number.isSafeInteger(message.expectedSize) ||
        message.expectedSize <= 0 ||
        message.expectedSize > COMPRESSED_LIMIT
      )
        throw new ProfileDataError("PROFILE_TOO_LARGE");
      data = await artifact(message, controller.signal);
    }
    self.postMessage({ data });
  } catch (error) {
    const problem =
      error instanceof ProfileDataError
        ? error
        : new ProfileDataError("PROFILE_WORKER_FAILED");
    self.postMessage({
      error: {
        code: problem.code,
        status: problem.status,
        requestId: problem.requestId,
      },
    });
  } finally {
    clearTimeout(timer);
  }
};
