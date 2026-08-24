import { after, before, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Value } from "typebox/value";
import { closeServer, createServer } from "../src/server.mjs";
import { RuntimeManager } from "../src/runtime.mjs";
import { SidecarStateStore } from "../src/state-store.mjs";
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
  statePath: ":memory:",
  sessionFactory: () => {
    const session = new FakeSession();
    sessions.push(session);
    return session;
  },
});
let server;
let base;
const originalSessionFactory = manager.sessionFactory;

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

async function waitFor(predicate, message, timeoutMilliseconds = 2_000) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail(message);
}

async function waitForDelivered(diagnosisId, turnId) {
  await waitFor(
    () => manager.stateStore.getTurnState(diagnosisId, turnId)?.lifecycleStatus === "DELIVERED",
    `callback delivery did not settle for ${diagnosisId}/${turnId}`,
  );
}

function acceptedRecord(diagnosisId = "diag-store", generation = 1) {
  const commandId = "command-store";
  const turnId = "turn-store";
  const accepted = {
    diagnosis_id: diagnosisId,
    turn_id: turnId,
    runtime_session_id: `pi:${diagnosisId}:${generation}`,
    runtime_generation: generation,
    mode: "pi",
  };
  return {
    diagnosisId,
    commandId,
    generation,
    turnId,
    request: JSON.stringify({ diagnosis_id: diagnosisId, client_command_id: commandId }),
    accepted,
  };
}

function createAcceptedStore(path = ":memory:") {
  const store = new SidecarStateStore(path);
  const record = acceptedRecord();
  assert.equal(store.bind(context(record.diagnosisId, record.generation)).outcome, "created");
  assert.equal(store.accept(record).outcome, "created");
  return { store, record };
}

