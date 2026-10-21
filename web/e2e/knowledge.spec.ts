/**
 * 在真实 App 外壳中验证知识/案例操作；所有会话、工具结果与写入响应均为导入的合成夹具。
 * 请求白名单阻止生产访问；通过只能证明 UI 边界，不能代替模型、数据库或真实修复验收。
 */
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const skill = {
  id: "cpu_hotspot",
  version: "1.0.0",
  title: "CPU 热点排查",
  task_kinds: ["PYTHON_CPU"],
  required_tools: ["inspect_hotspots"],
  steps: ["核对采样条件", "检查本轮调用链"],
  counter_evidence: ["热点比例不能直接当作请求延迟"],
  stop_conditions: ["样本不足时停止因果推断"],
  verification: ["对齐条件后复测"],
  match_reasons: ["任务类型匹配"],
};
const saved = {
  id: "71",
  task_id: "101",
  diagnosis_id: "301",
  attempt_id: "501",
  analysis_job_id: "601",
  analysis_run: 1,
  task_kind: "PYTHON_CPU",
  outcome: "reviewed_correction",
  correction_code: "insufficient_evidence",
  status: "active",
  created_at: "2026-10-21T01:00:00Z",
  expires_at: "2026-11-21T01:00:00Z",
};
const knowledge = {
  skills: [skill],
  cases: [
    {
      id: "70",
      source_diagnosis_id: "300",
      outcome: "reviewed_correction",
      assurance: "human_reviewed",
      conclusion: "先核对采样条件，不直接下因果结论",
      skill_refs: [{ id: "cpu_hotspot", version: "1.0.0" }],
      match_reasons: ["任务类型匹配"],
      prior_only: true,
    },
  ],
  prior_only: true,
  history_enabled: true,
};
const diagnosis = {
  id: "301",
  attempt_id: "501",
  analysis_job_id: "601",
  analysis_run: 1,
  goal: "导入的合成诊断问题",
  mode: "model",
  status: "success",
  created_at: "2026-10-21T01:00:00Z",
  trace: [
    {
      evidence_id: "E1",
      tool: "read_profile_summary",
      observation: { samples: 1 },
    },
  ],
  report: {
    engine: "model",
    model: "imported-fixture",
    summary: "导入的合成诊断结论",
    hypotheses: [
      {
        claim: "待验证的合成假设",
        evidence_ids: ["E1"],
        verification: "重新采集核对",
      },
    ],
    limitations: ["导入夹具，不是模型执行证据"],
    knowledge,
  },
};
type Options = {
  role?: "platform_admin" | "viewer";
  empty?: boolean;
  failure?: boolean;
  delay?: number;
  saveFailureOnce?: boolean;
  stale?: boolean;
  repairAccepted?: boolean;
};

