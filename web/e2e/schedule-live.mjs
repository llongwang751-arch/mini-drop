import { request } from "playwright";
import { readFile, writeFile } from "node:fs/promises";
const api = await request.newContext({
  baseURL: "http://120.24.187.205",
  storageState: JSON.parse(
    await readFile("../.local/admin-state.json", "utf8"),
  ),
});
const auth = (await (await api.get("/api/v1/auth/me")).json()).data;
const agent = (await (await api.get("/api/v1/agents")).json()).data.items[0];
const headers = { "X-CSRF-Token": auth.csrf_token };
let id;
try {
  const res = await api.post("/api/v1/schedules", {
    headers,
    data: {
      name: "验收 · 每分钟受控 CPU 诊断",
      interval_minutes: 1,
      timezone: "Asia/Shanghai",
      template: {
        name: "计划触发 · 真实线程 CPU",
        kind: "PROC_CPU",
        target_agent_id: agent.id,
        parameters: {
          pid: agent.resources.processes[0].pid,
          duration_seconds: 2,
          frequency_hz: 10,
          scope: "process",
        },
      },
    },
  });
  if (res.status() !== 201)
    throw new Error(`schedule create ${res.status()}: ${await res.text()}`);
  const schedule = (await res.json()).data;
  id = schedule.id;
  const started = Date.now();
  let records = [];
  while (Date.now() - started < 90000) {
    records = (await (await api.get(`/api/v1/schedules/${id}/records`)).json())
      .data.items;
    if (records.length) break;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  if (records.length !== 1 || !records[0].task_id)
    throw new Error("Expected one persisted scheduled execution");
  const record = records[0];
  let task;
  while (Date.now() - started < 100000) {
    task = (await (await api.get(`/api/v1/tasks/${record.task_id}`)).json())
      .data;
    if (task.analysis_status === "SUCCESS") break;
    if (task.collection_status === "FAILED")
      throw new Error(`scheduled task failed: ${task.error_code}`);
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  if (task.analysis_status !== "SUCCESS")
    throw new Error("Scheduled task did not finish");
  const result = {
    schedule_id: id,
    task_id: record.task_id,
    scheduled_at: record.scheduled_at,
    status: record.status,
    collection_status: task.collection_status,
    analysis_status: task.analysis_status,
    elapsed_ms: Date.now() - started,
    records: records.length,
  };
  await writeFile(
    "../docs/evidence/schedule-live.json",
    JSON.stringify(result, null, 2),
  );
  console.log(JSON.stringify(result));
} finally {
  if (id) {
    const pause = await api.patch(`/api/v1/schedules/${id}`, {
      headers,
      data: { enabled: false },
    });
    console.log(`Schedule ${id} paused: ${pause.status()}`);
  }
  await api.dispose();
}
