/**
 * 新扩展页面的真实浏览器交互；数据/会话/审批均为明确导入的合成夹具，不调用模型或生产。
 * UI验收不证明后端授权、真实因果、沙箱性能或生产发布；这些边界由各自服务集成测试验证。
 */
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const now = "2026-10-21T01:00:00Z";
const skill = {
  id: "library_imported",
  version: "1",
  title: "导入合成CPU方法",
  task_kinds: ["PYTHON_CPU"],
  required_tools: ["inspect_hotspots"],
  steps: ["先核对当前采样单位与窗口"],
  counter_evidence: ["热点占比不证明业务回退"],
  stop_conditions: ["没有可比证据时停止因果判断"],
  verification: ["对齐负载并进行受控复测"],
};
const diff =
  "--- a/business.py\n+++ b/business.py\n@@ -1 +1 @@\n-before\n+after\n--- /dev/null\n+++ b/drop_regression/test_edge.py\n@@ -0,0 +1 @@\n+assert True\n";
const validation = {
  success: true,
  accepted: true,
  functional_regression: "passed",
  output_contract: "drop-python-project-v1",
  executor_contract: 4,
  output_parity_cases: 7200,
  median_cpu_speedup: 1.2,
  improved_pairs: 6,
  pairs: [],
  original_tests_unchanged: true,
  root_cause_status: "validated_in_controlled_python_benchmark",
  scope: "导入的合成组件受控验证，不是生产收益",
  patch: [],
  regression_test_validation: {
    baseline_failed: true,
    candidate_passed: true,
    same_test_files: true,
    baseline: {
      tests: 1,
      failures: 1,
      errors: 0,
      skipped: 0,
      unexpected_successes: 0,
      expected_failures: 0,
      success: false,
    },
    candidate: {
      tests: 1,
      failures: 0,
      errors: 0,
      skipped: 0,
      unexpected_successes: 0,
      expected_failures: 0,
      success: true,
    },
  },
  negative_control: {
    kind: "identical_frozen_baseline",
    ratios: [1, 1, 1, 1, 1, 1],
    median_ratio: 1,
    stable: true,
  },
  patch_bundle: {
    schema_version: 1,
    format: "unified-diff",
    source_revision: "a".repeat(64),
    content: diff,
    sha256: createHash("sha256").update(diff).digest("hex"),
    code_files: ["business.py"],
    test_files: ["drop_regression/test_edge.py"],
    applied_to: "isolated_snapshot",
    production_deployed: false,
  },
};