before(async () => {
  process.env.MINI_DROP_PI_INTERNAL_TOKEN = "sidecar-token";
  server = await createServer({ manager });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

beforeEach(async () => {
  await manager.stop();
  manager.sessions.clear();
  manager.acceptedCommands.clear();
  manager.stateStore.clearForTests();
  manager.sessionFactory = originalSessionFactory;
  sessions.length = 0;
  manager.start();
});

after(async () => {
  delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  await closeServer(server);
});

test("state store preserves callback identity, order, retries, leases, and settlement", () => {
  const { store, record } = createAcceptedStore();
  try {
    const first = store.enqueueEvent({
      diagnosisId: record.diagnosisId,
      turnId: record.turnId,
      generation: record.generation,
      eventType: "assistant.delta",
      payload: { text: "first" },
    });
    const second = store.enqueueEvent({
      diagnosisId: record.diagnosisId,
      turnId: record.turnId,
      generation: record.generation,
      eventType: "assistant.delta",
      payload: { text: "second" },
    });
    const terminal = store.enqueueTerminal({
      diagnosisId: record.diagnosisId,
      turnId: record.turnId,
      generation: record.generation,
      terminalStatus: "COMPLETED",
      finalMessage: { text: "done" },
    });

    assert.equal(first.eventSeq, 1);
    assert.equal(second.eventSeq, 2);
    assert.equal(terminal.ordinal, 3);
    assert.deepEqual(first.body, {
      runtime_session_id: "pi:diag-store:1",
      runtime_generation: 1,
      events: [{
        event_id: "evt:diag-store:turn-store:1:1",
        event_seq: 1,
        event_type: "assistant.delta",
        payload: { text: "first" },
      }],
    });

    const firstClaim = store.claimNextCallback(1_000, 50);
    assert.equal(firstClaim.callbackId, first.callbackId);
    assert.equal(firstClaim.attemptCount, 1);
    assert.deepEqual(firstClaim.body, first.body);
    assert.equal(store.claimNextCallback(1_000, 50), null);

    assert.equal(store.retryCallback(first.callbackId, 1_100, "temporary"), true);
    assert.equal(store.claimNextCallback(1_099, 50), null);
    const retry = store.claimNextCallback(1_100, 50);
    assert.equal(retry.callbackId, first.callbackId);
    assert.equal(retry.attemptCount, 2);
    assert.deepEqual(retry.body, first.body);
    assert.equal(store.claimNextCallback(1_149, 50), null);
    const reclaimed = store.claimNextCallback(1_150, 50);
    assert.equal(reclaimed.callbackId, first.callbackId);
    assert.equal(reclaimed.attemptCount, 3);
    assert.deepEqual(reclaimed.body, first.body);

    store.acknowledgeCallback(first.callbackId);
    const secondClaim = store.claimNextCallback(1_150, 50);
    assert.equal(secondClaim.callbackId, second.callbackId);
    store.acknowledgeCallback(second.callbackId);
    const terminalClaim = store.claimNextCallback(1_150, 50);
    assert.equal(terminalClaim.callbackId, terminal.callbackId);
    assert.equal(store.getTurnState(record.diagnosisId, record.turnId).lifecycleStatus, "CALLBACK_PENDING");
    store.acknowledgeCallback(terminal.callbackId);
    assert.equal(store.getTurnState(record.diagnosisId, record.turnId).lifecycleStatus, "DELIVERED");
    assert.equal(store.liveTurn(record.diagnosisId, record.generation), null);
  } finally {
    store.close();
  }
});

test("state store blocks permanent callback failure and retains live ownership", () => {
  const { store, record } = createAcceptedStore();
  try {
    const event = store.enqueueEvent({
      diagnosisId: record.diagnosisId,
      turnId: record.turnId,
      generation: record.generation,
      eventType: "assistant.delta",
      payload: { text: "blocked" },
    });
    const terminal = store.enqueueTerminal({
      diagnosisId: record.diagnosisId,
      turnId: record.turnId,
      generation: record.generation,
      terminalStatus: "FAILED",
      finalMessage: { text: "failed" },
    });
    const claim = store.claimNextCallback(2_000, 50);
    assert.equal(claim.callbackId, event.callbackId);
    assert.equal(store.blockCallback(event.callbackId, "HTTP 409"), true);
    assert.equal(store.claimNextCallback(10_000, 50), null);
    assert.deepEqual(store.liveTurn(record.diagnosisId, record.generation), {
      turnId: record.turnId,
      lifecycleStatus: "RECOVERY_BLOCKED",
    });
    assert.equal(store.getTurnState(record.diagnosisId, record.turnId).lifecycleStatus, "RECOVERY_BLOCKED");
    assert.equal(store.acknowledgeCallback(terminal.callbackId).terminal, true);
    assert.equal(store.getTurnState(record.diagnosisId, record.turnId).lifecycleStatus, "DELIVERED");
  } finally {
    store.close();
  }
});

test("generation rotation fences an in-flight callback from the new binding", () => {
  const { store, record } = createAcceptedStore();
  try {
    const terminal = store.enqueueTerminal({
      diagnosisId: record.diagnosisId,
      turnId: record.turnId,
      generation: record.generation,
      terminalStatus: "COMPLETED",
      finalMessage: { text: "old generation" },
    });
    const claimed = store.claimNextCallback(1_000, 50);
    assert.equal(claimed.callbackId, terminal.callbackId);

    assert.equal(
      store.bind(context(record.diagnosisId, record.generation + 1)).outcome,
      "rotated",
    );
    assert.equal(store.acknowledgeCallback(claimed.callbackId), null);
    assert.equal(store.retryCallback(claimed.callbackId, 2_000, "late failure"), false);
    assert.equal(store.blockCallback(claimed.callbackId, "late rejection"), false);
    assert.equal(store.getTurnState(record.diagnosisId, record.turnId), null);
    assert.equal(store.getAccepted(record.diagnosisId, record.commandId), null);

    const next = acceptedRecord(record.diagnosisId, record.generation + 1);
    assert.equal(store.accept(next).outcome, "created");
    assert.deepEqual(
      store.getAccepted(record.diagnosisId, next.commandId).accepted,
      next.accepted,
    );
  } finally {
    store.close();
  }
});

test("state store migrates schema one and rejects unknown versions before creating runtime tables", () => {
  const directory = mkdtempSync(join(tmpdir(), "mini-drop-sidecar-schema-"));
  try {
    const legacyPath = join(directory, "legacy.sqlite3");
    const legacy = new DatabaseSync(legacyPath);
    legacy.exec(`
      CREATE TABLE schema_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL
      );
      INSERT INTO schema_metadata (singleton, schema_version) VALUES (1, 1);
    `);
    legacy.close();
    const migrated = new SidecarStateStore(legacyPath);
    assert.equal(migrated.health(), true);
    assert.equal(migrated.database.prepare(
      "SELECT schema_version FROM schema_metadata WHERE singleton = 1",
    ).get().schema_version, 2);
    assert.equal(migrated.database.prepare(
      "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = 'callback_outbox'",
    ).get().count, 1);
    migrated.close();

    const unknownPath = join(directory, "unknown.sqlite3");
    const unknown = new DatabaseSync(unknownPath);
    unknown.exec(`
      CREATE TABLE schema_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL
      );
      INSERT INTO schema_metadata (singleton, schema_version) VALUES (1, 99);
    `);
    unknown.close();
    assert.throws(() => new SidecarStateStore(unknownPath), /unsupported Sidecar state schema: 99/);
    const inspected = new DatabaseSync(unknownPath);
    assert.equal(inspected.prepare(
      "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = 'runtime_bindings'",
    ).get().count, 0);
    inspected.close();
  } finally {
    rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 20 });
  }
});