async function importedFixture(page: Page, options: Options = {}) {
  const writes: {
    path: string;
    body: Record<string, unknown>;
    key?: string;
  }[] = [];
  const unexpected: string[] = [],
    errors: string[] = [];
  let revoked = false,
    saveAttempts = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (url.hostname !== "127.0.0.1") {
      unexpected.push(url.origin);
      await route.abort();
      return;
    }
    const respond = (data: unknown) =>
      route.fulfill({
        json: { data, request_id: "imported-knowledge-fixture" },
      });
    if (
      request.method() === "POST" &&
      [
        "/diagnostic-cases/71/revoke",
        "/tasks/101/diagnoses/301/cases",
        "/tasks/101/diagnoses",
      ].includes(path)
    ) {
      writes.push({
        path,
        body: request.postDataJSON(),
        key: request.headers()["idempotency-key"],
      });
      if (path.endsWith("/revoke")) {
        revoked = true;
        await respond({
          ...saved,
          status: "revoked",
          revoked_at: "2026-10-21T02:00:00Z",
        });
        return;
      }
      if (path.endsWith("/cases")) {
        if (options.saveFailureOnce && ++saveAttempts === 1) {
          await route.fulfill({
            status: 503,
            json: {
              error: {
                code: "IMPORTED_TEMPORARY_FAILURE",
                message: "导入的暂时失败",
              },
            },
          });
          return;
        }
        await respond({ ...saved, ...request.postDataJSON() });
        return;
      }
      await respond({ ...diagnosis, id: "302" });
      return;
    }
    if (request.method() !== "GET") {
      unexpected.push(`${request.method()} ${path}`);
      await route.abort();
      return;
    }
    if (path === "/auth/me") {
      await respond({
        user: {
          id: "imported-knowledge-user",
          username: "imported-ui-fixture",
          display_name: "导入知识界面验证",
          role: options.role || "platform_admin",
        },
        csrf_token: "imported-fixture-csrf",
      });
      return;
    }
    if (path === "/auth/providers") {
      await respond({
        local: true,
        oidc: { enabled: false },
        ai: { enabled: true, name: "imported-fixture" },
      });
      return;
    }
    if (path === "/task-kinds") {
      await respond([]);
      return;
    }
    if (path === "/diagnostic-skills") {
      if (options.delay)
        await new Promise((resolve) => setTimeout(resolve, options.delay));
      if (options.failure) {
        await route.fulfill({
          status: 403,
          json: {
            error: {
              code: "IMPORTED_READ_DENIED",
              message: "导入的知识读取失败",
            },
          },
        });
        return;
      }
      await respond({ items: options.empty ? [] : [skill] });
      return;
    }
    if (path === "/diagnostic-cases") {
      await respond({
        items: options.empty
          ? []
          : [
              {
                ...saved,
                status: revoked ? "revoked" : "active",
                revoked_at: revoked ? "2026-10-21T02:00:00Z" : undefined,
              },
            ],
      });
      return;
    }
    if (path === "/tasks/101") {
      await respond({
        id: "101",
        name: "导入的知识界面诊断",
        kind: "PYTHON_CPU",
        collection_status: "COLLECTED",
        analysis_status: "SUCCESS",
        params: {},
        created_at: "2026-10-21T00:00:00Z",
        updated_at: "2026-10-21T01:00:00Z",
        creator_id: "imported-knowledge-user",
        artifacts: [],
        attempts: [
          {
            id: options.stale ? "502" : "501",
            attempt: 1,
            status: "COLLECTED",
          },
        ],
        analysis_identity: {
          analysis_job_id: "601",
          analysis_run: 1,
          input_artifact_id: "701",
          input_digest: "a".repeat(64),
        },
        permissions: {
          read: true,
          operate: options.role !== "viewer",
          share: false,
          delete: false,
          reanalyze: false,
        },
      });
      return;
    }
    if (path === "/tasks/101/result") {
      await respond({
        schema_version: 1,
        kind: "PYTHON_CPU",
        total_samples: 1,
        unique_stacks: 1,
        node_count: 2,
        max_depth: 1,
        flamegraph: {
          name: "root",
          value: 1,
          self: 0,
          children: [{ name: "imported_fixture", value: 1, self: 1 }],
        },
        top_functions: [
          {
            name: "imported_fixture",
            self: 1,
            total: 1,
            self_percent: 100,
            total_percent: 100,
          },
        ],
        summary: {
          duration_seconds: 1,
          frequency_hz: 99,
          unit: "samples",
          source: "import",
          limitations: ["导入夹具"],
        },
        suggestions: [],
      });
      return;
    }
    if (path === "/tasks/101/diagnoses") {
      await respond({ items: [diagnosis], total: 1 });
      return;
    }
    if (
      ["/tasks/101/events", "/tasks/101/verifications", "/agents"].includes(
        path,
      )
    ) {
      await respond({ items: [], total: 0 });
      return;
    }
    if (path === "/tasks/101/events/stream") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: ": imported-fixture\n\n",
      });
      return;
    }
    if (path === "/code-projects") {
      await respond({
        projects: [
          {
            id: "crm-python",
            label: "导入CRM组件",
            source_revision: "a".repeat(64),
            editable_files: [],
          },
        ],
      });
      return;
    }
    if (path === "/investigations") {
      await respond({
        items: [
          {
            id: "81",
            status: "success",
            goal: "导入修复调查",
            agent_id: "imported-agent",
            trace: [],
            baseline_task_id: "101",
            diagnosis_id: "301",
            code_repair_id: "9",
            created_at: "2026-10-21T01:00:00Z",
          },
        ],
        total: 1,
      });
      return;
    }
    if (["/code-repairs", "/code-repairs/9"].includes(path)) {
      const repair = {
        id: "9",
        goal: "导入的受控修复",
        project_id: "crm-python",
        source_revision: "a".repeat(64),
        status: "success",
        trace: [],
        created_at: "2026-10-21T01:00:00Z",
        report: {
          summary: "导入的修复验收夹具",
          locations: [],
          evidence_ids: [],
          validation: {
            accepted: options.repairAccepted !== false,
            functional_regression: "passed",
            output_parity_cases: 384,
            median_cpu_speedup: 1.2,
            improved_pairs: 6,
            root_cause_status: "validated_in_controlled_benchmark",
            scope: "只限合成组件",
            patch: [],
            pairs: [],
          },
        },
      };
      await respond(
        path.endsWith("/9") ? repair : { items: [repair], total: 1 },
      );
      return;
    }
    unexpected.push(`${request.method()} ${path}`);
    await route.abort();
  });
  return { writes, unexpected, errors };
}

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
}

