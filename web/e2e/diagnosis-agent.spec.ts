/**
 * 使用导入模型与 API 响应验证诊断交互、只读权限和计划确认；不会调用真实模型或采集器。
 */
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync } from "node:fs";
import path from "node:path";

// 所有被拦截的数据和模型文本都是显式导入的界面夹具，不代表真实模型诊断已执行。
async function fixture(
  page: Page,
  options: {
    model?: boolean;
    readOnly?: boolean;
    running?: boolean;
    failed?: boolean;
  } = {},
) {
  let run: Record<string, unknown> | undefined;
  let created = 0,
    collected = 0;
  const errors: string[] = [];
  page.on("pageerror", () => errors.push("PAGE_ERROR"));
  const report = {
    engine: "model",
    model: "explicit-imported-ui-fixture",
    summary: "导入的界面测试报告：热点分布仍需同负载复测。",
    hypotheses: [
      {
        claim: "热点候选 <img src=x onerror=alert(1)>，仅为待验证假设",
        evidence_ids: ["E2", "E3"],
        verification: "相同负载复测并记录P99，不能把样本权重当延迟。",
      },
    ],
    limitations: ["明确导入的界面测试数据，未调用真实模型。"],
    plan: {
      kind: "PERF_CPU",
      duration_seconds: 10,
      frequency_hz: 99,
      reason: "稳定负载复测后进行结果对比。",
    },
  };
  const makeRun = (mode = "model") => ({
    id: "10",
    task_id: "7",
    attempt_id: "20",
    analysis_job_id: "30",
    analysis_run: 1,
    goal: "检查热点与采集质量",
    mode,
    status: options.failed ? "failed" : options.running ? "running" : "success",
    error_code: options.failed ? "AI_INVALID_REPORT" : undefined,
    created_at: "2026-10-20T01:00:00Z",
    trace: [
      {
        evidence_id: "E1",
        tool: "read_profile_summary",
        observation: { total_weight: 100 },
      },
      {
        evidence_id: "E2",
        tool: "inspect_hotspots",
        observation: { rank: 1, self_percent: 80, symbol_hint: "lock_or_wait" },
      },
      {
        evidence_id: "E3",
        tool: "check_collection_quality",
        observation: { duration_seconds: 5, issues: ["采集窗口较短"] },
      },
    ],
    report:
      options.failed || options.running
        ? undefined
        : {
            ...report,
            engine: mode,
            model: mode === "model" ? report.model : undefined,
          },
  });
  if (options.readOnly || options.running || options.failed) run = makeRun();
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const endpoint = new URL(req.url()).pathname.replace("/api/v1", "");
    let data: unknown = {};
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "fixture-user",
          username: "fixture",
          display_name: "Imported diagnostic UI",
          role: options.readOnly ? "viewer" : "operator",
        },
        csrf_token: "fixture-csrf",
      };
    else if (endpoint === "/auth/providers")
      data = {
        local: true,
        oidc: { enabled: false },
        ai: { enabled: options.model !== false, name: "测试配置" },
      };
    else if (endpoint === "/tasks/7")
      data = {
        id: "7",
        name: "Imported diagnosis workflow fixture",
        kind: "PERF_CPU",
        target_agent_id: "fixture-agent",
        creator_id: "fixture-user",
        params: { pid: 7, duration_seconds: 5, frequency_hz: 99 },
        collection_status: "COLLECTED",
        analysis_status: "SUCCESS",
        attempts: [{ id: "20", attempt: 1, status: "COLLECTED" }],
        analysis_identity: {
          analysis_job_id: "30",
          analysis_run: 1,
          input_artifact_id: "40",
          input_digest: "a".repeat(64),
        },
        permissions: {
          read: true,
          operate: !options.readOnly,
          share: false,
          delete: false,
          reanalyze: false,
        },
        created_at: "2026-10-20T01:00:00Z",
        updated_at: "2026-10-20T01:00:00Z",
        artifacts: [],
        events: [],
      };
    else if (endpoint === "/tasks/7/result")
      data = {
        schema_version: 1,
        kind: "PERF_CPU",
        total_samples: 100,
        unique_stacks: 1,
        node_count: 2,
        max_depth: 2,
        flamegraph: {
          name: "all",
          value: 100,
          self: 0,
          children: [
            { name: "pthread_mutex_lock", value: 100, self: 100, children: [] },
          ],
        },
        top_functions: [
          {
            name: "pthread_mutex_lock",
            self: 100,
            total: 100,
            self_percent: 100,
            total_percent: 100,
          },
        ],
        suggestions: [],
        summary: { unit: "samples", duration_seconds: 5, frequency_hz: 99 },
      };
    else if (endpoint === "/tasks/7/diagnoses" && req.method() === "POST") {
      expect(req.headers()["x-csrf-token"]).toBe("fixture-csrf");
      expect(req.headers()["idempotency-key"]).toMatch(/^[a-f0-9-]{36}$/);
      const body = req.postDataJSON();
      if (body.mode === "model") expect(body.consent).toBe(true);
      created++;
      run = makeRun(body.mode);
      data = run;
    } else if (endpoint === "/tasks/7/diagnoses")
      data = { items: run ? [run] : [], total: run ? 1 : 0 };
    else if (endpoint === "/tasks/7/diagnoses/10/collect") {
      expect(req.postDataJSON()).toEqual({ confirm: true });
      expect(req.headers()["idempotency-key"]).toMatch(/^[a-f0-9-]{36}$/);
      collected++;
      data = { id: "8" };
    } else if (endpoint === "/tasks/7/diagnoses/10/cancel") {
      run = { ...run!, status: "failed", error_code: "DIAGNOSIS_CANCELED" };
      data = { canceled: true };
    } else if (endpoint === "/task-kinds")
      data = [{ id: "PERF_CPU", display_name: "性能采样" }];
    else data = { items: [], total: 0 };
    await route.fulfill({
      json: { data, request_id: "explicit-imported-diagnosis-ui" },
    });
  });
  await page.goto("/tasks/7");
  await page.getByRole("tab", { name: "智能诊断", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "智能性能诊断", exact: true }),
  ).toBeVisible();
  return { counts: () => ({ created, collected }), errors };
}