async function importedFixture(
  page: Page,
  options: {
    viewer?: boolean;
    reviewError?: boolean;
    decisionError?: boolean;
    sourcesError?: boolean;
    badSHA?: boolean;
    candidate?: boolean;
    traceDenied?: boolean;
  } = {},
) {
  const writes: { path: string; body: Record<string, unknown> }[] = [],
    reads: string[] = [],
    unexpected: string[] = [],
    errors: string[] = [];
  const books = [
    {
      id: "61",
      owner_id: "imported-user",
      logical_key: "imported",
      version: 1,
      status: "draft",
      skill,
      content_digest: "a".repeat(64),
      expires_at: "2026-11-21T01:00:00Z",
      created_at: now,
      updated_at: now,
    },
  ];
  const followup = {
    id: "71",
    investigation_id: "101",
    parent_task_id: "201",
    source_diagnosis_id: "301",
    status: "proposed",
    plan: {
      plan: {
        kind: "IO_TRACE",
        duration_seconds: 10,
        frequency_hz: 0,
        rationale: "导入补证：核对本轮等待路径，不直接推断因果。",
        options: { scope: "process" },
      },
      evidence_ids: ["E3"],
      requires_confirmation: true,
    },
    plan_digest: "b".repeat(64),
    created_at: now,
    updated_at: now,
  };
  const run = {
    id: "101",
    requested_by: "imported-user",
    agent_id: "imported-node",
    status: "success",
    goal: "导入扩展界面调查",
    created_at: now,
    trace: [
      {
        evidence_id: "P1",
        tool: "list_collectors",
        observation: { imported: true },
      },
    ],
    diagnosis_id: "301",
    baseline_task_id: "201",
    parameters: {},
    report: {
      diagnosis: {
        report_version: 2,
        summary: "导入的当前事实摘要",
        facts: [
          {
            id: "F1",
            evidence_id: "E3",
            tool: "inspect_hotspots",
            path: "/top_functions/0/self_percent",
            operator: "eq",
            value: 70,
            unit: "percent",
            description: "本轮排名1的self占比为70%，不代表请求耗时。",
          },
        ],
        hypotheses: [
          {
            unverified_interpretation:
              "导入的待验证模型解释，不能当作已核验事实。",
          },
        ],
      },
    },
  };
  const candidateReport = {
    ...run.report.diagnosis,
    facts: run.report.diagnosis.facts.map((fact) => ({
      ...fact,
      value: 33,
      description: "导入复测任务的self占比为33%，与基线E3分别绑定。",
    })),
  };
  const actualRun = options.candidate
    ? {
        ...run,
        verification_id: "401",
        report: {
          ...run.report,
          candidate_diagnosis_id: "302",
          candidate_report: candidateReport,
        },
      }
    : run;
  let reviewFailed = false,
    decisionFailed = false;
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", "");
    if (url.hostname !== "127.0.0.1") {
      unexpected.push(url.origin);
      await route.abort();
      return;
    }
    const respond = (data: unknown, status = 200) =>
      route.fulfill({
        status,
        json: { data, error: null, request_id: "imported-agent-extension" },
      });
    const fail = (message: string) =>
      route.fulfill({
        status: 403,
        json: {
          data: null,
          error: { code: "IMPORTED_DENIED", message },
          request_id: "imported-agent-extension",
        },
      });
    const list = (items: unknown[]) => ({
      items,
      total: items.length,
      page: 1,
      page_size: items.length,
    });
    if (req.method() === "POST") {
      writes.push({ path, body: req.postDataJSON() });
      if (path === "/diagnostic-playbooks") {
        const input = req.postDataJSON();
        const item = {
          ...books[0],
          id: "62",
          logical_key: input.logical_key,
          skill: {
            ...input.skill,
            id: "library_" + input.logical_key,
            version: String(input.version),
          },
        };
        books.push(item);
        await respond(item, 201);
        return;
      }
      if (path === "/diagnostic-playbooks/61/review") {
        if (options.reviewError && !reviewFailed) {
          reviewFailed = true;
          await fail("导入的审批权限变化，请重新核对");
          return;
        }
        books[0].status =
          req.postDataJSON().action === "publish" ? "published" : "retired";
        await respond(books[0]);
        return;
      }
      if (path === "/investigations/101/followups/71/decision") {
        if (options.decisionError && !decisionFailed) {
          decisionFailed = true;
          await fail("导入的补证权限变化");
          return;
        }
        followup.status = req.postDataJSON().confirm ? "queued" : "rejected";
        await respond(followup);
        return;
      }
      if (path === "/investigations/101/cancel") {
        followup.status = "canceled";
        actualRun.status = "canceled";
        await respond({ canceled: true });
        return;
      }
      unexpected.push(`${req.method()} ${path}`);
      await route.abort();
      return;
    }
    if (req.method() !== "GET") {
      unexpected.push(`${req.method()} ${path}`);
      await route.abort();
      return;
    }
    reads.push(path);
    if (path === "/tasks/201/verifications") {
      await respond(
        list([
          {
            id: "401",
            candidate_task_id: "202",
            candidate_diagnosis_id: "302",
          },
        ]),
      );
      return;
    }
    if (path === "/tasks/201/diagnoses" || path === "/tasks/202/diagnoses") {
      if (options.traceDenied && path === "/tasks/202/diagnoses") {
        await fail("导入复测诊断轨迹已失去授权");
        return;
      }
      const candidate = path === "/tasks/202/diagnoses";
      await respond(
        list([
          {
            id: candidate ? "302" : "301",
            task_id: candidate ? "202" : "201",
            status: "success",
            trace: [
              {
                evidence_id: "E3",
                tool: "inspect_hotspots",
                observation: {
                  self_percent: candidate ? 33 : 70,
                  imported: true,
                },
              },
            ],
          },
        ]),
      );
      return;
    }
    if (path === "/auth/me") {
      await respond({
        user: {
          id: "imported-user",
          username: "imported-user",
          display_name: "导入浏览器验证",
          role: options.viewer ? "viewer" : "platform_admin",
        },
        csrf_token: "imported-csrf",
      });
      return;
    }
    if (path === "/auth/providers") {
      await respond({
        local: true,
        ai: { enabled: true, name: "imported-model" },
        oidc: { enabled: false },
      });
      return;
    }
    if (path === "/task-kinds") {
      await respond([]);
      return;
    }
    if (path === "/diagnostic-skills") {
      await respond(list([skill]));
      return;
    }
    if (path === "/diagnostic-cases") {
      await respond(list([]));
      return;
    }
    if (path === "/diagnostic-playbooks") {
      await respond(list(books));
      return;
    }
    if (path === "/groups") {
      await respond(list([{ id: "imported-team", name: "导入合成团队" }]));
      return;
    }
    if (path === "/agents") {
      await respond(
        list(
          ["imported-node", "imported-other"].map((id) => ({
            id,
            hostname: id,
            status: "online",
            resources: {
              processes: [
                {
                  pid: 99,
                  name: "imported-python",
                  uid: 10001,
                  start_time_ticks: 123,
                },
              ],
            },
          })),
        ),
      );
      return;
    }
    if (path === "/diagnostic-data-sources") {
      if (options.sourcesError) {
        await fail("导入的业务数据源权限错误");
        return;
      }
      await respond(
        list(
          url.searchParams.get("agent_id") === "imported-node"
            ? [
                {
                  id: "imported-checkout",
                  label: "导入结算服务",
                  signal_types: ["metrics", "logs", "traces", "changes"],
                },
              ]
            : [],
        ),
      );
      return;
    }
    if (path === "/code-projects") {
      await respond({
        projects: [
          {
            id: "synthetic-python",
            label: "导入通用Python组件",
            source_revision: "a".repeat(64),
            editable_files: ["business.py"],
            test_preset: "manifest-python-v1",
            language: "python",
            runtime: {
              agent_id: "imported-node",
              pid: 99,
              uid: 10001,
              start_time_ticks: 123,
              source_revision: "a".repeat(64),
            },
          },
        ],
      });
      return;
    }
    if (path === "/investigations") {
      await respond(list([actualRun]));
      return;
    }
    if (path === "/investigations/101") {
      await respond(actualRun);
      return;
    }
    if (path === "/investigations/101/followups") {
      await respond(list([followup]));
      return;
    }
    if (path === "/code-repairs" || path === "/code-repairs/91") {
      const repair = {
        id: "91",
        project_id: "synthetic-python",
        source_revision: "a".repeat(64),
        goal: "导入受控修复",
        status: "success",
        created_at: now,
        trace: [],
        report: {
          summary: "导入修复回执",
          evidence_ids: [],
          locations: [],
          validation: options.badSHA
            ? {
                ...validation,
                patch_bundle: {
                  ...validation.patch_bundle,
                  sha256: "b".repeat(64),
                },
              }
            : validation,
        },
      };
      await respond(path.endsWith("/91") ? repair : list([repair]));
      return;
    }
    unexpected.push(`${req.method()} ${path}`);
    await route.abort();
  });
  return { writes, reads, unexpected, errors };
}
async function layoutAndAxe(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // 全页截图从页面顶端开始，避免Chromium把原滚动位置外的fixed跳转链接拼进证据图。
  await page.evaluate(() => window.scrollTo(0, 0));
}

