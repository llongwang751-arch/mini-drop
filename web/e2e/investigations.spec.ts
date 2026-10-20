/**
 * 调查计划、复测与源码修复的 UI 状态机夹具；特别覆盖跨列表缓存读取关联详情的身份边界。
 */
import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// 显式导入的界面夹具；真实 CRM/DeepSeek 执行的证据独立保存。
async function fixture(page: Page, admin = true, project = "crm-python") {
  const saber = project === "saber-python",
    pid = saber ? 42 : 41;
  let run: Record<string, unknown> | undefined;
  let confirmations = 0;
  const errors: string[] = [];
  page.on("pageerror", () => errors.push("PAGE_ERROR"));
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      endpoint = new URL(req.url()).pathname.replace("/api/v1", "");
    let data: unknown = { items: [], total: 0 };
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "fixture",
          username: "fixture",
          display_name: "Imported UI",
          role: admin ? "platform_admin" : "viewer",
        },
        csrf_token: "fixture",
      };
    if (endpoint === "/auth/providers")
      data = { local: true, ai: { enabled: true, name: "fixture" } };
    if (endpoint === "/agents")
      data = {
        items: [
          {
            id: "node",
            hostname: "fixture-node",
            status: "online",
            resources: {
              processes: [
                { pid, name: "python", uid: 10001, start_time_ticks: 123 },
              ],
            },
          },
        ],
      };
    if (endpoint === "/code-projects")
      data = {
        projects: [
          {
            id: project,
            label: saber ? "Python Saber · 知识库 FTS5 查询" : "Python CRM",
            source_revision: "a".repeat(64),
            editable_files: ["palette.py"],
            runtime: {
              agent_id: "node",
              pid,
              uid: 10001,
              start_time_ticks: 123,
              source_revision: "a".repeat(64),
            },
          },
        ],
      };
    if (endpoint === "/investigations" && req.method() === "POST") {
      const input = req.postDataJSON();
      expect(input.code_project_id).toBe(project);
      expect(input.code_consent).toBe(true);
      expect(input.parameters.pid).toBe(pid);
      run = {
        id: "101",
        goal: input.goal,
        agent_id: "node",
        status: "awaiting_confirmation",
        created_at: "2026-10-20T00:00:00Z",
        trace: [
          {
            evidence_id: "P1",
            tool: "list_collectors",
            observation: { fixture: true },
          },
        ],
        plan: {
          kind: "PYTHON_CPU",
          duration_seconds: 10,
          frequency_hz: 99,
          rationale: "显式fixture采集计划",
        },
      };
      data = run;
    } else if (endpoint === "/investigations")
      data = { items: run ? [run] : [], total: run ? 1 : 0 };
    else if (endpoint === "/investigations/101") data = run;
    if (endpoint === "/investigations/101/confirm") {
      confirmations++;
      run = {
        ...run,
        status: "verifying",
        baseline_task_id: "7",
        code_repair_id: "11",
      };
      data = { confirmed: true };
    }
    if (endpoint === "/code-repairs") data = { items: [], total: 0 };
    await route.fulfill({ json: { request_id: "fixture", data } });
  });
  await page.goto("/investigations");
  await expect(
    page.getByRole("heading", { name: "智能性能调查" }),
  ).toBeVisible();
  return { errors, confirmations: () => confirmations };
}

test("natural-language planning binds CRM and requires confirmation before execution", async ({
  page,
}) => {
  const f = await fixture(page);
  await page.getByRole("button", { name: "选择已接入的 Python CRM" }).click();
  await page
    .getByLabel("复测后继续定位所选登记项目的源码", { exact: false })
    .check();
  await page.getByLabel("同意发送问题与统计", { exact: false }).check();
  await page.getByRole("button", { name: "生成采集计划" }).click();
  await expect(page.getByText("显式fixture采集计划")).toBeVisible();
  expect(f.confirmations()).toBe(0);
  await page.getByRole("button", { name: /确认计划/ }).click();
  await expect(
    page.getByRole("button", { name: "查看补丁及实际验证" }),
  ).toBeVisible();
  expect(f.confirmations()).toBe(1);
  expect(f.errors).toEqual([]);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("viewer and mobile layout preserve source access boundary", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 900 });
  const f = await fixture(page, false);
  await expect(
    page.getByRole("button", { name: "源码定位与修复" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "生成采集计划" })).toHaveCount(
    0,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(f.errors).toEqual([]);
});

