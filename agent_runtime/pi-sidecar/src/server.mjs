import http from "node:http";
import { pathToFileURL } from "node:url";
import { RuntimeConflict, RuntimeManager } from "./runtime.mjs";

const PORT = Number(process.env.MINI_DROP_PI_SIDECAR_PORT || 8899);
const INTERNAL_BASE = process.env.MINI_DROP_PI_INTERNAL_BASE || "http://127.0.0.1:8191";
const serverRuntimes = new WeakMap();

async function readBody(req) {
  const chunks = [];
  let length = 0;
  for await (const chunk of req) {
    length += chunk.length;
    if (length > 1024 * 1024) throw new RuntimeConflict("request body too large", 413);
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  } catch {
    throw new RuntimeConflict("invalid JSON", 400);
  }
}

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function decode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    throw new RuntimeConflict("invalid path encoding", 400);
  }
}

function authorize(req) {
  const configured = process.env.MINI_DROP_PI_INTERNAL_TOKEN || "";
  return configured && req.headers["x-internal-token"] === configured;
}

export async function createServer({ manager } = {}) {
  const runtime = manager || new RuntimeManager({
    modelRuntime: null,
    internalBase: INTERNAL_BASE,
    statePath: process.env.MINI_DROP_PI_STATE_PATH,
  });
  runtime.start();
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
      if (req.method === "GET" && url.pathname === "/internal/runtime/v1/health") {
        const stateReady = runtime.health();
        json(res, stateReady ? 200 : 503, { ok: stateReady, data: {
          status: stateReady ? "ready" : "not_ready",
          runtime_type: "pi",
          runtime_version: "pi-0.83.0",
          model_ready: Boolean(runtime.modelRuntime),
          state_ready: stateReady,
        }});
        return;
      }
      if (!authorize(req)) {
        json(res, 401, { ok: false, error: "INTERNAL_TOKEN_REQUIRED" });
        return;
      }
      await route(runtime, req, res, url.pathname);
    } catch (error) {
      const status = error instanceof RuntimeConflict ? error.status : 500;
      json(res, status, { ok: false, error: String(error?.message || error) });
    }
  });
  serverRuntimes.set(server, runtime);
  return server;
}

export async function closeServer(server) {
  const runtime = serverRuntimes.get(server);
  await new Promise((resolve, reject) => {
    if (!server.listening) {
      resolve();
      return;
    }
    server.close((error) => error ? reject(error) : resolve());
  });
  if (runtime) await runtime.stop();
  serverRuntimes.delete(server);
}

async function route(runtime, req, res, path) {
  const accepted = path.match(
    /^\/internal\/runtime\/v1\/diagnoses\/([^/]+)\/turns\/accepted\/([^/]+)$/,
  );
  if (accepted) {
    if (req.method !== "GET") return json(res, 405, { ok: false, error: "method_not_allowed" });
    const value = runtime.getAcceptedTurn(decode(accepted[1]), decode(accepted[2]));
    if (!value) return json(res, 404, { ok: false, error: "not_found" });
    return json(res, 200, { ok: true, data: value });
  }

  const turnAction = path.match(
    /^\/internal\/runtime\/v1\/diagnoses\/([^/]+)\/turns\/([^/]+)\/(seal|cancel)$/,
  );
  if (turnAction) {
    if (req.method !== "POST") return json(res, 405, { ok: false, error: "method_not_allowed" });
    const diagnosisId = decode(turnAction[1]);
    const turnId = decode(turnAction[2]);
    await readBody(req);
    const data = turnAction[3] === "seal"
      ? runtime.sealTurn(diagnosisId, turnId)
      : await runtime.cancelTurn(diagnosisId, turnId);
    return json(res, 200, { ok: true, data });
  }

  const diagnosisAction = path.match(
    /^\/internal\/runtime\/v1\/diagnoses\/([^/]+)\/(resume|turn|state)$/,
  );
  if (!diagnosisAction) return json(res, 404, { ok: false, error: "unknown_internal_route" });
  const diagnosisId = decode(diagnosisAction[1]);
  const action = diagnosisAction[2];
  if (action === "state") {
    if (req.method !== "GET") return json(res, 405, { ok: false, error: "method_not_allowed" });
    return json(res, 200, { ok: true, data: runtime.state(diagnosisId) });
  }
  if (req.method !== "POST") return json(res, 405, { ok: false, error: "method_not_allowed" });
  const body = await readBody(req);
  if (action === "resume") {
    const context = body.context || {};
    if (context.diagnosis_id !== diagnosisId) throw new RuntimeConflict("diagnosis identity mismatch", 400);
    return json(res, 200, { ok: true, data: await runtime.startOrResume(context) });
  }
  return json(res, 200, {
    ok: true,
    data: await runtime.submitTurn(diagnosisId, body),
  });
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const server = await createServer();
  server.listen(PORT, "127.0.0.1", () => {
    console.log(`[sidecar] listening on 127.0.0.1:${PORT}`);
  });
  let shutdownPromise = null;
  const shutdown = () => {
    if (!shutdownPromise) {
      shutdownPromise = closeServer(server).then(
        () => process.exit(0),
        (error) => {
          console.error(`[sidecar] shutdown failed: ${String(error)}`);
          process.exit(1);
        },
      );
    }
  };
  process.once("SIGTERM", shutdown);
  process.once("SIGINT", shutdown);
}
