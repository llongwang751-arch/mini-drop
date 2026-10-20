/**
 * 不可信诊断文本的 AST 和链接策略测试；纯 Node 验证不等于浏览器 DOM 清理已验收。
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SafeMarkdown } from "./SafeMarkdown";
import SafeMarkdownContent from "./SafeMarkdownContent";
import { safeMarkdownUrl } from "./safe-markdown-policy";
import { diagnosticMarkdownHTML } from "./markdown-html";

// 使用导入的合成模型/规则文本；Node 单测只检查不会执行资源请求的 AST。
// 本单测不访问浏览器或网络，DOMPurify 的实际清理行为另由浏览器测试验证。
const html = diagnosticMarkdownHTML;

describe("untrusted diagnostic Markdown", () => {
  it("escapes text while the parser chunk is loading", () => {
    const output = renderToStaticMarkup(
      createElement(SafeMarkdown, {
        children: '<img src="https://example.test/pixel" onerror="alert(1)">',
      }),
    );
    expect(output).toContain("safe-markdown-pending");
    expect(output).toContain("&lt;img");
    expect(output).not.toContain("<img ");
    expect(output).not.toContain("<link ");
  });
  it("renders real lists, emphasis, quotes and inert code", () => {
    const output = html(
      "**判断**与*验证*\n\n- 第一项\n- 第二项\n\n1. 采样\n2. 对照\n\n> 保留证据\n\n`malloc`\n\n```sh\n<script>alert(1)</script>\n```",
    );
    for (const tag of ["strong", "em", "ul", "ol", "blockquote", "pre", "code"])
      expect(output).toContain(`<${tag}`);
    expect(output).toMatch(
      /(?:&lt;|&#x3C;)script(?:&gt;|>)alert\(1\)(?:&lt;|&#x3C;)\/script(?:&gt;|>)/,
    );
    expect(output).not.toContain("<script");
  });

  it("keeps headings below the surrounding section heading", () => {
    const output = html("# 结论\n\n## 验证\n\n### 观察");
    expect(output.match(/<h3>/g)).toHaveLength(3);
    expect(output).not.toMatch(/<h[12][\s>]/);
  });

  it("drops raw executable HTML and all automatic media elements", () => {
    const output = html(
      '<script>alert(1)</script>\n\n<iframe src="https://example.test/frame"></iframe>\n\n<svg onload="alert(1)"></svg>\n\n<style>body{background:url(https://example.test/pixel)}</style>\n\n<img src=x onerror="alert(1)">',
    );
    expect(output).not.toMatch(
      /<(script|iframe|svg|style|img|video|audio|object|embed|link)\b/i,
    );
    expect(output).not.toMatch(/\son(?:error|load)=/i);
  });

  it("never emits images or React image preloads, including reference images", () => {
    const output = html(
      "![tracking](https://example.test/pixel.png)\n\n![reference][pixel]\n\n[pixel]: https://example.test/another.png\n\n![inline](data:image/svg+xml;base64,PHN2Zz4=)",
    );
    expect(output).not.toMatch(/<(?:img|link)\b/i);
    expect(output).not.toContain("example.test");
    expect(output).not.toContain("data:image");
  });

  it("rejects executable, protocol-relative and ambiguous URL forms", () => {
    for (const value of [
      "javascript:alert(1)",
      "JaVaScRiPt:alert(1)",
      "data:text/html,hello",
      "vbscript:msgbox(1)",
      "file:///etc/passwd",
      "mailto:root@example.test",
      "//example.test/pixel",
      "\\\\example.test/pixel",
      "/\\example.test/pixel",
      "%2f%2fexample.test/pixel",
      "javascript%3Aalert(1)",
      "java\nscript:alert(1)",
      "https://example.test/%0aheader",
      " https://example.test/",
      "https:///example.test/",
      "https://user:secret@example.test/",
      "https://",
    ])
      expect(safeMarkdownUrl(value), value).toBeUndefined();
  });

  it("keeps bad links readable without an actionable anchor", () => {
    for (const source of [
      "[unsafe](javascript:alert%281%29)",
      "[unsafe](javascript&#58;alert%281%29)",
      "[unsafe](//example.test/path)",
      "[unsafe](data:text/html,hello)",
    ]) {
      const output = html(source);
      expect(output).toContain("unsafe");
      expect(output).not.toContain("<a ");
    }
  });

  it("allows valid http(s) links with isolated tab and referrer protection", () => {
    const output = html(
      "[官方说明](https://example.test/docs?q=cpu) [HTTP](http://example.test/docs)",
    );
    expect(output.match(/target="_blank"/g)).toHaveLength(2);
    expect(output.match(/rel="noopener noreferrer"/g)).toHaveLength(2);
    expect(output.match(/referrerPolicy="no-referrer"/gi)).toHaveLength(2);
    expect(output).toContain('href="https://example.test/docs?q=cpu"');
  });

  it("allows same-origin relative paths, queries and anchors without a new tab", () => {
    for (const value of [
      "/tasks/7",
      "../tasks/7",
      "?sort=created_at",
      "#evidence",
      "./cpu%20time",
    ]) {
      expect(safeMarkdownUrl(value)).toBe(value);
      const output = html(`[内部](${value})`);
      expect(output).toContain('href="' + value + '"');
      expect(output).not.toContain("target=");
    }
  });

  it("renders tables and gives code/table scroll regions keyboard access", () => {
    const output = html(
      "| 指标 | 值 |\n| --- | --- |\n| CPU | 20% |\n\n```sh\necho evidence\n```",
    );
    expect(output).toContain("<table");
    expect(output).toContain("<th>指标</th>");
    expect(output.match(/tabIndex="0"/gi)).toHaveLength(2);
    expect(output).toContain('aria-label="诊断数据表"');
    expect(output).toContain('aria-label="代码示例"');
  });

  it("bounds parser input and tells readers when content is clipped", () => {
    const output = html("a".repeat(32768) + "UNRENDERED_TAIL");
    expect(output).not.toContain("UNRENDERED_TAIL");
    const fallback = renderToStaticMarkup(
      createElement(SafeMarkdownContent, {
        children: "a".repeat(32768) + "UNRENDERED_TAIL",
      }),
    );
    expect(fallback).toContain("内容较长");
  });
  it("uses escaped plaintext when a real sanitizer DOM is unavailable", () => {
    const output = renderToStaticMarkup(
      createElement(SafeMarkdownContent, {
        children: '<img src=x onerror="alert(1)">',
      }),
    );
    expect(output).toContain('data-markdown-sanitizer="plaintext"');
    expect(output).toContain("&lt;img");
    expect(output).not.toContain("<img ");
  });
});
