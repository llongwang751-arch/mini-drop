import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { gunzipSync } from "node:zlib";
import { chromium, request } from "playwright";

const root = path.resolve(import.meta.dirname, "../..");
const evidence = path.join(root, "docs/evidence");
const checkpointFile = path.join(root, ".local/perf-live-final-run.json");
const stateFile = path.join(root, ".local/admin-state.json");
const baseURL = process.env.DROP_BASE_URL || "http://120.24.187.205";
const timestampNote =
  "连接、关闭与verified_at来自Windows客户端；事件created_at来自服务端数据库。未用跨机器时间差衡量延迟。";
mkdirSync(evidence, { recursive: true });
const checkpoint = existsSync(checkpointFile)
  ? JSON.parse(readFileSync(checkpointFile, "utf8"))
  : { idempotency_key: `perf-live-final-${randomUUID()}` };
const save = () =>
  writeFileSync(checkpointFile, JSON.stringify(checkpoint, null, 2));
const api = await request.newContext({ baseURL, storageState: stateFile });
let browser;

async function unpack(response) {
  const envelope = await response.json();
  if (!response.ok())
    throw new Error(
      `API ${response.status()}: ${envelope.error?.code || "HTTP_ERROR"}`,
    );
  return envelope.data;
}

async function readSse(taskId, cookieHeader, { after, limit, timeoutMs }) {
  const controller = new AbortController();
  const startedAt = new Date().toISOString();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const events = [];
  let reader;
  let status;
  let contentType;
  try {
    const response = await fetch(
      `${baseURL}/api/v1/tasks/${taskId}/events/stream`,
      {
        headers: {
          Cookie: cookieHeader,
          Accept: "text/event-stream",
          ...(after ? { "Last-Event-ID": after } : {}),
        },
        signal: controller.signal,
      },
    );
    status = response.status;
    contentType = response.headers.get("content-type");
    assert.equal(status, 200);
    assert(contentType?.includes("text/event-stream"));
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (events.length < limit) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      buffer = buffer.replaceAll("\r\n", "\n");
      let boundary;
      while (
        events.length < limit &&
        (boundary = buffer.indexOf("\n\n")) >= 0
      ) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const lines = frame.split("\n");
        const id = lines
          .find((line) => line.startsWith("id:"))
          ?.slice(3)
          .trim();
        const name = lines
          .find((line) => line.startsWith("event:"))
          ?.slice(6)
          .trim();
        const data = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!id || !data) continue;
        const event = JSON.parse(data);
        assert.equal(String(event.sequence), id);
        assert.equal(name, "task_status");
        events.push({
          id,
          event: name,
          type: event.type,
          created_at: event.created_at,
        });
      }
    }
    return {
      started_at: startedAt,
      closed_at: new Date().toISOString(),
      status,
      content_type: contentType,
      last_event_id_header: after || null,
      timeout_ms: timeoutMs,
      timed_out: timedOut,
      close_reason:
        events.length === limit
          ? "client_event_limit_reached"
          : "server_stream_ended",
      events,
    };
  } finally {
    clearTimeout(timer);
    controller.abort();
    await reader?.cancel().catch(() => {});
  }
}

