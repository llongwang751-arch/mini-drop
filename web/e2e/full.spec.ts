/**
 * 需要显式开关的隔离环境全链路验收，会创建导入任务并访问真实后端。
 * 与纯前端 route 夹具分开，凭据和原始下载字节不能进入浏览器报告。
 */
import { test, expect, request } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";
import {
  safeStageAPI,
  safeRouteFetch,
  stageArtifactBytes,
} from "./safe-stage-io";

const root = path.resolve(import.meta.dirname, "../..");
const baseURL = process.env.DROP_BASE_URL || "http://127.0.0.1:18080";
const prefix = process.env.DROP_CREDENTIAL_PREFIX || "stage_admin";
const evidence = path.join(root, "docs/evidence/full");
const state = path.join(root, ".local/full-e2e-state.json");
const fixtures = path.join(root, "analyzer/tests/fixtures/collected");
const stamp = Date.now();
let api: APIRequestContext,
  csrf = "",
  taskID = "";
const ids: Record<string, string> = {};
async function post(route: string, data: unknown) {
  return api.post("/api/v1" + route, {
    data,
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": randomUUID() },
  });
}
async function complete(id: string) {
  await expect
    .poll(
      async () => {
        const t = (await (await api.get("/api/v1/tasks/" + id)).json()).data;
        return t.analysis_status === "FAILED" ||
          t.collection_status === "FAILED"
          ? "FAILED:" + t.error_code
          : t.analysis_status;
      },
      { timeout: 85000, intervals: [1000, 2000] },
    )
    .toBe("SUCCESS");
}