for (const width of [1440, 375]) {
  test(`imported knowledge catalog and cases fit ${width}px with accessible states`, async ({
    page,
  }, info) => {
    const journal = await importedFixture(page);
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/knowledge");
    await expect(
      page.getByRole("heading", { name: "CPU 热点排查" }),
    ).toBeVisible();
    await page.getByText("查看排查步骤与验收标准").click();
    await expect(page.getByRole("heading", { name: "停止条件" })).toBeVisible();
    await noOverflow(page);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({
      path: info.outputPath(`knowledge-skills-${width}.png`),
      fullPage: true,
    });
    await page.getByRole("tab", { name: "诊断 Skill" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "我的复核案例" })).toBeFocused();
    await expect(page.getByRole("heading", { name: "案例 #71" })).toBeVisible();
    await noOverflow(page);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({
      path: info.outputPath(`knowledge-cases-${width}.png`),
      fullPage: true,
    });
    expect(journal.writes).toEqual([]);
    expect(journal.unexpected).toEqual([]);
    expect(journal.errors).toEqual([]);
  });
}

test("case revocation requires confirmation and changes its visible state", async ({
  page,
}) => {
  const journal = await importedFixture(page);
  await page.goto("/knowledge?tab=cases");
  await page.getByRole("button", { name: "撤销案例", exact: true }).click();
  expect(journal.writes).toHaveLength(0);
  await page.getByRole("button", { name: "确认撤销", exact: true }).click();
  await expect(page.getByText("已撤销", { exact: true })).toBeVisible();
  expect(journal.writes).toEqual([
    { path: "/diagnostic-cases/71/revoke", body: {}, key: undefined },
  ]);
  expect(journal.unexpected).toEqual([]);
});

test("viewer sees cases without a revocation control", async ({ page }) => {
  const journal = await importedFixture(page, { role: "viewer" });
  await page.goto("/knowledge?tab=cases");
  await expect(page.getByRole("heading", { name: "案例 #71" })).toBeVisible();
  await expect(page.getByRole("button", { name: "撤销案例" })).toHaveCount(0);
  expect(journal.writes).toEqual([]);
});