test("runtime manager start is idempotent and supports restart", async () => {
  const runtime = new RuntimeManager({
    internalBase: "http://127.0.0.1:1",
    statePath: ":memory:",
    sessionFactory: () => new FakeSession(),
  });
  const recoverOrphanedTurns = runtime.stateStore.recoverOrphanedTurns.bind(
    runtime.stateStore,
  );
  let recoveryCalls = 0;
  runtime.stateStore.recoverOrphanedTurns = () => {
    recoveryCalls += 1;
    return recoverOrphanedTurns();
  };

  try {
    assert.equal(runtime.stopped, true);
    runtime.start();
    const firstTimer = runtime.deliveryTimer;
    assert.notEqual(firstTimer, null);
    runtime.start();
    assert.equal(runtime.deliveryTimer, firstTimer);
    assert.equal(recoveryCalls, 1);

    await runtime.stop();
    assert.equal(runtime.stopped, true);
    assert.equal(runtime.deliveryTimer, null);

    runtime.start();
    assert.equal(runtime.stopped, false);
    assert.notEqual(runtime.deliveryTimer, null);
    assert.equal(recoveryCalls, 2);
  } finally {
    await runtime.stop();
    runtime.stateStore.close();
  }
});

test("restart recovery journals one deterministic terminal without rerunning the prompt", () => {
  const directory = mkdtempSync(join(tmpdir(), "mini-drop-sidecar-recovery-"));
  const statePath = join(directory, "state.sqlite3");
  try {
    const { store, record } = createAcceptedStore(statePath);
    store.close();

    const restarted = new SidecarStateStore(statePath);
    assert.deepEqual(restarted.recoverOrphanedTurns(), [{
      diagnosisId: record.diagnosisId,
      turnId: record.turnId,
    }]);
    assert.deepEqual(restarted.recoverOrphanedTurns(), []);
    const state = restarted.getTurnState(record.diagnosisId, record.turnId);
    assert.equal(state.lifecycleStatus, "CALLBACK_PENDING");
    assert.equal(state.terminalStatus, "FAILED");
    assert.deepEqual(state.finalMessage, {
      text: "Pi Runtime process restarted before prompt settlement",
      recovery_reason: "SIDECAR_RESTART",
    });
    const terminal = restarted.claimNextCallback(3_000, 50);
    assert.equal(terminal.kind, "TERMINAL");
    assert.equal(terminal.ordinal, 1);
    assert.equal(restarted.claimNextCallback(3_000, 50), null);
    restarted.retryCallback(terminal.callbackId, 4_000, "defer cleanup");
    restarted.close();
  } finally {
    rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 20 });
  }
});