test.describe("full documented capabilities", () => {
  test.describe.configure({ mode: "serial" });
  test.skip(
    process.env.DROP_FULL_E2E !== "1",
    "Explicit full integration opt-in required",
  );
  test.beforeAll(async () => {
    mkdirSync(evidence, { recursive: true });
    api = safeStageAPI(await request.newContext({ baseURL }));
    const login = await api.post("/api/v1/auth/login", {
      data: {
        username: readFileSync(
          path.join(root, ".local/" + prefix + "_username"),
          "utf8",
        ).trim(),
        password: readFileSync(
          path.join(root, ".local/" + prefix + "_password"),
          "utf8",
        ).trim(),
      },
    });
    expect(login.status()).toBe(200);
    csrf = (await login.json()).data.csrf_token;
    await api.storageState({ path: state });
    expect(
      (await (await api.get("/api/v1/task-kinds")).json()).data,
    ).toHaveLength(18);
  });
  test.afterAll(async () => {
    await api?.dispose();
  });

  test("UI import preserves lost-response idempotency, provenance and feedback", async ({
    browser,
  }) => {
    const context = await browser.newContext({ storageState: state });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", () => errors.push("PAGE_ERROR"));
    await page.goto(baseURL + "/tasks/new");
    await page
      .getByRole("button", { name: "导入原始文件", exact: true })
      .click();
    await page.getByRole("button", { name: /导入折叠调用栈/ }).click();
    await page
      .getByLabel("或粘贴折叠调用栈")
      .fill("imported-worker;main;malloc 80\nimported-worker;main;work 20");
    await page
      .getByLabel("任务名称", { exact: true })
      .fill("全量验收 · 导入 " + stamp);
    let first = "",
      count = 0;
    const keys: string[] = [];
    await page.route("**/api/v1/tasks", async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      count++;
      keys.push(route.request().headers()["idempotency-key"]);
      const response = await safeRouteFetch(() => route.fetch());
      if (count === 1) {
        expect(response.status()).toBe(201);
        first = (await response.json()).data.id;
        await route.abort("failed");
      } else await route.fulfill({ response });
    });
    await page.getByRole("button", { name: "创建诊断", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await page.getByRole("button", { name: "创建诊断", exact: true }).click();
    await expect(page).toHaveURL(new RegExp("/tasks/" + first + "$"));
    expect(keys[0]).toBe(keys[1]);
    taskID = first;
    await complete(first);
    await page.reload();
    await expect(page.locator(".flame-component")).toBeVisible();
    // 真实后端验收必须走有授权校验的 gzip 制品下载链，不能只靠页面内嵌小树通过。
    await expect(
      page.locator('[data-flame-source="gzip-artifact"]'),
    ).toBeVisible();
    await expect(
      page.getByText("工作空间尚未接入模型服务。", { exact: false }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "生成模型辅助建议" }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "有帮助", exact: true })
      .first()
      .click();
    await expect(page.getByText("反馈已保存。")).toBeVisible();
    await page.screenshot({
      path: path.join(evidence, "import-result-desktop.png"),
      fullPage: true,
    });
    expect(errors).toEqual([]);
    await context.close();
  });

  test("native files render typed results and hash-matched downloads", async ({
    browser,
  }) => {
    test.setTimeout(300000);
    const cases = [
      ["GO_HEAP", "go-heap.pprof", "pprof", ""],
      ["JAVA_HEAP", "java-heap.hprof", "hprof", ""],
      ["PYTHON_MEMORY", "python-memory.bin", "memray", ""],
      ["IO_TRACE", "io.jsonl", "jsonl", ""],
      ["MEMORY_TRACE", "memory.jsonl", "jsonl", ""],
      ["RESOURCE_SNAPSHOT", "resources.json", "json", ""],
      ["BOLT_PROFILE", "bolt.fdata", "bolt_fdata", "bolt-target.elf"],
      ["SCRIPT", "script.json", "json", ""],
    ];
    const context = await browser.newContext({ storageState: state });
    const page = await context.newPage(),
      results = [];
    const headings: Record<string, string> = {
      JAVA_HEAP: "类实例与内存",
      IO_TRACE: "I/O 观测",
      MEMORY_TRACE: "内存分配与存活",
      RESOURCE_SNAPSHOT: "资源观测与采集开销",
      BOLT_PROFILE: "BOLT 二进制优化证据",
    };
    for (const [kind, file, format, binary] of cases) {
      console.log("Imported native fixture:", kind);
      const profile: Record<string, unknown> = {
        filename: file,
        content_type: "application/octet-stream",
        format,
        data_base64: readFileSync(path.join(fixtures, file)).toString("base64"),
      };
      if (binary)
        profile.auxiliary = [
          {
            filename: "target.elf",
            content_type: "application/octet-stream",
            format: "elf",
            data_base64: readFileSync(path.join(fixtures, binary)).toString(
              "base64",
            ),
          },
        ];
      const response = await post("/tasks", {
        name: "全量验收 · " + kind + " " + stamp,
        kind,
        parameters: {
          options: kind === "BOLT_PROFILE" ? { optimize: true } : {},
        },
        import: profile,
      });
      expect(response.status(), kind + ": HTTP " + response.status()).toBe(201);
      const task = (await response.json()).data;
      ids[kind] = task.id;
      await complete(task.id);
      const result = (
        await (await api.get("/api/v1/tasks/" + task.id + "/result")).json()
      ).data;
      expect(result.summary.source).toBe("import");
      await page.goto(baseURL + "/tasks/" + task.id);
      await expect(
        page.getByRole("heading", {
          name: "全量验收 · " + kind + " " + stamp,
          exact: true,
        }),
      ).toBeVisible();
      if (headings[kind])
        await expect(
          page.getByRole("heading", { name: headings[kind] }),
        ).toBeVisible();
      await page.screenshot({
        path: path.join(evidence, kind.toLowerCase() + ".png"),
        fullPage: true,
      });
      const artifacts = (
        await (await api.get("/api/v1/tasks/" + task.id + "/artifacts")).json()
      ).data.items;
      expect(artifacts.length).toBeGreaterThanOrEqual(4);
      for (const a of artifacts) {
        const link = await api.get(
          "/api/v1/tasks/" + task.id + "/artifacts/" + a.id + "/download",
        );
        expect(link.status()).toBe(200);
        const signed = (await link.json()).data;
        const bytes = await stageArtifactBytes(
          baseURL,
          signed.url || signed.download_url,
          Number(a.size),
        );
        expect(createHash("sha256").update(bytes).digest("hex")).toBe(a.sha256);
        expect(bytes.length).toBe(Number(a.size));
      }
      results.push({
        kind,
        task_id: task.id,
        source: result.summary.source,
        sections: Object.keys(result.sections || {}),
        artifacts: artifacts.length,
      });
    }
    writeFileSync(
      path.join(evidence, "native-browser-results.json"),
      JSON.stringify(results, null, 2),
    );
    await context.close();
  });

  test("real imported results support normalized comparison and confirmed batch deletion", async ({
    browser,
  }) => {
    test.setTimeout(210000);
    const unique = `全量验收 · 导入批量对比 ${stamp}-${randomUUID().slice(0, 8)}`;
    const work = `imported_compare_work_${stamp}`;
    const io = `imported_compare_io_${stamp}`;
    const samples = [
      [80, 20],
      [120, 80],
    ];
    const created: { id: string; name: string }[] = [];
    const results: { total_samples: number; summary: { source: string } }[] =
      [];
    const preserved = [taskID, ...Object.values(ids)];
    for (const [index, [cpu, wait]] of samples.entries()) {
      const name = unique + (index === 0 ? " · 基准" : " · 对照");
      const response = await post("/tasks", {
        name,
        kind: "IMPORT_COLLAPSED",
        visibility: "private",
        parameters: { options: {} },
        collapsed_text: `imported-comparison-root;${work} ${cpu}\nimported-comparison-root;${io} ${wait}`,
      });
      expect(response.status(), "HTTP " + response.status()).toBe(201);
      const task = (await response.json()).data;
      expect(typeof task.id).toBe("string");
      expect(task.kind).toBe("IMPORT_COLLAPSED");
      created.push({ id: task.id, name });
      await complete(task.id);
      const analyzed = await api.get(`/api/v1/tasks/${task.id}/result`);
      expect(analyzed.status()).toBe(200);
      const result = (await analyzed.json()).data;
      expect(result.summary.source).toBe("import");
      expect(result.total_samples).toBe(cpu + wait);
      results.push(result);
    }
    const context = await browser.newContext({ storageState: state });
    const page = await context.newPage();
    const errors: string[] = [],
      deletedRequests: string[] = [];
    page.on("pageerror", () => errors.push("PAGE_ERROR"));
    page.on("request", (request) => {
      if (request.method() !== "DELETE") return;
      const match = new URL(request.url()).pathname.match(
        /^\/api\/v1\/tasks\/(\d+)$/,
      );
      if (match) deletedRequests.push(match[1]);
    });
    try {
      await page.goto(baseURL + "/tasks");
      await page
        .getByPlaceholder("搜索任务名称或 ID", { exact: true })
        .fill(unique);
      await expect(page.locator(".task-table tbody tr")).toHaveCount(2);
      for (const task of created)
        await page
          .getByRole("checkbox", { name: "选择" + task.name, exact: true })
          .check();
      await page.getByRole("link", { name: "对比结果", exact: true }).click();
      await expect(
        page.getByRole("heading", { level: 1, name: /^任务对比\.?$/ }),
      ).toBeVisible();
      const selected = new URL(page.url()).searchParams;
      expect(selected.get("baseline")).toBe(created[0].id);
      expect(selected.get("candidate")).toBe(created[1].id);
      const row = page.locator(".comparison-top tbody tr").filter({
        has: page.getByRole("rowheader", { name: work, exact: true }),
      });
      await expect(row).toContainText("80.00%");
      await expect(row).toContainText("60.00%");
      await expect(row).toContainText("-20.00");
      await expect(
        page
          .locator(".comparison-profile")
          .getByText("用户导入", { exact: true }),
      ).toHaveCount(2);
      await expect(
        page.locator(".comparison-flames .flame-component"),
      ).toHaveCount(2);
      await expect(
        page
          .locator(".comparison-flames")
          .getByRole("button", { name: new RegExp("^" + work + ",") }),
      ).toHaveCount(2);
      await page
        .locator(".comparison-top")
        .getByRole("button", { name: "总计", exact: true })
        .click();
      const rootRow = page.locator(".comparison-top tbody tr").filter({
        has: page.getByRole("rowheader", {
          name: "imported-comparison-root",
          exact: true,
        }),
      });
      await expect(rootRow.getByText("100.00%", { exact: true })).toHaveCount(
        2,
      );
      await expect(rootRow.locator(".comparison-delta strong")).toHaveText(
        "0.00",
      );
      await page.screenshot({
        path: path.join(evidence, "real-imported-comparison.png"),
        fullPage: true,
      });
      await page
        .getByRole("link", { name: "返回诊断任务", exact: true })
        .click();
      await page
        .getByPlaceholder("搜索任务名称或 ID", { exact: true })
        .fill(unique);
      await expect(page.locator(".task-table tbody tr")).toHaveCount(2);
      for (const task of created)
        await page
          .getByRole("checkbox", { name: "选择" + task.name, exact: true })
          .check();
      await page.getByRole("button", { name: "删除", exact: true }).click();
      const confirmation = page.getByRole("dialog", {
        name: "删除选中的 2 个任务？",
        exact: true,
      });
      await expect(confirmation).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(confirmation).not.toBeVisible();
      expect(deletedRequests).toEqual([]);
      for (const task of created)
        expect((await api.get("/api/v1/tasks/" + task.id)).status()).toBe(200);
      await page.getByRole("button", { name: "删除", exact: true }).click();
      await confirmation
        .getByRole("button", { name: "确认删除", exact: true })
        .click();
      await expect(
        page.getByText("本批次成功 2 项，跳过 0 项，失败 0 项。", {
          exact: true,
        }),
      ).toBeVisible();
      expect([...deletedRequests].sort()).toEqual(
        created.map((task) => task.id).sort(),
      );
      for (const task of created)
        expect((await api.get("/api/v1/tasks/" + task.id)).status()).toBe(404);
      for (const id of preserved)
        expect(
          (await api.get("/api/v1/tasks/" + id)).status(),
          "Preserve previous test task " + id,
        ).toBe(200);
      expect(errors).toEqual([]);
      writeFileSync(
        path.join(evidence, "real-imported-batch-comparison.json"),
        JSON.stringify(
          {
            passed: true,
            checked_at: new Date().toISOString(),
            source: "imported-folded-profiles-through-real-api-and-analyzer",
            imported_profile_data: true,
            backend_verified: true,
            browser_api_mocks: false,
            task_ids: created.map((task) => task.id),
            total_samples: results.map((result) => result.total_samples),
            normalized_self_percentage_points: -20,
            real_flamegraphs_rendered: 2,
            escaped_confirmation_mutations: 0,
            confirmed_delete_requests: deletedRequests,
            deleted_tasks_return_404: true,
            preserved_task_ids: preserved,
            javascript_errors: errors,
          },
          null,
          2,
        ) + "\n",
      );
    } finally {
      await context.close();
    }
  });

  test("read shares enforce matching API and UI permissions", async ({
    browser,
  }) => {
    const username = "full_scope_" + stamp,
      password = randomUUID() + "A1!";
    const created = await post("/users", {
      username,
      password,
      display_name: "隔离权限验收",
      role: "operator",
    });
    expect(created.status()).toBe(201);
    const u = (await created.json()).data;
    const groupResponse = await post("/groups", {
      name: "隔离资源组 " + stamp,
    });
    expect(groupResponse.status()).toBe(201);
    const group = (await groupResponse.json()).data;
    expect(
      (
        await api.put("/api/v1/groups/" + group.id + "/members/" + u.id, {
          data: { role: "viewer" },
          headers: { "X-CSRF-Token": csrf },
        })
      ).ok(),
    ).toBe(true);
    expect(
      (
        await post("/tasks/" + taskID + "/shares", {
          user_id: u.id,
          permission: "read",
        })
      ).status(),
    ).toBe(201);
    const other = safeStageAPI(await request.newContext({ baseURL }));
    const login = await other.post("/api/v1/auth/login", {
      data: { username, password },
    });
    expect(login.status()).toBe(200);
    const token = (await login.json()).data.csrf_token;
    const detail = await other.get("/api/v1/tasks/" + taskID);
    expect(detail.status()).toBe(200);
    expect((await detail.json()).data.permissions).toMatchObject({
      read: true,
      operate: false,
      share: false,
      delete: false,
      reanalyze: false,
    });
    expect(
      (await other.get("/api/v1/tasks/" + taskID + "/events")).status(),
    ).toBe(200);
    expect(
      (
        await other.post("/api/v1/tasks/" + taskID + "/cancel", {
          data: {},
          headers: { "X-CSRF-Token": token },
        })
      ).status(),
    ).toBe(404);
    const context = await browser.newContext({
      storageState: await other.storageState(),
    });
    const page = await context.newPage();
    await page.goto(baseURL + "/tasks/" + taskID);
    await expect(page.getByRole("tab", { name: "分析结果" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "共享权限" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "删除任务" })).toHaveCount(0);
    await context.close();
    await other.dispose();
  });

  test("Cron and composite editors persist real orchestration", async ({
    browser,
  }) => {
    const agent = (
      await (await api.get("/api/v1/agents")).json()
    ).data.items.find((a: { status: string }) => a.status === "online");
    const pid = agent.resources.processes[0].pid;
    const context = await browser.newContext({ storageState: state });
    const page = await context.newPage();
    await page.goto(baseURL + "/schedules");
    await page.getByRole("button", { name: "创建计划", exact: true }).click();
    await page.getByLabel("计划名称").fill("全量验收 · Cron " + stamp);
    await page.getByLabel("采集方式").selectOption("PROC_CPU");
    await page.getByLabel("Agent 节点").selectOption(agent.id);
    await page.getByLabel("目标 PID", { exact: true }).fill(String(pid));
    await page.getByLabel("触发方式").selectOption("cron");
    await page.getByLabel("Cron 表达式").fill("0 9 * * 1-5");
    await page.getByLabel("IANA 时区").fill("America/New_York");
    await page
      .getByRole("button", { name: "创建计划", exact: true })
      .last()
      .click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByText("全量验收 · Cron " + stamp)).toBeVisible();
    const schedules = (await (await api.get("/api/v1/schedules")).json()).data
      .items;
    const schedule = schedules.find(
      (s: { name: string }) => s.name === "全量验收 · Cron " + stamp,
    );
    expect(schedule.cron).toBe("0 9 * * 1-5");
    expect(schedule.timezone).toBe("America/New_York");
    expect(
      (
        await api.patch("/api/v1/schedules/" + schedule.id, {
          data: { enabled: false },
          headers: { "X-CSRF-Token": csrf },
        })
      ).ok(),
    ).toBe(true);
    await page.goto(baseURL + "/composites");
    await page
      .getByRole("button", { name: "编排复合诊断", exact: true })
      .click();
    await page
      .getByLabel("名称", { exact: true })
      .fill("全量验收 · DAG " + stamp);
    await page.getByLabel("Agent 节点").selectOption(agent.id);
    await page.getByLabel("目标 PID", { exact: true }).fill(String(pid));
    await page.getByLabel("采集时长（秒）").fill("2");
    await page
      .getByRole("button", { name: "创建复合诊断", exact: true })
      .click();
    await expect(page).toHaveURL(/\/composites\/\d+$/);
    const id = page.url().split("/").pop();
    await expect
      .poll(
        async () => {
          const r = await api.get("/api/v1/composites/" + id);
          return (await r.json()).data.status;
        },
        { timeout: 60000, intervals: [1000, 2000] },
      )
      .toBe("SUCCESS");
    await page.reload();
    await expect(
      page.getByRole("link", { name: "查看诊断结果" }),
    ).toBeVisible();
    await page.screenshot({
      path: path.join(evidence, "composite-result.png"),
      fullPage: true,
    });
    await context.close();
  });

  test("new routes pass accessibility and responsive checks", async ({
    browser,
  }) => {
    test.setTimeout(180000);
    const context = await browser.newContext({ storageState: state });
    const page = await context.newPage();
    const errors: string[] = [],
      reports = [];
    page.on("pageerror", () => errors.push("PAGE_ERROR"));
    for (const route of [
      "/tasks/new",
      "/agents",
      "/groups",
      "/rules",
      "/composites",
      "/schedules",
      "/tasks/" + ids.JAVA_HEAP,
      "/tasks/" + ids.IO_TRACE,
    ]) {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(baseURL + route);
      await page.waitForTimeout(400);
      const axe = await new AxeBuilder({ page }).analyze();
      reports.push({ route, violations: axe.violations });
      expect(axe.violations, route).toEqual([]);
      for (const width of [375, 768, 1440]) {
        await page.setViewportSize({
          width,
          height: width === 375 ? 812 : 1000,
        });
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          route + " " + width,
        ).toBe(true);
      }
      await page.screenshot({
        path: path.join(
          evidence,
          "responsive-" + route.replaceAll("/", "_") + ".png",
        ),
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 812, height: 375 });
    await page.goto(baseURL + "/tasks/new");
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    writeFileSync(
      path.join(evidence, "axe-routes.json"),
      JSON.stringify(reports, null, 2),
    );
    expect(errors).toEqual([]);
    await context.close();
  });
});