test("diagnosis requires consent, shows evidence and confirms collection separately", async ({
  page,
}) => {
  const f = await fixture(page);
  await expect(page.getByRole("button", { name: "开始诊断" })).toBeDisabled();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(
    page.getByRole("heading", { name: "诊断结论与验证" }),
  ).toBeVisible();
  expect(f.counts()).toEqual({ created: 1, collected: 0 });
  await page.getByText("检查热点路径", { exact: false }).click();
  await expect(page.getByText("平台内原符号（未发送模型）")).toBeVisible();
  await expect(
    page.getByText("pthread_mutex_lock", { exact: true }),
  ).toBeVisible();
  expect(await page.locator(".diagnosis-report img").count()).toBe(0);
  await page.getByRole("link", { name: "E2", exact: true }).click();
  await page.getByRole("button", { name: "审阅并发起复测" }).click();
  expect(f.counts().collected).toBe(0);
  await page.getByRole("button", { name: "确认创建复测任务" }).click();
  await expect(
    page.getByRole("link", { name: "#8", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "对比结果" })).toHaveAttribute(
    "href",
    "/compare?baseline=7&candidate=8",
  );
  expect(f.counts().collected).toBe(1);
  expect(f.errors).toEqual([]);
  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations).toEqual([]);
  const evidence = path.resolve(
    import.meta.dirname,
    "../../.local/agent-extensions-20261021/ui/diagnosis-regression",
  );
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({
    path: path.join(evidence, "imported-ui-desktop.png"),
    fullPage: true,
  });
});
test("model unconfigured still allows explicit local evidence mode", async ({
  page,
}) => {
  const f = await fixture(page, { model: false });
  await page.getByLabel("诊断方式", { exact: true }).selectOption("evidence");
  await page.getByRole("button", { name: "开始诊断" }).click();
  await expect(page.getByText("本地证据检查 / 无模型调用")).toBeVisible();
  expect(f.counts()).toEqual({ created: 1, collected: 0 });
});
test("read share can review history but cannot diagnose or collect", async ({
  page,
}) => {
  const f = await fixture(page, { readOnly: true });
  await expect(page.getByRole("button", { name: "开始诊断" })).toBeDisabled();
  await expect(
    page.getByRole("heading", { name: "诊断结论与验证" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "审阅并发起复测" }),
  ).toHaveCount(0);
  expect(f.counts()).toEqual({ created: 0, collected: 0 });
});
test("active diagnosis can be canceled without changing collection", async ({
  page,
}) => {
  await fixture(page, { running: true });
  await page.getByRole("button", { name: "取消诊断", exact: true }).click();
  await expect(
    page.getByText("诊断已取消，原采集与分析结果不受影响。"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "诊断结论与验证" }),
  ).toHaveCount(0);
});
test("mobile failure state is accessible and has no horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await fixture(page, { failed: true });
  await expect(
    page.getByText("模型报告未通过结构或证据引用校验，未采纳该结论。"),
  ).toBeVisible();
  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const evidence = path.resolve(
    import.meta.dirname,
    "../../.local/agent-extensions-20261021/ui/diagnosis-regression",
  );
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({
    path: path.join(evidence, "imported-ui-mobile.png"),
    fullPage: true,
  });
});