test("closeServer waits for runtime callback delivery to stop", async () => {
  let releaseDelivery;
  const delivery = new Promise((resolve) => {
    releaseDelivery = resolve;
  });
  const runtime = new RuntimeManager({
    internalBase: "http://127.0.0.1:1",
    statePath: ":memory:",
    sessionFactory: () => new FakeSession(),
  });
  runtime.deliveryPromise = delivery;
  const isolatedServer = await createServer({ manager: runtime });
  await new Promise((resolve) => isolatedServer.listen(0, "127.0.0.1", resolve));

  let closed = false;
  const closing = closeServer(isolatedServer).then(() => {
    closed = true;
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(closed, false);
  releaseDelivery();
  await closing;
  assert.equal(runtime.stopped, true);
  assert.equal(isolatedServer.listening, false);
  runtime.stateStore.close();
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

test("accepted command survives manager restart and rotation remains fenced", async () => {
  const directory = mkdtempSync(join(tmpdir(), "mini-drop-sidecar-"));
  const statePath = join(directory, "state.sqlite3");
  const createManager = () => new RuntimeManager({
    internalBase: "http://127.0.0.1:1",
    statePath,
    sessionFactory: () => new FakeSession(),
  });
  const first = createManager();
  try {
    await first.startOrResume(context());
    const accepted = await first.submitTurn("diag-a", { turn: turn(), shadow: true });
    first.stateStore.close();

    const restarted = createManager();
    await restarted.startOrResume(context());
    assert.deepEqual(restarted.getAcceptedTurn("diag-a", "command-a"), accepted);
    const replay = await restarted.submitTurn("diag-a", {
      turn: turn({ turn_id: "retry-after-restart" }),
      shadow: true,
    });
    assert.equal(replay.turn_id, "turn-authoritative");
    await assert.rejects(
      restarted.submitTurn("diag-a", {
        turn: turn({ turn_id: "changed-after-restart", message: "changed" }),
        shadow: true,
      }),
      /different request/,
    );
    await restarted.startOrResume(context("diag-a", 2));
    assert.equal(restarted.getAcceptedTurn("diag-a", "command-a"), null);
    restarted.stateStore.close();

    const rotatedRestart = createManager();
    await rotatedRestart.startOrResume(context("diag-a", 2));
    assert.equal(rotatedRestart.getAcceptedTurn("diag-a", "command-a"), null);
    await assert.rejects(rotatedRestart.startOrResume(context()), /stale runtime generation/);
    rotatedRestart.stateStore.close();
  } finally {
    rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 20 });
  }
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
    await waitFor(() => calls.length === 1, "event callback was not delivered");
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

test("callback delivery retries transient failures with stable event identity", async () => {
  await resume();
  const calls = [];
  const originalFetch = globalThis.fetch;
  let eventAttempts = 0;
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    calls.push({ url: String(url), options });
    if (/\/events$/.test(String(url))) {
      eventAttempts += 1;
      return new Response(JSON.stringify({ ok: eventAttempts >= 3 }), {
        status: eventAttempts >= 3 ? 200 : 503,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await submit(turn(), false);
    sessions[0].emit({ type: "turn_start", text: "start" });
    const turnContext = manager.sessions.get("diag-a").turns.get("turn-authoritative");
    await turnContext.eventTail;
    sessions[0].pendingPrompts[0].resolve();
    await turnContext.completion;
    await waitForDelivered("diag-a", "turn-authoritative");

    const events = calls.filter((item) => /\/events$/.test(item.url));
    assert.equal(events.length, 3);
    assert.equal(new Set(events.map((item) => item.options.body)).size, 1);
    const event = JSON.parse(events[0].options.body).events[0];
    assert.equal(event.event_seq, 1);
    assert.equal(event.event_id, "evt:diag-a:turn-authoritative:1:1");
    assert.equal(calls.filter((item) => /\/terminal$/.test(item.url)).length, 1);
    assert.equal(turnContext.terminalDelivered, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("terminal waits for ordered event drain and retries transient failure once", async () => {
  await resume();
  const calls = [];
  const originalFetch = globalThis.fetch;
  let releaseFirstEvent;
  const firstEventGate = new Promise((resolve) => {
    releaseFirstEvent = resolve;
  });
  let eventCalls = 0;
  let terminalAttempts = 0;
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    calls.push({ url: String(url), options });
    if (/\/events$/.test(String(url))) {
      eventCalls += 1;
      if (eventCalls === 1) await firstEventGate;
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    terminalAttempts += 1;
    return new Response(JSON.stringify({ ok: terminalAttempts >= 2 }), {
      status: terminalAttempts >= 2 ? 200 : 503,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await submit(turn(), false);
    sessions[0].emit({ type: "turn_start" });
    sessions[0].emit({ type: "turn_end" });
    sessions[0].pendingPrompts[0].resolve();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(calls.filter((item) => /\/terminal$/.test(item.url)).length, 0);

    releaseFirstEvent();
    const turnContext = manager.sessions.get("diag-a").turns.get("turn-authoritative");
    await turnContext.completion;
    await waitForDelivered("diag-a", "turn-authoritative");

    const eventBodies = calls
      .filter((item) => /\/events$/.test(item.url))
      .map((item) => JSON.parse(item.options.body).events[0]);
    assert.deepEqual(eventBodies.map((event) => event.event_seq), [1, 2]);
    const terminalIndexes = calls
      .map((item, index) => /\/terminal$/.test(item.url) ? index : -1)
      .filter((index) => index >= 0);
    assert.equal(terminalIndexes.length, 2);
    assert.ok(terminalIndexes[0] > calls.findLastIndex((item) => /\/events$/.test(item.url)));
    assert.equal(new Set(terminalIndexes.map((index) => calls[index].options.body)).size, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("transient event delivery retains ownership and suppresses terminal delivery", async () => {
  await resume();
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ ok: false }), {
      status: /\/events$/.test(String(url)) ? 503 : 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await submit(turn(), false);
    sessions[0].emit({ type: "turn_start" });
    sessions[0].pendingPrompts[0].resolve();
    const entry = manager.sessions.get("diag-a");
    const turnContext = entry.turns.get("turn-authoritative");
    await turnContext.completion;
    await waitFor(
      () => calls.filter((item) => /\/events$/.test(item.url)).length >= 2,
      "event callback was not retried",
    );

    const events = calls.filter((item) => /\/events$/.test(item.url));
    assert.equal(new Set(events.map((item) => item.options.body)).size, 1);
    assert.equal(calls.filter((item) => /\/terminal$/.test(item.url)).length, 0);
    assert.equal(turnContext.sealed, false);
    assert.equal(turnContext.terminalDelivered, false);
    assert.equal(entry.activeTurnId, "turn-authoritative");
    assert.match(entry.lastError, /callback delivery pending/);
    const overlapping = await submit(turn({
      turn_id: "turn-overlapping",
      client_command_id: "command-overlapping",
    }), false);
    assert.equal(overlapping.response.status, 409);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("definitive callback rejection is not retried", async () => {
  await resume();
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ ok: false }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await submit(turn(), false);
    sessions[0].emit({ type: "turn_start" });
    sessions[0].pendingPrompts[0].resolve();
    const turnContext = manager.sessions.get("diag-a").turns.get("turn-authoritative");
    await turnContext.completion;
    await waitFor(() => calls.length === 1, "blocked callback was not attempted");

    assert.equal(calls.length, 1);
    const entry = manager.sessions.get("diag-a");
    assert.equal(entry.activeTurnId, "turn-authoritative");
    assert.equal(manager.stateStore.getTurnState(
      "diag-a",
      "turn-authoritative",
    ).lifecycleStatus, "RECOVERY_BLOCKED");
    assert.match(entry.lastError, /callback delivery blocked/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("late prompt settlement after seal or cancel does not emit terminal", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    for (const action of ["seal", "cancel"]) {
      manager.sessions.clear();
      manager.acceptedCommands.clear();
      manager.stateStore.clearForTests();
      sessions.length = 0;
      await resume();
      await submit(turn(), false);
      const entry = manager.sessions.get("diag-a");
      const turnContext = entry.turns.get("turn-authoritative");
      await request(
        `/internal/runtime/v1/diagnoses/diag-a/turns/turn-authoritative/${action}`,
        { method: "POST", body: action === "cancel" ? { reason: "stop" } : {} },
      );
      sessions[0].pendingPrompts[0].resolve();
      await turnContext.completion;
      assert.equal(entry.activeTurnId, null);
      assert.equal(turnContext.terminalDelivered, false);
    }
    assert.equal(calls.filter((item) => /\/terminal$/.test(item.url)).length, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("finish is exactly once and terminal retry retains ownership", async () => {
  await resume();
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify({ ok: false }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await submit(turn(), false);
    const entry = manager.sessions.get("diag-a");
    const turnContext = entry.turns.get("turn-authoritative");
    sessions[0].pendingPrompts[0].resolve();
    await Promise.all([
      turnContext.completion,
      manager._finishTurn(entry, turnContext, "COMPLETED"),
    ]);
    await waitFor(
      () => calls.filter((item) => /\/terminal$/.test(item.url)).length >= 2,
      "terminal callback was not retried",
    );

    const terminals = calls.filter((item) => /\/terminal$/.test(item.url));
    assert.equal(new Set(terminals.map((item) => item.options.body)).size, 1);
    assert.equal(entry.activeTurnId, "turn-authoritative");
    assert.equal(turnContext.terminalDelivered, false);
    assert.match(entry.lastError, /callback delivery pending/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("prompt completion and failures release ownership after terminal acknowledgement", async () => {
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
    for (const behavior of ["resolve", "throw", "reject"]) {
      manager.sessions.clear();
      manager.acceptedCommands.clear();
      manager.stateStore.clearForTests();
      sessions.length = 0;
      const diagnosisId = `diag-${behavior}`;
      const session = new FakeSession();
      session.promptBehavior = behavior;
      manager.sessionFactory = () => {
        sessions.push(session);
        return session;
      };
      await resume(context(diagnosisId));
      const value = turn({
        diagnosis_id: diagnosisId,
        turn_id: `turn-${behavior}`,
        client_command_id: `command-${behavior}`,
      });
      const submitted = await submit(value, false);
      assert.equal(submitted.response.status, 200);
      const entry = manager.sessions.get(diagnosisId);
      assert.ok(entry, submitted.payload.error);
      await entry.turns.get(value.turn_id).completion;
      await waitForDelivered(diagnosisId, value.turn_id);
      assert.equal(entry.activeTurnId, null);
      assert.equal(sessions[0].subscribers.size, 0);
      assert.equal(sessions[0].prompts.length, 1);
      assert.deepEqual(sessions[0].prompts[0].options, {
        expandPromptTemplates: false,
        source: "rpc",
      });
      const terminal = calls.at(-1);
      assert.match(terminal.url, /\/terminal$/);
      const terminalBody = JSON.parse(terminal.options.body);
      assert.equal(terminalBody.runtime_session_id, `${"pi:"}${value.diagnosis_id}:1`);
      assert.equal(terminalBody.runtime_generation, 1);
      assert.equal(
        terminalBody.terminal_status,
        behavior === "resolve" ? "COMPLETED" : "FAILED",
      );
      if (behavior === "resolve") {
        assert.equal(entry.lastError, "");
      } else {
        assert.match(entry.lastError, /prompt failed/);
      }
    }
  } finally {
    globalThis.fetch = originalFetch;
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
    await manager.sessions.get("diag-a").turns.get("turn-authoritative").eventTail;
    manager.sealTurn("diag-a", "turn-authoritative");
    await submit(turn({
      turn_id: "turn-next",
      client_command_id: "command-next",
    }), false);
    sessions[0].emit({ type: "turn_end" });
    const nextTurn = manager.sessions.get("diag-a").turns.get("turn-next");
    await nextTurn.eventTail;
    await waitFor(
      () => calls.filter((item) => /\/events$/.test(item.url)).length === 2,
      "both turn events were not delivered",
    );

    assert.equal(calls.filter((item) => /\/events$/.test(item.url)).length, 2);
    assert.equal(calls.filter((item) => /\/terminal$/.test(item.url)).length, 0);
    assert.match(calls.find((item) => /turns\/turn-authoritative\/events$/.test(item.url)).url, /turns\/turn-authoritative\/events$/);
    assert.match(calls.find((item) => /turns\/turn-next\/events$/.test(item.url)).url, /turns\/turn-next\/events$/);
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
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (String(url).startsWith(base)) return originalFetch(url, options);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
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
    await manager.sessions.get("diag-a").turns.get("turn-authoritative").completion;
    await waitForDelivered("diag-a", "turn-authoritative");
    const ready = await request("/internal/runtime/v1/diagnoses/diag-a/state");
    assert.equal(ready.payload.data.status, "READY");
    assert.equal(ready.payload.data.active_turn_id, null);
    assert.match(ready.payload.data.last_error, /model stream failed/);
  } finally {
    globalThis.fetch = originalFetch;
  }
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