test("knowledge loading, empty and error states remain actionable", async ({
  page,
}) => {
  await importedFixture(page, { empty: true, delay: 900 });
  await page.goto("/knowledge");
  await expect(
    page.getByRole("status", { name: "正在加载诊断知识" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "暂无可用诊断 Skill" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "我的复核案例" }).click();
  await expect(
    page.getByRole("heading", { name: "还没有复核案例" }),
  ).toBeVisible();
  await page.unrouteAll();
  await importedFixture(page, { failure: true });
  await page.goto("/knowledge");
  await expect(page.getByRole("alert")).toContainText("导入的知识读取失败");
  await expect(
    page.getByRole("button", { name: "重试", exact: true }),
  ).toBeVisible();
});

test("reviewed correction keeps fixed fields and reuses its idempotency key after failure", async ({
  page,
}, info) => {
  const journal = await importedFixture(page, { saveFailureOnce: true });
  await page.goto("/tasks/101");
  await page.getByRole("tab", { name: "智能诊断", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "本轮参考的诊断知识" }),
  ).toContainText("不是本次采集证据");
  await expect(page.getByLabel("知识参考", { exact: true })).toHaveValue(
    "experience",
  );
  await page
    .getByRole("button", { name: "保存复核纠正案例", exact: true })
    .click();
  const form = page.getByRole("region", { name: "保存复核纠正案例" });
  await expect(
    form.getByRole("button", { name: "确认保存案例" }),
  ).toBeDisabled();
  await form
    .getByLabel("纠正类别", { exact: true })
    .selectOption("insufficient_evidence");
  await form.getByRole("checkbox").check();
  expect(
    (await new AxeBuilder({ page }).include(".diagnosis-agent").analyze())
      .violations,
  ).toEqual([]);
  await form.getByRole("button", { name: "确认保存案例" }).click();
  await expect(form.getByRole("alert")).toContainText("导入的暂时失败");
  await form.getByRole("button", { name: "确认保存案例" }).click();
  await expect(form.getByRole("status")).toContainText("已保存");
  expect(journal.writes).toHaveLength(2);
  expect(journal.writes[0].body).toEqual({
    outcome: "reviewed_correction",
    human_confirmed: true,
    correction_code: "insufficient_evidence",
  });
  expect(journal.writes[0].key).toMatch(/^[a-f0-9-]{36}$/);
  expect(journal.writes[1].key).toBe(journal.writes[0].key);
  await page.screenshot({
    path: info.outputPath("knowledge-report-1440.png"),
    fullPage: true,
  });
  expect(journal.unexpected).toEqual([]);
  expect(journal.errors).toEqual([]);
});

test("stale diagnosis and viewer cannot save reviewed cases", async ({
  page,
}) => {
  await importedFixture(page, { stale: true });
  await page.goto("/tasks/101");
  await page.getByRole("tab", { name: "智能诊断", exact: true }).click();
  await expect(
    page.getByText("这条记录属于历史执行版本，请基于当前结果重新诊断。"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存复核纠正案例" }),
  ).toHaveCount(0);
  await page.unrouteAll();
  await importedFixture(page, { role: "viewer" });
  await page.goto("/tasks/101");
  await page.getByRole("tab", { name: "智能诊断", exact: true }).click();
  await expect(page.getByText("诊断报告已生成 · 待人工验证")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存复核纠正案例" }),
  ).toHaveCount(0);
});

test("knowledge mode is explicitly submitted without changing the diagnosis consent boundary", async ({
  page,
}) => {
  const journal = await importedFixture(page);
  await page.goto("/tasks/101");
  await page.getByRole("tab", { name: "智能诊断", exact: true }).click();
  await page.getByLabel("知识参考", { exact: true }).selectOption("off");
  await expect(
    page.getByRole("button", { name: "开始诊断", exact: true }),
  ).toBeDisabled();
  await page
    .getByText("同意发送问题文本与脱敏统计；已确认问题中不含敏感信息")
    .click();
  await page.getByRole("button", { name: "开始诊断", exact: true }).click();
  await expect.poll(() => journal.writes.length).toBe(1);
  expect(journal.writes[0].body).toMatchObject({
    knowledge_mode: "off",
    consent: true,
    mode: "model",
  });
});

test("only a passed associated repair offers the verified-case save path", async ({
  page,
}) => {
  const journal = await importedFixture(page);
  await page.goto("/investigations?repair=9");
  await page
    .getByRole("button", { name: "保存受控修复案例", exact: true })
    .click();
  const form = page.getByRole("region", { name: "保存受控修复案例" });
  await expect(
    form.getByRole("button", { name: "确认保存案例" }),
  ).toBeDisabled();
  await form.getByRole("checkbox").check();
  await form.getByRole("button", { name: "确认保存案例" }).click();
  await expect(form.getByRole("status")).toContainText("已保存");
  expect(journal.writes[0].body).toEqual({
    outcome: "verified_repair",
    human_confirmed: true,
    code_repair_id: "9",
  });
  expect(journal.unexpected).toEqual([]);
  expect(journal.errors).toEqual([]);
  await page.unrouteAll();
  await importedFixture(page, { repairAccepted: false });
  await page.goto("/investigations?repair=9");
  await expect(page.getByText("导入的修复验收夹具")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存受控修复案例" }),
  ).toHaveCount(0);
});
