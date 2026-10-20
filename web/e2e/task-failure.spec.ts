import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const directory = path.resolve(
  import.meta.dirname,
  "../../docs/evidence/full/failure-fixtures-rc6",
);

async function fixture(page: Page, withFiles: boolean) {
  let reanalyzed = false;
  const calls: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", () => errors.push("PAGE_ERROR"));
  const artifact = (
    id: string,
    kind: string,
    name: string,
    attempt = "20",
    extra = {},
  ) => ({
    id,
    task_id: "7",
    attempt_id: attempt,
    kind,
    name,
    size: 12,
    sha256: "a".repeat(64),
    content_type: "application/octet-stream",
    status: "ready",
    download_allowed: true,
    created_at: "2026-10-18T00:01:00Z",
    ...extra,
  });
  const files = withFiles
    ? [
        artifact("30", "RAW", "current-profile.raw"),
        artifact("31", "LOG", "current-runner.log"),
        artifact("32", "BINARY", "current-target.elf"),
        artifact("33", "RAW", "OLD-ATTEMPT-MUST-NOT-SHOW", "10"),
        artifact("34", "RESULT", "OLD-ANALYSIS-MUST-NOT-SHOW.json"),
        artifact("35", "MANIFEST", "OLD-MANIFEST-MUST-NOT-SHOW.json"),
        artifact("36", "RAW", "EXPIRED-MUST-NOT-SHOW", "20", {
          expires_at: "2020-01-01T00:00:00Z",
        }),
      ]
    : [];
  const events = [
    {
      sequence: "2",
      task_id: "7",
      attempt_id: "10",
      type: "RUNNER_STARTED",
      source: "agent",
      payload: {},
      created_at: "2026-10-17T00:00:00Z",
    },
    {
      sequence: "7",
      task_id: "7",
      attempt_id: "20",
      type: "RUNNER_STARTED",
      source: "agent",
      payload: {},
      created_at: "2026-10-18T00:00:00Z",
    },
    {
      sequence: "8",
      task_id: "7",
      attempt_id: "20",
      type: withFiles ? "ANALYSIS_FAILED" : "TASK_FAILED",
      source: withFiles ? "analyzer" : "core",
      payload: withFiles ? { retryable: false, dead_letter: true } : {},
      created_at: "2026-10-18T00:02:00Z",
    },
  ];
  await page.route("**/api/v1/**", async (route) => {
    const endpoint = new URL(route.request().url()).pathname.replace(
      "/api/v1",
      "",
    );
    calls.push(endpoint);
    let data: unknown;
    if (endpoint === "/auth/me")
      data = {
        user: {
          id: "fixture-user",
          username: "fixture",
          display_name: "Imported failure fixture",
          role: withFiles ? "platform_admin" : "viewer",
        },
        csrf_token: "fixture-csrf",
      };
    else if (endpoint === "/auth/providers") data = { ai: { enabled: false } };
    else if (endpoint === "/task-kinds")
      data = [{ id: "PERF_CPU", display_name: "性能采样" }];
    else if (endpoint === "/tasks")
      data = {
        items: [
          {
            id: "7",
            name: "Imported failed execution fixture",
            kind: "PERF_CPU",
            params: {},
            creator_id: "fixture-user",
            target_agent_id: "fixture-agent",
            collection_status: "COLLECTED",
            analysis_status: "FAILED",
            permissions: {
              read: true,
              operate: true,
              share: false,
              delete: false,
              reanalyze: true,
            },
            created_at: "2026-10-18T00:00:00Z",
            updated_at: "2026-10-18T00:02:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 12,
      };
    else if (endpoint === "/tasks/7/reanalyze") {
      expect(route.request().method()).toBe("POST");
      expect(route.request().headers()["x-csrf-token"]).toBe("fixture-csrf");
      reanalyzed = true;
      data = {};
    } else if (endpoint === "/tasks/7/events/stream")
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: ": imported fixture\n\n",
      });
    else if (endpoint === "/tasks/7/events")
      data = { items: events, total: events.length };
    else if (endpoint === "/tasks/7")
      data = {
        id: "7",
        name: "Imported failed execution fixture",
        kind: "PERF_CPU",
        params: {},
        creator_id: "fixture-user",
        target_agent_id: "fixture-agent",
        collection_status: withFiles || reanalyzed ? "COLLECTED" : "FAILED",
        analysis_status: reanalyzed ? "PENDING" : "FAILED",
        request_id: withFiles ? "imported-request-7" : undefined,
        error_code: reanalyzed ? undefined : "INVALID_INPUT",
        error_message: reanalyzed ? undefined : "Imported failure message",
        attempts: [
          { id: "20", attempt: 2, status: withFiles ? "COLLECTED" : "FAILED" },
          { id: "10", attempt: 1, status: "FAILED" },
        ],
        artifacts: files,
        events,
        permissions: {
          read: true,
          operate: withFiles,
          share: false,
          delete: false,
          reanalyze: withFiles,
        },
        created_at: "2026-10-18T00:00:00Z",
        updated_at: reanalyzed
          ? "2026-10-18T00:03:00Z"
          : "2026-10-18T00:02:00Z",
      };
    else {
      errors.push("UNHANDLED_FIXTURE_ENDPOINT");
      return route.fulfill({ status: 404, json: {} });
    }
    return route.fulfill({
      status: 200,
      json: { error: null, request_id: "imported-fixture", data },
    });
  });
  return { calls, errors };
}