test("completed mobile comparison is keyboard accessible and source report stays escaped", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 900 });
  const f = await fixture(page);
  const comparison = {
    comparable: true,
    kind: "PYTHON_CPU",
    unit: "samples",
    baseline_weight: 500,
    candidate_weight: 1400,
    outcome: "insufficient_evidence",
    root_cause_status: "not_tested",
    limitations: ["显式界面fixture"],
    differences: [
      {
        symbol: "sha256:" + "a".repeat(32),
        baseline_self_percent: 45,
        candidate_self_percent: 42,
        delta_percentage_points: -3,
      },
    ],
  };
  await page.route("**/api/v1/investigations", (route) =>
    route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "101",
              agent_id: "node",
              goal: "显式完成状态fixture",
              status: "success",
              created_at: "2026-10-20T00:00:00Z",
              trace: [],
              baseline_task_id: "7",
              code_repair_id: "11",
              report: { comparison },
            },
          ],
          total: 1,
        },
      },
    }),
  );
  await page.route("**/api/v1/code-repairs", (route) =>
    route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "11",
              project_id: "crm-python",
              source_revision: "a".repeat(64),
              goal: "显式源码界面fixture",
              status: "success",
              created_at: "2026-10-20T00:00:00Z",
              trace: [],
              report: {
                summary: "显式fixture，未执行真实模型和补丁",
                locations: [
                  { path: "palette.py", line: 31, reason: "fixture" },
                ],
                evidence_ids: [],
                validation: {
                  accepted: true,
                  functional_regression: "passed",
                  output_parity_cases: 384,
                  median_cpu_speedup: 1.5,
                  improved_pairs: 6,
                  patch: [
                    {
                      path: "palette.py",
                      sha256: "a".repeat(64),
                      old: "fixture",
                      new: "<img src=x onerror=alert(1)>",
                    },
                  ],
                  pairs: [],
                },
              },
            },
          ],
          total: 1,
        },
      },
    }),
  );
  await page.reload();
  const region = page.getByRole("region", {
    name: "热点百分比对比表，可横向滚动",
  });
  await expect(region).toBeVisible();
  await region.focus();
  await expect(region).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page
    .getByRole("button", { name: "源码定位与修复", exact: true })
    .click();
  await expect(page.getByText("palette.py:31")).toBeVisible();
  await page.getByText("查看实际应用的补丁 · palette.py").click();
  await expect(page.locator(".investigation-page img")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(f.errors).toEqual([]);
});

test("a second enrolled project binds its own identity without overwriting the user's goal", async ({
  page,
}) => {
  const f = await fixture(page, true, "saber-python");
  const goal =
    "分析Saber知识库查询CPU热点，保持FTS查询与用户隔离，完成自动复测和源码验证。";
  await page.getByLabel("需要调查的问题", { exact: true }).fill(goal);
  await page
    .getByRole("button", {
      name: "选择已接入的 Python Saber · 知识库 FTS5 查询",
      exact: true,
    })
    .click();
  await expect(page.getByLabel("需要调查的问题", { exact: true })).toHaveValue(
    goal,
  );
  await page
    .getByLabel("复测后继续定位所选登记项目的源码", { exact: false })
    .check();
  await page.getByLabel("同意发送问题与统计", { exact: false }).check();
  await page.getByRole("button", { name: "生成采集计划", exact: true }).click();
  await expect(page.getByText("显式fixture采集计划")).toBeVisible();
  expect(f.confirmations()).toBe(0);
  await page.getByRole("button", { name: /确认计划/ }).click();
  await expect(
    page.getByRole("button", { name: "查看补丁及实际验证" }),
  ).toBeVisible();
  expect(f.confirmations()).toBe(1);
  expect(f.errors).toEqual([]);
});

// 导入夹具复现列表缓存竞争：列表仍保留 CRM，而关联的 Saber 记录
// 只能通过按自身 ID 读取的详情接口获得，页面不能拿列表缓存替代身份校验。
async function staleLinkedSource(page: Page) {
  const f = await fixture(page, true, "saber-python");
  const report = (summary: string, project: string, path: string) => ({
    summary,
    locations: [{ path, line: 35, reason: "显式源码界面fixture" }],
    evidence_ids: [],
    validation: {
      accepted: true,
      functional_regression: "passed",
      output_parity_cases: 384,
      median_cpu_speedup: 1.5,
      improved_pairs: 6,
      root_cause_status: `validated_in_controlled_${project}_benchmark`,
      scope: `${project} explicit imported benchmark fixture`,
      patch: [],
      pairs: [],
    },
  });
  const old = {
    id: "5",
    project_id: "crm-python",
    source_revision: "b".repeat(64),
    goal: "旧CRM记录fixture",
    status: "success",
    created_at: "2026-10-20T00:00:00Z",
    trace: [],
    report: report("旧CRM结论不得代替所选Saber记录", "crm", "palette.py"),
  };
  const latest = {
    id: "6",
    project_id: "saber-python",
    source_revision: "a".repeat(64),
    goal: "Saber新记录fixture",
    status: "success",
    created_at: "2026-10-20T01:00:00Z",
    trace: [],
    report: report(
      "所选Saber详情API的显式fixture结论",
      "saber",
      "internal/rag/fts5_index.py",
    ),
  };
  await page.route("**/api/v1/investigations", (route) =>
    route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "8",
              agent_id: "node",
              goal: "已完成的Saber调查fixture",
              status: "success",
              created_at: "2026-10-20T01:00:00Z",
              trace: [],
              code_repair_id: "6",
              code_project_id: "saber-python",
            },
          ],
          total: 1,
        },
      },
    }),
  );
  await page.route("**/api/v1/code-repairs", (route) =>
    route.fulfill({ json: { data: { items: [old], total: 1 } } }),
  );
  return { ...f, latest };
}

