import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer } from "node:http";
import { gzipSync } from "node:zlib";
import type { APIRequestContext } from "@playwright/test";
import {
  safeStageAPI,
  safeRouteFetch,
  stageArtifactBytes,
} from "./safe-stage-io.ts";

// Imported synthetic signatures and cookies only. Never load stage credentials.
const signature = "IMPORTED_SIGNATURE_MUST_NOT_APPEAR_IN_ERRORS";
const encodedArtifact = gzipSync(Buffer.from("imported-profile-gzip-bytes"));
const requests: { path: string; cookie?: string; referer?: string }[] = [];
let origin = "";
const server = createServer((request, response) => {
  const path = new URL(request.url!, "http://localhost").pathname;
  requests.push({
    path,
    cookie: request.headers.cookie,
    referer: request.headers.referer,
  });
  if (path === "/objects/redirect") {
    response.writeHead(302, { Location: "/objects/redirect-target" }).end();
  } else if (path === "/objects/stored-gzip") {
    // Object metadata can retain Content-Encoding even if a client asks for
    // identity. Integrity is over stored bytes, so the helper must not decode.
    response
      .writeHead(200, {
        "Content-Encoding": "gzip",
        "Content-Length": String(encodedArtifact.byteLength),
      })
      .end(encodedArtifact);
  } else if (path === "/objects/forbidden") {
    response.writeHead(403).end("Imported private upstream diagnostic");
  } else if (path === "/objects/overflow") {
    response.writeHead(200, { "Transfer-Encoding": "chunked" }).end("abcde");
  } else if (path === "/objects/short") {
    response.writeHead(200, { "Transfer-Encoding": "chunked" }).end("ab");
  } else if (path === "/objects/stalled") {
    response.writeHead(200, { "Transfer-Encoding": "chunked" });
    response.write("a");
  } else {
    response.writeHead(200, { "Content-Length": "3" }).end("abc");
  }
});
before(async () => {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  origin = `http://127.0.0.1:${address.port}`;
});
after(async () => {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});
function safeError(error: unknown, expected = "E2E_ARTIFACT_DOWNLOAD_FAILED") {
  assert.ok(error instanceof Error);
  assert.equal(error.message, expected);
  assert.equal(error.cause, undefined);
  assert.ok(!error.stack?.includes(signature));
  assert.ok(!error.stack?.includes("IMPORTED_COOKIE"));
  return true;
}
test("real loopback Artifact bytes match without a cookie or referrer", async () => {
  const bytes = await stageArtifactBytes(
    origin,
    `${origin}/objects/ok?X-Amz-Signature=${signature}`,
    3,
  );
  assert.equal(bytes.toString(), "abc");
  assert.equal(requests.at(-1)?.cookie, undefined);
  assert.equal(requests.at(-1)?.referer, undefined);
});
test("stored gzip encoding preserves the exact compressed Artifact bytes", async () => {
  const bytes = await stageArtifactBytes(
    origin,
    `${origin}/objects/stored-gzip?X-Amz-Signature=${signature}`,
    encodedArtifact.byteLength,
  );
  assert.deepEqual(bytes, encodedArtifact);
});
test("foreign URLs and invalid sizes are rejected before a network request", async () => {
  const count = requests.length;
  for (const url of [
    "https://foreign.invalid/objects/a",
    origin + "/api/v1/auth/logout",
    origin.replace("http://", "http://user:secret@") + "/objects/a",
    origin + "/objects/a#fragment",
  ])
    await assert.rejects(stageArtifactBytes(origin, url, 3), safeError);
  await assert.rejects(
    stageArtifactBytes(origin, origin + "/objects/ok", 256 * 1024 * 1024 + 1),
    safeError,
  );
  assert.equal(requests.length, count);
});
test("redirects are refused and never followed", async () => {
  await assert.rejects(
    stageArtifactBytes(origin, origin + "/objects/redirect", 3),
    safeError,
  );
  assert.ok(!requests.some((r) => r.path === "/objects/redirect-target"));
});
test("HTTP failures and bounded body mismatches expose no signature", async () => {
  for (const path of ["forbidden", "overflow", "short"])
    await assert.rejects(
      stageArtifactBytes(
        origin,
        `${origin}/objects/${path}?X-Amz-Signature=${signature}`,
        3,
      ),
      safeError,
    );
});
test("a stalled body obeys its total deadline and redacts the native error", async () => {
  await assert.rejects(
    stageArtifactBytes(
      origin,
      `${origin}/objects/stalled?X-Amz-Signature=${signature}`,
      3,
      30,
    ),
    safeError,
  );
});
test("API and routed request exceptions cannot leak Playwright Call logs", async () => {
  const context = {
    async get() {
      throw new Error(`Call log: ${signature} Cookie: IMPORTED_COOKIE`);
    },
  } as unknown as APIRequestContext;
  await assert.rejects(safeStageAPI(context).get("/api/v1/tasks"), (error) =>
    safeError(error, "E2E_API_REQUEST_FAILED"),
  );
  await assert.rejects(
    safeRouteFetch(async () => {
      throw new Error(`Call log: ${signature} Cookie: IMPORTED_COOKIE`);
    }),
    (error) => safeError(error, "E2E_ROUTE_FETCH_FAILED"),
  );
});
