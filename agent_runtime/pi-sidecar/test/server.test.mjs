import { after, before, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { Value } from "typebox/value";
import { createServer } from "../src/server.mjs";
import { RuntimeManager } from "../src/runtime.mjs";
import { ALLOWED_TOOL_NAMES, buildToolCatalog } from "../src/tools.mjs";

class FakeSession {
  constructor() {
    this.subscribers = new Set();
    this.prompts = [];
    this.promptBehavior = "pending";
    this.pendingPrompts = [];
    this.abortCalls = 0;
    this.abortArgumentCounts = [];
    this.abortError = null;
    this.disposeCalls = 0;
  }

  subscribe(callback) {
    this.subscribers.add(callback);
    return () => this.subscribers.delete(callback);
  }

  prompt(value, options) {
    this.prompts.push({ value, options });
    if (this.promptBehavior === "throw") throw new Error("synchronous prompt failure");
    if (this.promptBehavior === "reject") return Promise.reject(new Error("asynchronous prompt failure"));
    if (this.promptBehavior === "resolve") return Promise.resolve();
    return new Promise((resolve, reject) => {
      this.pendingPrompts.push({ resolve, reject });
    });
  }

  async abort() {
    this.abortCalls += 1;
    this.abortArgumentCounts.push(arguments.length);
    if (this.abortError) throw this.abortError;
  }

  dispose() {
    this.disposeCalls += 1;
  }

  emit(event) {
    for (const callback of [...this.subscribers]) callback(event);
  }
}

const sessions = [];
const manager = new RuntimeManager({
  internalBase: "http://127.0.0.1:1",
  sessionFactory: () => {
    const session = new FakeSession();
    sessions.push(session);
    return session;
  },
});
let server;
let base;

function headers(token = "sidecar-token") {
  return {
    "Content-Type": "application/json",
    "X-Internal-Token": token,
  };
}

function context(diagnosisId = "diag-a", generation = 1) {
  return {
    diagnosis_id: diagnosisId,
    case_id: "case-public",
    runtime_generation: generation,
    case_goal: "find the verified cause",
    target_scope: {},
  };
}

function turn(overrides = {}) {
  return {
    diagnosis_id: "diag-a",
    turn_id: "turn-authoritative",
    runtime_generation: 1,
    message: "diagnose from verified evidence",
    references: [],
    requested_mode: null,
    client_command_id: "command-a",
    ...overrides,
  };
}

async function request(path, { method = "GET", body, token = "sidecar-token" } = {}) {
  const options = { method, headers: headers(token) };
  if (body !== undefined) options.body = JSON.stringify(body);
  const response = await fetch(`${base}${path}`, options);
  return { response, payload: await response.json() };
}

async function resume(value = context()) {
  return request(
    `/internal/runtime/v1/diagnoses/${encodeURIComponent(value.diagnosis_id)}/resume`,
    { method: "POST", body: { context: value } },
  );
}

async function submit(value = turn(), shadow = true) {
  return request(
    `/internal/runtime/v1/diagnoses/${encodeURIComponent(value.diagnosis_id)}/turn`,
    { method: "POST", body: { turn: value, shadow } },
  );
}

before(async () => {
  process.env.MINI_DROP_PI_INTERNAL_TOKEN = "sidecar-token";
  server = await createServer({ manager });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

beforeEach(() => {
  manager.sessions.clear();
  manager.acceptedCommands.clear();
  sessions.length = 0;
});

after(async () => {
  delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
});

test("health is narrow and raw Pi RPC remains unavailable", async () => {
  const health = await fetch(`${base}/internal/runtime/v1/health`);
  assert.equal(health.status, 200);
  assert.equal((await health.json()).data.runtime_type, "pi");

  const raw = await fetch(`${base}/raw/rpc`, { method: "POST" });
  assert.equal(raw.status, 401);
  const authenticated = await request("/raw/rpc", { method: "POST" });
  assert.equal(authenticated.response.status, 404);
});

test("diagnosis routes fail closed without the configured token", async () => {
  const { response, payload } = await request(
    "/internal/runtime/v1/diagnoses/diag-a/state",
    { token: "wrong-token" },
  );
  assert.equal(response.status, 401);
  assert.equal(payload.ok, false);
});

test("resume binds exact diagnosis and generation", async () => {
  const { response, payload } = await resume();
  assert.equal(response.status, 200);
  assert.deepEqual(payload.data, {
    diagnosis_id: "diag-a",
    runtime_session_id: "pi:diag-a:1",
    runtime_generation: 1,
    runtime_type: "pi",
    runtime_version: "pi-0.83.0",
  });

  const mismatch = await request(
    "/internal/runtime/v1/diagnoses/diag-b/resume",
    { method: "POST", body: { context: context("diag-a") } },
  );
  assert.equal(mismatch.response.status, 400);
});

test("submit preserves authoritative turn identity and supports accepted lookup", async () => {
  await resume();
  const first = await submit();
  assert.equal(first.response.status, 200);
  assert.equal(first.payload.data.turn_id, "turn-authoritative");
  assert.equal(first.payload.data.mode, "pi_shadow");

  const lookup = await request(
    "/internal/runtime/v1/diagnoses/diag-a/turns/accepted/command-a",
  );
  assert.equal(lookup.response.status, 200);
  assert.equal(lookup.payload.data.turn_id, "turn-authoritative");

  const missing = await request(
    "/internal/runtime/v1/diagnoses/diag-a/turns/accepted/missing",
  );
  assert.equal(missing.response.status, 404);
});

test("command replay ignores retry-local turn id but rejects immutable changes", async () => {
  await resume();
  await submit();
  const replay = await submit(turn({ turn_id: "retry-local-id" }));
  assert.equal(replay.response.status, 200);
  assert.equal(replay.payload.data.turn_id, "turn-authoritative");

  const conflict = await submit(turn({ turn_id: "another-id", message: "changed" }));
  assert.equal(conflict.response.status, 409);
  assert.match(conflict.payload.error, /different request/);
});

test("stale generation is rejected and rotation fences old turn closures", async () => {
  await resume();
  await submit(turn(), false);
  const oldSession = sessions[0];
  const rotated = await resume(context("diag-a", 2));
  assert.equal(rotated.response.status, 200);
  assert.equal(oldSession.abortCalls, 1);
  assert.deepEqual(oldSession.abortArgumentCounts, [0]);
  assert.equal(oldSession.disposeCalls, 1);
  assert.equal(oldSession.subscribers.size, 0);

  const stale = await submit(turn({ turn_id: "turn-stale" }), true);
  assert.equal(stale.response.status, 409);
  const oldLookup = await request(
    "/internal/runtime/v1/diagnoses/diag-a/turns/accepted/command-a",
  );
  assert.equal(oldLookup.response.status, 404);
});

test("events keep immutable turn ownership and stop after seal", async () => {
  await resume();
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await submit(turn(), false);
    sessions[0].emit({
      type: "message_end",
      message: {
        role: "assistant",
        content: [
          { type: "thinking", thinking: "private" },
          { type: "text", text: "Evidence is insufficient" },
        ],
      },
    });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /diagnoses\/diag-a\/turns\/turn-authoritative\/events$/);
    const event = JSON.parse(calls[0].options.body);
    assert.equal(event.runtime_generation, 1);
    assert.equal(event.events[0].event_seq, 1);
    assert.doesNotMatch(JSON.stringify(event), /private/);

    const sealed = await request(
      "/internal/runtime/v1/diagnoses/diag-a/turns/turn-authoritative/seal",
      { method: "POST", body: {} },
    );
    assert.equal(sealed.response.status, 200);
    sessions[0].emit({ type: "turn_end", text: "late" });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(calls.length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("cancel seals the exact turn and aborts its session", async () => {
  await resume();
  await submit(turn(), false);
  const cancelled = await request(
    "/internal/runtime/v1/diagnoses/diag-a/turns/turn-authoritative/cancel",
    { method: "POST", body: { reason: "operator stopped" } },
  );
  assert.equal(cancelled.response.status, 200);
  assert.deepEqual(cancelled.payload.data, { sealed: true, cancelled: true });
  assert.equal(sessions[0].abortCalls, 1);
  assert.deepEqual(sessions[0].abortArgumentCounts, [0]);
  assert.equal(sessions[0].subscribers.size, 0);
  assert.equal(manager.sessions.get("diag-a").activeTurnId, null);
});

test("only one live model turn is accepted and seal releases ownership", async () => {
  await resume();
  await submit(turn(), false);
  assert.equal(manager.sessions.get("diag-a").activeTurnId, "turn-authoritative");
  assert.equal(sessions[0].subscribers.size, 1);

  const overlapping = await submit(turn({
    turn_id: "turn-overlapping",
    client_command_id: "command-overlapping",
  }), false);
  assert.equal(overlapping.response.status, 409);
  assert.match(overlapping.payload.error, /another model turn is active/);

  const sealed = await request(
    "/internal/runtime/v1/diagnoses/diag-a/turns/turn-authoritative/seal",
    { method: "POST", body: {} },
  );
  assert.equal(sealed.response.status, 200);
  assert.equal(manager.sessions.get("diag-a").activeTurnId, null);
  assert.equal(sessions[0].subscribers.size, 0);

  const next = await submit(turn({
    turn_id: "turn-next",
    client_command_id: "command-next",
  }), false);
  assert.equal(next.response.status, 200);
  assert.equal(manager.sessions.get("diag-a").activeTurnId, "turn-next");
  assert.equal(sessions[0].subscribers.size, 1);
});

test("prompt completion and failures release active ownership", async () => {
  for (const behavior of ["resolve", "throw", "reject"]) {
    manager.sessions.clear();
    manager.acceptedCommands.clear();
    sessions.length = 0;
    await resume(context(`diag-${behavior}`));
    sessions[0].promptBehavior = behavior;
    const value = turn({
      diagnosis_id: `diag-${behavior}`,
      turn_id: `turn-${behavior}`,
      client_command_id: `command-${behavior}`,
    });
    const submitted = await submit(value, false);
    assert.equal(submitted.response.status, 200);
    await new Promise((resolve) => setImmediate(resolve));
    const entry = manager.sessions.get(`diag-${behavior}`);
    assert.equal(entry.activeTurnId, null);
    assert.equal(sessions[0].subscribers.size, 0);
    assert.equal(sessions[0].prompts.length, 1);
    assert.deepEqual(sessions[0].prompts[0].options, {
      expandPromptTemplates: false,
      source: "rpc",
    });
    if (behavior === "resolve") {
      assert.equal(entry.lastError, "");
    } else {
      assert.match(entry.lastError, /prompt failed/);
    }
  }
});

test("events are observed by the current turn only", async () => {
  await resume();
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await submit(turn(), false);
    sessions[0].emit({ type: "turn_start" });
    await new Promise((resolve) => setImmediate(resolve));
    manager.sealTurn("diag-a", "turn-authoritative");
    await submit(turn({
      turn_id: "turn-next",
      client_command_id: "command-next",
    }), false);
    sessions[0].emit({ type: "turn_end" });
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(calls.length, 2);
    assert.match(calls[0].url, /turns\/turn-authoritative\/events$/);
    assert.match(calls[1].url, /turns\/turn-next\/events$/);
    assert.equal(sessions[0].subscribers.size, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("abort rejection is awaited and recorded without reopening the turn", async () => {
  await resume();
  await submit(turn(), false);
  sessions[0].abortError = new Error("abort unavailable");
  const cancelled = await request(
    "/internal/runtime/v1/diagnoses/diag-a/turns/turn-authoritative/cancel",
    { method: "POST", body: { reason: "operator stopped" } },
  );
  assert.equal(cancelled.response.status, 200);
  const entry = manager.sessions.get("diag-a");
  assert.equal(entry.activeTurnId, null);
  assert.equal(entry.turns.get("turn-authoritative").sealed, true);
  assert.equal(entry.turns.get("turn-authoritative").cancelled, true);
  assert.match(entry.lastError, /abort failed: Error: abort unavailable/);
  assert.equal(sessions[0].abortCalls, 1);
  assert.deepEqual(sessions[0].abortArgumentCounts, [0]);
});

test("state exposes active ownership and prompt failure", async () => {
  await resume();
  await submit(turn(), false);
  const running = await request("/internal/runtime/v1/diagnoses/diag-a/state");
  assert.deepEqual(running.payload.data, {
    diagnosis_id: "diag-a",
    runtime_session_id: "pi:diag-a:1",
    runtime_generation: 1,
    status: "RUNNING",
    active_turn_id: "turn-authoritative",
    last_error: null,
  });

  sessions[0].pendingPrompts[0].reject(new Error("model stream failed"));
  await new Promise((resolve) => setImmediate(resolve));
  const ready = await request("/internal/runtime/v1/diagnoses/diag-a/state");
  assert.equal(ready.payload.data.status, "READY");
  assert.equal(ready.payload.data.active_turn_id, null);
  assert.match(ready.payload.data.last_error, /model stream failed/);
});

test("tool catalog is diagnosis-bound and read-only", async () => {
  const tools = buildToolCatalog({ diagnosisId: "diag-bound", internalBase: "http://tools" });
  assert.deepEqual(tools.map((item) => item.name).sort(), [...ALLOWED_TOOL_NAMES].sort());
  for (const forbidden of ["bash", "read", "write", "edit", "request_operation", "finish_investigation"]) {
    assert.ok(!tools.some((item) => item.name === forbidden));
  }

  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({ ok: true, data: { message: "诊断完成" } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const result = await tools[0].execute("call-a", {});
    const body = JSON.parse(calls[0].options.body);
    assert.equal(body.diagnosis_id, "diag-bound");
    assert.equal(calls[0].options.headers["X-Internal-Token"], "sidecar-token");
    assert.equal(result.content[0].text, JSON.stringify({ message: "诊断完成" }));
    assert.equal(
      result.details.projection_bytes,
      Buffer.byteLength(result.content[0].text, "utf8"),
    );
    assert.ok(!result.content[0].text.includes('"ok"'));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("tool response contract rejects malformed, rejected, and oversize responses", async () => {
  const [tool] = buildToolCatalog({ diagnosisId: "diag-bound", internalBase: "http://tools" });
  const originalFetch = globalThis.fetch;
  const cases = [
    {
      response: new Response("not-json", { status: 200 }),
      error: /returned invalid JSON/,
    },
    {
      response: new Response(JSON.stringify({ ok: true }), { status: 200 }),
      error: /invalid response envelope/,
    },
    {
      response: new Response(JSON.stringify({ ok: true, data: {}, extra: true }), { status: 200 }),
      error: /invalid response envelope/,
    },
    {
      response: new Response(JSON.stringify({ ok: false, data: {} }), { status: 200 }),
      error: /rejected response envelope/,
    },
    {
      response: new Response(JSON.stringify({ ok: false, data: {} }), { status: 409 }),
      error: /failed: HTTP 409/,
    },
    {
      response: new Response(JSON.stringify({ ok: true, data: "测".repeat(43691) }), { status: 200 }),
      error: /projection exceeds 131072 UTF-8 bytes/,
    },
  ];
  try {
    for (const item of cases) {
      globalThis.fetch = async () => item.response;
      await assert.rejects(() => tool.execute("call-a", {}), item.error);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("tool parameters match the closed Python request contract", () => {
  const byName = Object.fromEntries(
    buildToolCatalog({ diagnosisId: "diag-bound" }).map((tool) => [tool.name, tool]),
  );
  const list = byName.list_diagnosis_evidence.parameters;
  assert.equal(Value.Check(list, { filters: {} }), true);
  assert.equal(Value.Check(list, { filters: { source_type: "metric" }, cursor: "ev-a" }), true);
  assert.equal(Value.Check(list, { filters: { reviewer_id: "private" } }), false);
  assert.equal(Value.Check(list, { cursor: "" }), false);
  assert.equal(Value.Check(list, { unexpected: true }), false);

  const projection = byName.get_evidence_projection.parameters;
  assert.equal(Value.Check(projection, { evidence_ids: ["a"], projection_kinds: ["signal"] }), true);
  assert.equal(Value.Check(projection, { evidence_ids: ["a".repeat(129)] }), false);
  assert.equal(Value.Check(projection, { evidence_ids: ["a"], projection_kinds: ["private"] }), false);
  assert.equal(Value.Check(projection, { evidence_ids: ["a"], unexpected: true }), false);

  const comparison = byName.compare_evidence.parameters;
  assert.equal(Value.Check(comparison, { evidence_ids: ["a", "b"], dimensions: ["source"] }), true);
  assert.equal(Value.Check(comparison, { evidence_ids: ["a", "b"], dimensions: ["private"] }), false);
  assert.equal(Value.Check(comparison, { evidence_ids: ["a"] }), false);
});

test("diagnosis tools fail closed when the internal token is absent", async () => {
  const [tool] = buildToolCatalog({ diagnosisId: "diag-bound", internalBase: "http://tools" });
  const originalToken = process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  try {
    await assert.rejects(
      () => tool.execute("call-a", {}),
      /MINI_DROP_PI_INTERNAL_TOKEN is required/,
    );
  } finally {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = originalToken;
  }
});

test("invalid JSON, methods, and unknown state have explicit status codes", async () => {
  const invalid = await fetch(`${base}/internal/runtime/v1/diagnoses/diag-a/resume`, {
    method: "POST",
    headers: headers(),
    body: "{",
  });
  assert.equal(invalid.status, 400);

  const method = await request(
    "/internal/runtime/v1/diagnoses/diag-a/resume",
    { method: "GET" },
  );
  assert.equal(method.response.status, 405);

  const unknown = await request(
    "/internal/runtime/v1/diagnoses/diag-a/state",
  );
  assert.equal(unknown.response.status, 404);
});
