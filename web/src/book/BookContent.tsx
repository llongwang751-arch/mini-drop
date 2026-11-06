import { createElement, useState } from "react";
import type { ReactNode } from "react";
import type { Root, RootContent } from "hast";
import { safeMarkdownUrl } from "../components/safe-markdown-policy";
import { nodeText } from "./markdown";

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [feedback, setFeedback] = useState("");
  return (
    <div className="book-code-block">
      <div className="book-code-header">
        <span>{language || "代码示例"}</span>
        <button
          type="button"
          className="book-copy-button"
          aria-label="复制代码"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(code);
              setFeedback("已复制");
            } catch {
              setFeedback("请选中代码后复制");
            }
          }}
        >
          复制
        </button>
        <span role="status" className="book-copy-status">
          {feedback}
        </span>
      </div>
      <pre tabIndex={0} aria-label="代码示例">
        <code>{code}</code>
      </pre>
    </div>
  );
}

const tags = new Set([
  "p",
  "h2",
  "h3",
  "h4",
  "strong",
  "em",
  "del",
  "a",
  "ul",
  "ol",
  "li",
  "blockquote",
  "pre",
  "code",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "br",
  "hr",
]);

function renderNode(node: RootContent, key: number): ReactNode {
  if (node.type === "text") return node.value;
  if (node.type !== "element" || !tags.has(node.tagName)) return null;
  const children = node.children.map(renderNode);
  if (node.tagName === "pre") {
    const code = node.children.find(
      (child) => child.type === "element" && child.tagName === "code",
    );
    const language =
      code?.type === "element" && Array.isArray(code.properties.className)
        ? String(code.properties.className[0] || "").replace(/^language-/, "")
        : "";
    return <CodeBlock key={key} code={nodeText(node)} language={language} />;
  }
  if (node.tagName === "a") {
    const href = safeMarkdownUrl(String(node.properties.href || ""));
    if (!href) return <span key={key}>{children}</span>;
    return (
      <a
        key={key}
        href={href}
        {...(/^https?:\/\//.test(href)
          ? {
              target: "_blank",
              rel: "noopener noreferrer",
              referrerPolicy: "no-referrer",
            }
          : {})}
      >
        {children}
      </a>
    );
  }
  if (node.tagName === "table") {
    return (
      <div
        className="book-table-scroll"
        key={key}
        tabIndex={0}
        role="region"
        aria-label="教程对照表"
      >
        <table>{children}</table>
      </div>
    );
  }
  return createElement(
    node.tagName,
    { key, ...(node.tagName === "h2" ? { id: node.properties.id } : {}) },
    ...children,
  );
}

export default function BookContent({ tree }: { tree: Root }) {
  return <>{tree.children.map(renderNode)}</>;
}
