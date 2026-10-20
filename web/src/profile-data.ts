/**
 * 性能报告的边界校验：分别限制压缩大小、解压大小和调用树规模。
 * 读取接口失败时只返回受控错误类型；下载链接、对象错误正文和原始 profile 不进入界面错误详情。
 */
import type { AnalysisResult, Artifact, FlameNode, Task } from "./types";
import { validateFlameTree } from "./components/flame-analysis";

export const COMPRESSED_LIMIT = 16 * 1024 * 1024;
export const EXPANDED_LIMIT = 32 * 1024 * 1024;
export const ERROR_BODY_LIMIT = 16 * 1024;
export const PROFILE_DEADLINE_MS = 30000;
const messages: Record<string, string> = {
  PROFILE_TOO_LARGE: "火焰图制品超过安全大小限制",
  PROFILE_GZIP_INVALID: "火焰图 gzip 制品损坏或无法解压",
  PROFILE_UTF8_INVALID: "火焰图制品不是有效 UTF-8 文本",
  PROFILE_JSON_INVALID: "火焰图制品 JSON 格式无效",
  PROFILE_TREE_INVALID: "火焰图制品调用树无效或超过节点/深度限制",
  PROFILE_URL_INVALID: "火焰图下载链接来源或路径无效",
  PROFILE_URL_EXPIRED: "火焰图短期链接刷新后仍已过期，请重新载入",
  PROFILE_UNAUTHORIZED: "登录已失效，无法读取火焰图制品",
  PROFILE_FORBIDDEN: "没有读取此火焰图制品的权限",
  PROFILE_GONE: "火焰图制品已超过保留期或已移除",
  PROFILE_DOWNLOAD_FAILED: "火焰图制品下载失败，请稍后重新载入",
  PROFILE_SIZE_MISMATCH: "火焰图制品大小与登记信息不一致",
  PROFILE_RESULT_INVALID: "分析结果格式无效",
  PROFILE_WORKER_FAILED: "火焰图解析进程无法完成，请重新载入",
  REQUEST_TIMEOUT: "火焰图载入超时，请稍后重新载入",
};
export class ProfileDataError extends Error {
  constructor(
    public code: string,
    public status = 0,
    public requestId = "",
  ) {
    super(messages[code] || messages.PROFILE_DOWNLOAD_FAILED);
  }
}
export function profileError(status: number, requestId = "") {
  return new ProfileDataError(
    status === 401
      ? "PROFILE_UNAUTHORIZED"
      : status === 403
        ? "PROFILE_FORBIDDEN"
        : status === 410
          ? "PROFILE_GONE"
          : "PROFILE_DOWNLOAD_FAILED",
    status,
    requestId,
  );
}
export function safeRequestId(value: unknown) {
  return typeof value === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(value)
    ? value
    : "";
}
// 即使 Content-Length 缺失或不可信，也逐块累计实际字节；超限时取消 reader 并释放锁。
export async function readBounded(
  body: ReadableStream<Uint8Array> | null,
  limit: number,
): Promise<ArrayBuffer> {
  if (!body) throw new ProfileDataError("PROFILE_DOWNLOAD_FAILED");
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new ProfileDataError("PROFILE_TOO_LARGE");
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes.buffer;
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
}
export function parseUTF8JSON(buffer: ArrayBuffer) {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    throw new ProfileDataError("PROFILE_UTF8_INVALID");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new ProfileDataError("PROFILE_JSON_INVALID");
  }
}
// 正式页面只在专用 profile Worker 内调用此解析入口，避免大型 JSON 阻塞主线程。
export async function decodeFlameGzip(
  buffer: ArrayBuffer,
  expandedLimit = EXPANDED_LIMIT,
): Promise<FlameNode> {
  if (buffer.byteLength > COMPRESSED_LIMIT)
    throw new ProfileDataError("PROFILE_TOO_LARGE");
  let unpacked: ArrayBuffer;
  try {
    const bytes = new Uint8Array(buffer);
    if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) throw new Error();
    unpacked = await readBounded(
      new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip")),
      expandedLimit,
    );
  } catch (error) {
    if (error instanceof ProfileDataError) throw error;
    throw new ProfileDataError("PROFILE_GZIP_INVALID");
  }
  const tree = parseUTF8JSON(unpacked) as FlameNode;
  try {
    validateFlameTree(tree);
  } catch {
    throw new ProfileDataError("PROFILE_TREE_INVALID");
  }
  return tree;
}
// 拒绝跨源、带用户信息、片段和反斜杠的地址；预签名参数只能在当前对象路径中临时使用。
export function signedURL(value: unknown, origin: string) {
  try {
    if (
      typeof value !== "string" ||
      value.length > 8192 ||
      /[\s\\]/.test(value)
    )
      throw new Error();
    const url = new URL(value, origin);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.origin !== origin ||
      url.username ||
      url.password ||
      url.hash ||
      !url.pathname.startsWith("/objects/") ||
      url.pathname === "/objects/"
    )
      throw new Error();
    return url.href;
  } catch {
    throw new ProfileDataError("PROFILE_URL_INVALID");
  }
}
export function expiredSignature(status: number, body: string) {
  if (![400, 403].includes(status) || body.length > ERROR_BODY_LIMIT)
    return false;
  return (
    /<Code>(?:RequestExpired|ExpiredToken|TokenRefreshRequired)<\/Code>/.test(
      body,
    ) ||
    (/<Code>AccessDenied<\/Code>/.test(body) &&
      /<Message>Request has expired<\/Message>/.test(body))
  );
}
// 只有成功完成的最新采集轮次可提供完整火焰图，旧轮次与过期/禁止下载的制品都不能回退使用。
export function selectFlameArtifact(task: Task): Artifact | undefined {
  if (
    task.analysis_status !== "SUCCESS" ||
    task.collection_status !== "COLLECTED"
  )
    return;
  const attempts = [...(task.attempts || [])]
    .filter((a) => Number.isSafeInteger(a.attempt) && a.attempt > 0)
    .sort((a, b) => b.attempt - a.attempt);
  const latest = attempts[0];
  if (
    !latest ||
    latest.status !== "COLLECTED" ||
    !/^[1-9]\d{0,18}$/.test(latest.id)
  )
    return;
  return (task.artifacts || [])
    .filter(
      (a) =>
        /^[1-9]\d{0,18}$/.test(a.id) &&
        a.attempt_id === latest.id &&
        a.kind === "RESULT" &&
        a.name === "flamegraph.json.gz" &&
        a.status === "ready" &&
        a.download_allowed !== false &&
        (!a.expires_at || Date.parse(a.expires_at) > Date.now()),
    )
    .sort((a, b) => (BigInt(a.id) > BigInt(b.id) ? -1 : 1))[0];
}
// 接口封装和调用树分别验证；HTTP 成功不代表 body.data 已满足前端读取约定。
export function parseAnalysisEnvelope(
  buffer: ArrayBuffer,
  status: number,
  headerRequestId: string,
): AnalysisResult {
  let body;
  try {
    body = parseUTF8JSON(buffer);
  } catch (error) {
    if (status < 200 || status >= 300)
      throw profileError(status, safeRequestId(headerRequestId));
    throw error;
  }
  const requestId =
    safeRequestId(body?.request_id) || safeRequestId(headerRequestId);
  if (status < 200 || status >= 300) throw profileError(status, requestId);
  const data = body?.data;
  if (
    !data ||
    typeof data !== "object" ||
    !Array.isArray(data.top_functions) ||
    !Array.isArray(data.suggestions) ||
    !data.summary ||
    typeof data.summary.unit !== "string"
  )
    throw new ProfileDataError("PROFILE_RESULT_INVALID", status, requestId);
  if (data.flamegraph) {
    try {
      validateFlameTree(data.flamegraph);
    } catch {
      throw new ProfileDataError("PROFILE_TREE_INVALID", status, requestId);
    }
  }
  return data as AnalysisResult;
}
