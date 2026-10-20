/**
 * 真实隔离环境验收的安全 I/O 辅助层：将网络异常转换为固定错误，避免测试框架附带敏感请求日志。
 */
import type { APIRequestContext } from "@playwright/test";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import type { ClientRequest, IncomingMessage } from "node:http";

// 真实环境的测试报告不能接收 Playwright 请求日志，其中可能含 Cookie、
// CSRF 头或对象存储预签名 URL；错误包装也不能添加会泄漏原异常的 cause。
export function safeStageAPI(context: APIRequestContext): APIRequestContext {
  const methods = new Set([
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "fetch",
  ]);
  return new Proxy(context, {
    get(target, property) {
      const value = Reflect.get(target, property);
      if (typeof value !== "function") return value;
      if (!methods.has(String(property))) return value.bind(target);
      return async (...args: unknown[]) => {
        try {
          return await value.apply(target, args);
        } catch {
          throw new Error("E2E_API_REQUEST_FAILED");
        }
      };
    },
  });
}

export async function stageArtifactBytes(
  baseURL: string,
  signedURL: unknown,
  expectedSize: number,
  deadlineMs = 30000,
): Promise<Buffer> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), deadlineMs);
  let request: ClientRequest | undefined;
  let response: IncomingMessage | undefined;
  try {
    if (
      !Number.isSafeInteger(expectedSize) ||
      expectedSize <= 0 ||
      expectedSize > 256 * 1024 * 1024 ||
      typeof signedURL !== "string" ||
      signedURL.length > 8192 ||
      /[\s\\]/.test(signedURL)
    )
      throw new Error();
    const base = new URL(baseURL),
      url = new URL(signedURL, base);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.origin !== base.origin ||
      url.username ||
      url.password ||
      url.hash ||
      !url.pathname.startsWith("/objects/") ||
      url.pathname === "/objects/"
    )
      throw new Error();
    // 使用原生 HTTP 流保留 S3 原始字节，包括标有 Content-Encoding:gzip 的对象；
    // fetch 会透明解压，无法用于这一层的存储字节校验。
    // 不附带 Cookie 容器、不跟随重定向、不发送来源信息，也不让 Playwright 记录请求步骤。
    response = await new Promise<IncomingMessage>((resolve, reject) => {
      request = (url.protocol === "https:" ? httpsRequest : httpRequest)(
        url,
        {
          method: "GET",
          agent: false,
          signal: controller.signal,
          maxHeaderSize: 16384,
          headers: { "Accept-Encoding": "identity" },
        },
        resolve,
      );
      request.once("error", reject);
      request.end();
    });
    if (response.statusCode !== 200) throw new Error();
    const length = response.headers["content-length"];
    if (length && (!/^\d+$/.test(length) || Number(length) !== expectedSize))
      throw new Error();
    const chunks: Buffer[] = [];
    let size = 0;
    for await (const chunk of response) {
      size += chunk.byteLength;
      if (size > expectedSize) throw new Error();
      chunks.push(Buffer.from(chunk));
    }
    if (size !== expectedSize || !response.complete) throw new Error();
    return Buffer.concat(chunks, size);
  } catch {
    throw new Error("E2E_ARTIFACT_DOWNLOAD_FAILED");
  } finally {
    controller.abort();
    response?.destroy();
    request?.destroy();
    clearTimeout(timer);
  }
}

export async function safeRouteFetch<T>(
  operation: () => Promise<T>,
): Promise<T> {
  try {
    return await operation();
  } catch {
    throw new Error("E2E_ROUTE_FETCH_FAILED");
  }
}