test("failure evidence shows current saved collection files, seven stages and safe recovery", async ({
  page,
  baseURL,
}) => {
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1");
  mkdirSync(directory, { recursive: true });
  const evidence = await fixture(page, true);
  await page.goto("/tasks/7");
  const failure = page.getByRole("region", { name: "本次执行诊断" });
  await expect(failure).toBeVisible();
  await expect(failure).toContainText("离线分析");
  await expect(failure).toContainText("自动重试已结束");
  await expect(failure).toContainText("imported-request-7");
  await expect(failure).toContainText("#7 / 20");
  await expect(failure.locator(".failure-saved li")).toHaveCount(3);
  await expect(failure).not.toContainText("MUST-NOT-SHOW");
  await expect(page.locator(".progress-stage")).toHaveCount(7);
  await expect(
    page.locator('.progress-stage[data-stage-state="failed"]'),
  ).toContainText("分析中");
  await expect(
    page.locator('.progress-stage[data-stage-state="pending"]'),
  ).toContainText("结果可用");
  const checks = [];
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate(() => document.fonts.ready);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const violations = (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations;
    expect(violations).toEqual([]);
    const noResultBox = await page
      .locator(".failed-result-summary")
      .boundingBox();
    expect(noResultBox!.height).toBeLessThan(220);
    expect(
      await failure.locator("button").evaluateAll((buttons) =>
        buttons.every((button) => {
          const box = button.getBoundingClientRect();
          return box.width >= 44 && box.height >= 44;
        }),
      ),
    ).toBe(true);
    await page.screenshot({
      path: path.join(directory, `failure-${width}.png`),
      fullPage: true,
    });
    checks.push({
      width,
      axe_violations: violations.length,
      document_overflow: false,
    });
  }
  await failure.getByRole("button", { name: "查看本次时间线" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.locator('.timeline-marker[data-event-tone="failed"]'),
  ).toHaveAttribute("aria-label", "执行失败");
  await failure
    .getByRole("button", { name: "重新分析原始数据", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(failure).toHaveCount(0);
  expect(evidence.calls).toContain("/tasks/7/reanalyze");
  expect(evidence.calls).not.toContain("/tasks/7/result");
  await page.goto("/tasks");
  await expect(
    page.getByRole("link", {
      name: /^Imported failed execution fixture #7/,
    }),
  ).toBeVisible();
  const selection = page.getByRole("checkbox", {
    name: "选择Imported failed execution fixture",
    exact: true,
  });
  await selection.focus();
  await page.keyboard.press("Space");
  await expect(selection).toBeChecked();
  await page.keyboard.press("Space");
  await expect(selection).not.toBeChecked();
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate(() => document.fonts.ready);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.screenshot({
      path: path.join(directory, `tasks-${width}.png`),
      fullPage: true,
    });
  }
  expect(evidence.errors).toEqual([]);
  writeFileSync(
    path.join(directory, "report.json"),
    JSON.stringify(
      {
        provenance:
          "Explicit imported UI/API metadata; no actual backend sampling",
        checks,
        failure_icon_not_success: true,
        current_attempt_only: true,
        no_failed_result_query: true,
        reanalysis_permission_and_csrf: true,
        keyboard_activation_and_selection: true,
        task_list_375_1440_axe_and_no_document_overflow: true,
      },
      null,
      2,
    ),
  );
});

test("failed read-only execution explicitly has no readable saved artifacts", async ({
  page,
  baseURL,
}) => {
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1");
  const evidence = await fixture(page, false);
  await page.goto("/tasks/7");
  const failure = page.getByRole("region", { name: "本次执行诊断" });
  await expect(failure).toContainText("失败阶段");
  await expect(failure).toContainText("采集");
  await expect(failure).toContainText("服务端未记录");
  await expect(failure).toContainText("本次执行尚未生成可安全读取的制品");
  await expect(
    failure.getByRole("button", { name: /重新采集|重新分析|重新导入/ }),
  ).toHaveCount(0);
  expect(evidence.calls).not.toContain("/tasks/7/result");
  expect(evidence.errors).toEqual([]);
});