async function keyboardTable(page: Page, name: string) {
  const region = page.getByRole("region", { name, exact: true });
  if (
    await region.evaluate(
      (element) => element.scrollWidth > element.clientWidth,
    )
  ) {
    await region.focus();
    await region.press("ArrowRight");
    await expect
      .poll(() => region.evaluate((element) => element.scrollLeft))
      .toBeGreaterThan(0);
    for (let i = 0; i < 4; i++) await region.press("ArrowLeft");
    await expect
      .poll(() => region.evaluate((element) => element.scrollLeft))
      .toBe(0);
  }
}

for (const width of [1440, 375]) {
  test(`imported playbook creation review and keyboard at ${width}px`, async ({
    page,
  }, info) => {
    const journal = await importedFixture(page, { reviewError: true });
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/knowledge");
    await page.getByRole("tab", { name: "诊断 Skill", exact: true }).focus();
    await page.keyboard.press("End");
    await expect(
      page.getByRole("tab", { name: "项目与团队方法", exact: true }),
    ).toBeFocused();
    const card = page.getByRole("article").filter({
      has: page.getByRole("heading", { name: skill.title, exact: true }),
    });
    await expect(
      card.getByRole("button", { name: "发布此版本" }),
    ).toBeDisabled();
    await card.getByText("审阅步骤与适用边界").click();
    await card.getByRole("checkbox").focus();
    await page.keyboard.press("Space");
    await card.getByRole("button", { name: "发布此版本" }).click();
    await expect(card.getByRole("alert")).toContainText("导入的审批权限变化");
    await card.getByRole("button", { name: "发布此版本" }).click();
    await expect(
      card.getByRole("button", { name: "退役此版本" }),
    ).toBeDisabled();
    await card.getByRole("checkbox").check();
    await card.getByRole("button", { name: "退役此版本" }).click();
    await expect(card.getByText("v1 · 已退役")).toBeVisible();
    await page.getByRole("button", { name: "新建方法草稿" }).click();
    await page.getByLabel("方法标识", { exact: true }).fill("imported_memory");
    await page.getByLabel("标题", { exact: true }).fill("导入内存排查方法");
    await page.getByLabel("适用采集类型").selectOption("PYTHON_MEMORY");
    await page.getByLabel("共享范围").selectOption("imported-team");
    await page.getByLabel("绑定项目（可选）").fill("synthetic-python");
    await layoutAndAxe(page);
    const keyboardLayout = await page.evaluate(() => {
      const skip = document.querySelector<HTMLElement>(".skip-link")!;
      const style = getComputedStyle(skip);
      return {
        activeElement: document.activeElement?.tagName,
        skipFocused: skip === document.activeElement,
        transform: style.transform,
        position: style.position,
        y: skip.getBoundingClientRect().y,
        scrollY,
      };
    });
    await writeFile(
      info.outputPath("keyboard-layout-state.json"),
      JSON.stringify(keyboardLayout),
      "utf8",
    );
    await page.screenshot({
      path: info.outputPath(`playbooks-form-${width}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "草稿已保存" }),
    ).toBeVisible();
    expect(
      journal.writes.find((w) => w.path === "/diagnostic-playbooks")?.body,
    ).toMatchObject({
      logical_key: "imported_memory",
      skill: { task_kinds: ["PYTHON_MEMORY"] },
    });
    expect(
      journal.writes
        .filter((w) => w.path.endsWith("/review"))
        .every((w) => w.body.review_confirmed === true),
    ).toBe(true);
    expect(journal.unexpected).toEqual([]);
    expect(journal.errors).toEqual([]);
  });
  test(`imported followup review failure and terminal-root cancellation at ${width}px`, async ({
    page,
  }, info) => {
    const journal = await importedFixture(page, { decisionError: true });
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/investigations");
    const section = page.getByRole("region", { name: "动态补证分支" });
    await expect(
      section.getByRole("button", { name: "确认补采集", exact: true }),
    ).toBeDisabled();
    await expect(
      section.getByRole("button", { name: "不执行此计划", exact: true }),
    ).toBeDisabled();
    await section.getByRole("checkbox").focus();
    await page.keyboard.press("Space");
    await section
      .getByRole("button", { name: "确认补采集", exact: true })
      .click();
    await expect(section.getByRole("alert")).toContainText(
      "导入的补证权限变化",
    );
    await section
      .getByRole("button", { name: "确认补采集", exact: true })
      .click();
    await expect(
      section.getByText("已确认，等待采集", { exact: true }),
    ).toBeVisible();
    await section
      .getByRole("button", { name: "停止后续补证", exact: true })
      .click();
    const confirm = section.getByRole("group", { name: "确认停止后续补证" });
    await expect(confirm.getByRole("button")).toBeDisabled();
    await layoutAndAxe(page);
    await section.screenshot({
      path: info.outputPath(`followups-${width}.png`),
    });
    await confirm.getByRole("checkbox").check();
    await confirm.getByRole("button").click();
    await expect(section.getByText("已取消", { exact: true })).toBeVisible();
    expect(journal.writes.at(-1)).toEqual({
      path: "/investigations/101/cancel",
      body: {},
    });
    expect(journal.unexpected).toEqual([]);
    expect(journal.errors).toEqual([]);
  });
  test(`imported telemetry target scope and unknown state at ${width}px`, async ({
    page,
  }, info) => {
    const journal = await importedFixture(page);
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/investigations");
    await page
      .getByRole("combobox", { name: "目标节点", exact: true })
      .selectOption("imported-node");
    const picker = page.getByLabel("关联业务信号（可选）");
    await expect(
      picker.getByRole("option", { name: /导入结算服务/ }),
    ).toHaveCount(1);
    await picker.selectOption("imported-checkout");
    await page
      .getByRole("combobox", { name: "目标节点", exact: true })
      .selectOption("imported-other");
    await expect(picker).toHaveValue("");
    await expect(
      page.getByText(
        "当前节点没有可用的已登记业务数据源；仍可仅使用采集证据。",
      ),
    ).toBeVisible();
    await layoutAndAxe(page);
    await page.screenshot({
      path: info.outputPath(`telemetry-${width}.png`),
      fullPage: true,
    });
    expect(journal.writes).toEqual([]);
    expect(journal.unexpected).toEqual([]);
    expect(journal.errors).toEqual([]);
  });
  test(`imported facts anchors and verified patch download at ${width}px`, async ({
    page,
  }, info) => {
    const journal = await importedFixture(page);
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/investigations");
    const facts = page.getByRole("region", { name: "结构化事实与模型解释" });
    await expect(facts.getByText("字段、数值、单位与出处已核对")).toBeVisible();
    await facts.getByText("字段与单位", { exact: true }).click();
    await expect(
      facts.getByText("未经事实核验的模型解释 · 假设 1"),
    ).toBeVisible();
    await keyboardTable(page, "已核对事实来源表");
    await facts.screenshot({ path: info.outputPath(`facts-${width}.png`) });
    await facts.getByRole("link", { name: "E3", exact: true }).click();
    await expect(page.locator("#diagnosis-301-E3")).toBeInViewport();
    await page
      .getByRole("button", { name: "源码定位与修复", exact: true })
      .click();
    const receipt = page.getByRole("region", { name: "缺陷回归与补丁交付" });
    await expect(receipt.getByText("独立回归前后验证通过")).toBeVisible();
    await receipt.getByText("审阅代码与新增测试 diff").click();
    const pending = page.waitForEvent("download");
    await receipt.getByRole("button", { name: "核对 SHA 并下载补丁" }).click();
    const download = await pending;
    expect(download.suggestedFilename()).toBe(
      `drop-${validation.patch_bundle.sha256.slice(0, 12)}.patch`,
    );
    await download.saveAs(info.outputPath(`imported-${width}.patch`));
    await expect(receipt.getByRole("status")).toContainText("补丁 SHA 已核对");
    await keyboardTable(page, "新增回归用例实际计数");
    await layoutAndAxe(page);
    await receipt.screenshot({ path: info.outputPath(`repair-${width}.png`) });
    expect(journal.writes).toEqual([]);
    expect(journal.unexpected).toEqual([]);
    expect(journal.errors).toEqual([]);
  });
}
test("imported denied telemetry and substituted patch fail visibly", async ({
  page,
}) => {
  const journal = await importedFixture(page, {
    sourcesError: true,
    badSHA: true,
  });
  await page.goto("/investigations");
  await page
    .getByRole("combobox", { name: "目标节点", exact: true })
    .selectOption("imported-node");
  await expect(page.getByRole("alert")).toContainText(
    "导入的业务数据源权限错误",
  );
  await page
    .getByRole("button", { name: "源码定位与修复", exact: true })
    .click();
  const receipt = page.getByRole("region", { name: "缺陷回归与补丁交付" });
  const downloads: string[] = [];
  page.on("download", (download) =>
    downloads.push(download.suggestedFilename()),
  );
  await receipt.getByRole("button", { name: "核对 SHA 并下载补丁" }).click();
  await expect(receipt.getByRole("status")).toContainText(
    "SHA 不一致，已停止下载",
  );
  expect(downloads).toEqual([]);
  expect(journal.writes).toEqual([]);
  expect(journal.unexpected).toEqual([]);
});
test("imported viewer cannot publish create or approve followups", async ({
  page,
}) => {
  const journal = await importedFixture(page, { viewer: true });
  await page.goto("/knowledge?tab=playbooks");
  await expect(page.getByRole("heading", { name: skill.title })).toBeVisible();
  await expect(page.getByRole("button", { name: "发布此版本" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "新建方法草稿" })).toHaveCount(
    0,
  );
  await page.goto("/investigations");
  await expect(
    page.getByRole("region", { name: "动态补证分支" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认补采集", exact: true }),
  ).toHaveCount(0);
  expect(journal.writes).toEqual([]);
  expect(journal.unexpected).toEqual([]);
  expect(journal.errors).toEqual([]);
});

for (const traceDenied of [false, true]) {
  test(`imported candidate facts use their own diagnosis trace or plain source (${traceDenied ? "denied" : "linked"})`, async ({
    page,
  }) => {
    const journal = await importedFixture(page, {
      candidate: true,
      traceDenied,
    });
    await page.setViewportSize({
      width: traceDenied ? 375 : 1440,
      height: 1000,
    });
    await page.goto("/investigations");
    const facts = page.getByRole("region", { name: "结构化事实与模型解释" });
    await expect(
      facts.getByText("导入复测任务的self占比为33%，与基线E3分别绑定。"),
    ).toBeVisible();
    if (traceDenied) {
      await expect(page.getByRole("alert")).toContainText(
        "导入复测诊断轨迹已失去授权",
      );
      await expect(
        facts.getByRole("link", { name: "E3", exact: true }),
      ).toHaveCount(0);
      await expect(
        page.locator("#diagnosis-301-E3, #diagnosis-302-E3"),
      ).toHaveCount(0);
    } else {
      await expect(
        facts.getByRole("link", { name: "E3", exact: true }),
      ).toHaveAttribute("href", "#diagnosis-302-E3");
      await facts.getByRole("link", { name: "E3", exact: true }).click();
      await expect(page.locator("#diagnosis-302-E3")).toBeInViewport();
      await expect(page.locator("#diagnosis-301-E3")).toHaveCount(0);
    }
    expect(journal.reads).toContain("/tasks/201/verifications");
    expect(journal.reads).toContain("/tasks/202/diagnoses");
    expect(journal.reads).not.toContain("/tasks/201/diagnoses");
    expect(journal.unexpected).toEqual([]);
    expect(journal.errors).toEqual([]);
  });
}