test("linked source loads the exact Saber record while the list remains cached with CRM", async ({
  page,
}) => {
  const f = await staleLinkedSource(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let detailRequests = 0;
  await page.route("**/api/v1/code-repairs/6", async (route) => {
    detailRequests++;
    await gate;
    await route.fulfill({ json: { data: f.latest } });
  });
  try {
    await page.reload();
    await page.getByRole("button", { name: "查看补丁及实际验证" }).click();
    await expect(
      page.getByText("正在加载源码记录 #6…", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "选择记录", exact: true }),
    ).toHaveValue("6");
    await expect(page.getByText("旧CRM结论不得代替所选Saber记录")).toHaveCount(
      0,
    );
    release();
    await expect(
      page.getByText(f.latest.report.summary, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("internal/rag/fts5_index.py:35", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/源码记录 #6 · 项目/)).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "已接入的项目", exact: true }),
    ).toHaveValue("saber-python");
    await expect(page.getByText("旧CRM结论不得代替所选Saber记录")).toHaveCount(
      0,
    );
    expect(detailRequests).toBeGreaterThan(0);
    expect(f.errors).toEqual([]);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  } finally {
    release();
  }
});

test("a missing selected source record shows its ID and never falls back to old CRM", async ({
  page,
}) => {
  const f = await staleLinkedSource(page);
  await page.route("**/api/v1/code-repairs/6", (route) =>
    route.fulfill({
      status: 404,
      json: {
        data: null,
        error: { code: "NOT_FOUND", message: "显式详情不存在fixture" },
      },
    }),
  );
  await page.reload();
  await page.getByRole("button", { name: "查看补丁及实际验证" }).click();
  await expect(
    page.getByText("无法读取所选源码记录 #6。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "选择记录", exact: true }),
  ).toHaveValue("6");
  await expect(page.getByText("旧CRM结论不得代替所选Saber记录")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "定位与修复结论", exact: true }),
  ).toHaveCount(0);
  expect(f.errors).toEqual([]);
});

test("new investigation confirmation stays on its exact ID when the list still has an older pending plan", async ({
  page,
}) => {
  const f = await fixture(page, true, "saber-python");
  const goal = "新Saber目标42的显式调查fixture";
  const old = {
    id: "7",
    goal: "旧调查7不能被误确认",
    agent_id: "old-node",
    parameters: { pid: 7 },
    status: "awaiting_confirmation",
    created_at: "2026-10-20T00:00:00Z",
    trace: [],
    plan: {
      kind: "PROC_CPU",
      duration_seconds: 1,
      frequency_hz: 1,
      rationale: "旧目标7的计划",
    },
  };
  let created = false;
  let confirmed = false;
  let detailRequests = 0;
  const confirmationIDs: string[] = [];
  const latest = () => ({
    id: "8",
    goal,
    agent_id: "node",
    parameters: { pid: 42 },
    status: confirmed ? "collecting" : "awaiting_confirmation",
    created_at: "2026-10-20T01:00:00Z",
    trace: [],
    plan: {
      kind: "PYTHON_CPU",
      duration_seconds: 10,
      frequency_hz: 99,
      rationale: "新目标42的计划",
    },
  });
  await page.route("**/api/v1/investigations", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      expect(body.goal).toBe(goal);
      expect(body.parameters.pid).toBe(42);
      created = true;
      await route.fulfill({
        json: { data: { ...latest(), status: "planning", plan: undefined } },
      });
    } else await route.fulfill({ json: { data: { items: [old], total: 1 } } });
  });
  await page.route("**/api/v1/investigations/8", async (route) => {
    detailRequests++;
    expect(created).toBe(true);
    await route.fulfill({ json: { data: latest() } });
  });
  await page.route("**/api/v1/investigations/*/confirm", async (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2)!;
    confirmationIDs.push(id);
    confirmed = true;
    await route.fulfill({ json: { data: { confirmed: true } } });
  });
  await page.reload();
  await expect(page.getByText(old.goal, { exact: true })).toBeVisible();
  await page.getByLabel("需要调查的问题", { exact: true }).fill(goal);
  await page
    .getByRole("button", {
      name: "选择已接入的 Python Saber · 知识库 FTS5 查询",
      exact: true,
    })
    .click();
  await page.getByLabel("同意发送问题与统计", { exact: false }).check();
  await page.getByRole("button", { name: "生成采集计划", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "选择调查", exact: true }),
  ).toHaveValue("8");
  await expect(page.locator("p").filter({ hasText: goal })).toHaveText(goal);
  await expect(
    page.getByText(/调查记录 #8 · 节点 node · PID 42/),
  ).toBeVisible();
  await expect(page.getByText(old.goal, { exact: true })).toHaveCount(0);
  await expect(page.getByText("新目标42的计划", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "确认计划并执行闭环", exact: true })
    .click();
  await expect.poll(() => confirmationIDs).toEqual(["8"]);
  expect(detailRequests).toBeGreaterThan(0);
  expect(f.errors).toEqual([]);
});
