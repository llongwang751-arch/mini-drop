import { describe, expect, it } from "vitest";
import { parseBookMarkdown, nodeText } from "./markdown";
import { parseBookPreferences } from "./preferences";

describe("book reading content", () => {
  it("keeps repeated section names independently addressable and excludes fenced headings", () => {
    const result = parseBookMarkdown(
      "## 再试一次\n正文\n\n```text\n## 代码里的标题\n```\n\n## 再试一次\n第二段",
    );
    expect(result.sections).toEqual([
      { id: "section-1", title: "再试一次" },
      { id: "section-2", title: "再试一次" },
    ]);
  });

  it("drops raw HTML before a tutorial can become executable markup", () => {
    const result = parseBookMarkdown(
      '## 例子\n\n<script>alert(1)</script>\n\n<iframe src="https://example.com"></iframe>\n\n保留正文',
    );
    expect(JSON.stringify(result.tree)).not.toContain('"tagName":"script"');
    expect(JSON.stringify(result.tree)).not.toContain('"tagName":"iframe"');
    expect(result.tree.children.map(nodeText).join("")).toContain("保留正文");
  });
});

describe("book settings recovery", () => {
  it.each([
    null,
    "broken json",
    '"wrong shape"',
    '{"fontSize":9999,"theme":"url(bad)"}',
    '{"fontSize":null}',
  ])("recovers safe defaults for invalid browser storage %s", (input) => {
    expect(parseBookPreferences(input)).toEqual({
      theme: "light",
      fontSize: 18,
    });
  });
});