try {
  const me = await unpack(await api.get("/api/v1/auth/me"));
  const agents = await unpack(await api.get("/api/v1/agents"));
  const agent = agents.items.find(
    (item) =>
      item.status === "online" &&
      item.capabilities.includes("PERF_CPU") &&
      item.resources.processes?.length,
  );
  assert(agent, "An online Agent must advertise verified PERF_CPU capability");
  const process = agent.resources.processes[0];
  assert(Number.isSafeInteger(process.pid) && process.pid > 0);
  if (!checkpoint.payload) {
    checkpoint.payload = {
      name: "验收 · 最终真实 perf CPU · 5s/99Hz",
      kind: "PERF_CPU",
      target_agent_id: agent.id,
      parameters: {
        pid: process.pid,
        duration_seconds: 5,
        frequency_hz: 99,
        scope: "process",
      },
    };
    save();
  }
  if (!checkpoint.task_id) {
    const task = await unpack(
      await api.post("/api/v1/tasks", {
        headers: {
          "X-CSRF-Token": me.csrf_token,
          "Idempotency-Key": checkpoint.idempotency_key,
        },
        data: checkpoint.payload,
      }),
    );
    checkpoint.task_id = task.id;
    save();
  }
  const taskId = checkpoint.task_id;
  console.log(
    `Actual PERF_CPU task #${taskId}: PID ${checkpoint.payload.parameters.pid}, 5s / 99Hz`,
  );
  const state = await api.storageState();
  const cookieHeader = state.cookies
    .filter((cookie) => cookie.name === "drop_session")
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");
  assert(
    cookieHeader,
    "Existing session cookie is required; no new login is attempted",
  );
  const first = await readSse(taskId, cookieHeader, {
    limit: 1,
    timeoutMs: 20000,
  });
  assert.equal(first.events.length, 1);
  const cursor = first.events[0].id;
  const resumed = await readSse(taskId, cookieHeader, {
    after: cursor,
    limit: 2,
    timeoutMs: 25000,
  });
  assert.equal(resumed.events.length, 2);
  assert(resumed.events.every((event) => BigInt(event.id) > BigInt(cursor)));
  assert(BigInt(resumed.events[1].id) > BigInt(resumed.events[0].id));
  const expectedEvents = await unpack(
    await api.get(`/api/v1/tasks/${taskId}/events?after=${cursor}`),
  );
  assert.deepEqual(
    resumed.events.map((event) => event.id),
    expectedEvents.items.slice(0, 2).map((event) => String(event.sequence)),
  );
  const sse = {
    verified_at: new Date().toISOString(),
    task_id: taskId,
    actual_network_stream: true,
    credentials_reused_without_login: true,
    first_connection: first,
    resumed_connection: resumed,
    strict_monotonic_ids: true,
    no_replayed_cursor_event: true,
    matches_persisted_events_after_cursor: true,
    bounded_timeout_and_active_client_close: true,
    timestamp_note: timestampNote,
  };
  writeFileSync(
    path.join(evidence, "sse-resume.json"),
    JSON.stringify(sse, null, 2),
  );

  let detail;
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    detail = await unpack(await api.get(`/api/v1/tasks/${taskId}`));
    if (detail.analysis_status === "SUCCESS") break;
    if (
      ["FAILED", "CANCELED"].includes(detail.collection_status) ||
      ["FAILED", "CANCELED"].includes(detail.analysis_status)
    )
      throw new Error(
        `Actual perf task failed: ${detail.error_code || detail.analysis_status}`,
      );
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  assert.equal(detail.collection_status, "COLLECTED");
  assert.equal(detail.analysis_status, "SUCCESS");
  const result = await unpack(await api.get(`/api/v1/tasks/${taskId}/result`));
  assert.equal(result.kind, "PERF_CPU");
  assert.equal(result.summary.source, "perf");
  assert.equal(result.summary.unit, "samples");
  assert.equal(result.summary.duration_seconds, 5);
  assert.equal(result.summary.frequency_hz, 99);
  assert(result.total_samples > 0);
  const artifacts = await unpack(
    await api.get(`/api/v1/tasks/${taskId}/artifacts`),
  );
  const raw = artifacts.items.find((item) => item.kind === "RAW");
  const outputs = artifacts.items.filter((item) =>
    ["RESULT", "MANIFEST"].includes(item.kind),
  );
  assert(raw);
  assert.equal(outputs.length, 4);
  assert.deepEqual(
    outputs.map((item) => item.name).sort(),
    ["flamegraph.json.gz", "manifest.json", "report.json", "top.json"].sort(),
  );
  const verified = [];
  const bodies = new Map();
  for (const artifact of [raw, ...outputs]) {
    const download = await unpack(
      await api.get(
        `/api/v1/tasks/${taskId}/artifacts/${artifact.id}/download`,
      ),
    );
    const response = await api.get(download.url);
    assert.equal(response.status(), 200);
    const body = await response.body();
    const digest = createHash("sha256").update(body).digest("hex");
    assert.equal(body.length, Number(artifact.size));
    assert.equal(digest, artifact.sha256);
    bodies.set(artifact.name, body);
    verified.push({
      id: artifact.id,
      name: artifact.name,
      kind: artifact.kind,
      bytes: body.length,
      sha256: digest,
      content_type: artifact.content_type,
      sha256_verified: true,
      size_verified: true,
    });
  }
  const folded = bodies.get(raw.name).toString("utf8").trim().split("\n");
  const rawWeight = folded.reduce((sum, line) => {
    const match = line.match(/ ([0-9]+)$/);
    assert(match, "Actual perf raw artifact must contain valid folded weights");
    return sum + BigInt(match[1]);
  }, 0n);
  assert.equal(rawWeight, BigInt(result.total_samples));
  const flamegraph = JSON.parse(gunzipSync(bodies.get("flamegraph.json.gz")));
  assert.equal(flamegraph.value, result.total_samples);
  const report = JSON.parse(bodies.get("report.json"));
  assert.equal(report.summary.source, "perf");
  const manifest = JSON.parse(bodies.get("manifest.json"));
  assert.equal(manifest.input_sha256, raw.sha256);
  assert.equal(manifest.kind, "PERF_CPU");
  assert.equal(manifest.artifacts.length, 3);
  const requiredEvents = [
    "TASK_CREATED",
    "DISPATCH_QUEUED",
    "TASK_DELIVERED",
    "RUNNER_STARTED",
    "RAW_ARTIFACT_UPLOADED",
    "COLLECTION_SUCCEEDED",
    "ANALYSIS_STARTED",
    "RESULT_ARTIFACT_UPLOADED",
    "ANALYSIS_SUCCEEDED",
  ];
  for (const event of requiredEvents)
    assert(
      detail.events.some((item) => item.type === event),
      `Missing actual event ${event}`,
    );
  assert.equal(
    detail.events.filter((item) => item.type === "RESULT_ARTIFACT_UPLOADED")
      .length,
    4,
  );

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    baseURL,
    storageState: stateFile,
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`/tasks/${taskId}`, { waitUntil: "domcontentloaded" });
  await page.getByRole("group", { name: /可交互火焰图/ }).waitFor();
  const visual = await page.evaluate(async () => {
    await document.fonts.ready;
    const finite = document
      .getAnimations()
      .filter((animation) =>
        Number.isFinite(animation.effect?.getComputedTiming().iterations),
      );
    await Promise.allSettled(finite.map((animation) => animation.finished));
    if (document.activeElement instanceof HTMLElement)
      document.activeElement.blur();
    window.scrollTo(0, 0);
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)),
    );
    return {
      fonts_status: document.fonts.status,
      finite_animations_waited: finite.length,
      active_finite_animations: document
        .getAnimations()
        .filter(
          (animation) =>
            Number.isFinite(animation.effect?.getComputedTiming().iterations) &&
            ["running", "pending"].includes(animation.playState),
        ).length,
      visible_flame_rects: document.querySelectorAll(".flame-svg rect").length,
    };
  });
  assert.equal(visual.fonts_status, "loaded");
  assert.equal(visual.active_finite_animations, 0);
  assert(visual.visible_flame_rects > 0);
  await page.screenshot({
    path: path.join(evidence, "perf-live-final.png"),
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  await context.close();
  const evidenceJson = {
    verified_at: new Date().toISOString(),
    task_id: taskId,
    name: detail.name,
    task_url: `${baseURL}/tasks/${taskId}`,
    kind: detail.kind,
    target: {
      agent_id: detail.target_agent_id,
      sampled_pid: checkpoint.payload.parameters.pid,
      observed_agent_capabilities: agent.capabilities,
      observed_process_name: process.name,
    },
    parameters: checkpoint.payload.parameters,
    collection_status: detail.collection_status,
    analysis_status: detail.analysis_status,
    result_summary: result.summary,
    total_samples: result.total_samples,
    unique_stacks: result.unique_stacks,
    node_count: result.node_count,
    max_depth: result.max_depth,
    raw_folded_lines: folded.length,
    raw_weight_matches_result: true,
    artifacts: verified,
    analyzer_version: manifest.analyzer_version,
    events: detail.events.map((event) => ({
      sequence: String(event.sequence),
      type: event.type,
      source: event.source,
      created_at: event.created_at,
    })),
    required_events_verified: true,
    sse_resume_evidence: "sse-resume.json",
    screenshot: {
      ...visual,
      viewport: "1440x1000",
      path: "perf-live-final.png",
      javascript_errors: errors,
    },
    scope:
      "Actual perf sampling against the Agent's currently advertised controlled process. Not imported or mocked. No service restart or deployment performed.",
    timestamp_note: timestampNote,
  };
  writeFileSync(
    path.join(evidence, "perf-live-final.json"),
    JSON.stringify(evidenceJson, null, 2),
  );
  console.log(
    JSON.stringify({
      task_id: taskId,
      source: result.summary.source,
      samples: result.total_samples,
      raw_sha256_verified: true,
      outputs_verified: outputs.length,
      events: detail.events.length,
      sse_resume_verified: true,
      screenshot_fonts_loaded: true,
      screenshot_finite_animations_finished: true,
      javascript_errors: errors.length,
    }),
  );
} finally {
  await browser?.close();
  await api.dispose();
}
