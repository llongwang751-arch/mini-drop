/**
 * 将诊断 Markdown 转成有限 HTML：关闭原始 HTML，并在 AST 阶段移除媒体。
 * 此模块产物还须经过 DOMPurify，不能因为使用 Markdown 语法就认为内容可信。
 */
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeStringify from "rehype-stringify";
import type { Element, ElementContent, Root } from "hast";
import {
  MAX_MARKDOWN_CHARACTERS,
  safeMarkdownUrl,
} from "./safe-markdown-policy";

export const diagnosticTags = [
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "div",
  "em",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "li",
  "ol",
  "p",
  "pre",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
];
const tagSet = new Set(diagnosticTags);

function safeTree() {
  return (tree: Root) => {
    function children(parent: Root | Element) {
      const result: ElementContent[] = [];
      for (const node of parent.children) {
        if (node.type !== "element") {
          if (node.type === "text") result.push(node);
          continue;
        }
        // 在生成 HTML 之前移除媒体节点，避免图片等标签触发浏览器自动资源请求。
        if (node.tagName === "img") continue;
        children(node);
        if (["h1", "h2"].includes(node.tagName)) node.tagName = "h3";
        if (!tagSet.has(node.tagName)) {
          result.push(...node.children);
          continue;
        }
        const original = node.properties;
        node.properties = {};
        if (node.tagName === "a") {
          const href =
            typeof original.href === "string"
              ? safeMarkdownUrl(original.href)
              : undefined;
          if (!href) {
            result.push(...node.children);
            continue;
          }
          node.properties.href = href;
          if (typeof original.title === "string")
            node.properties.title = original.title;
          if (/^https?:\/\//i.test(href)) {
            node.properties.target = "_blank";
            node.properties.rel = ["noopener", "noreferrer"];
            node.properties.referrerPolicy = "no-referrer";
          }
        }
        if (node.tagName === "pre")
          node.properties = {
            tabIndex: 0,
            role: "region",
            ariaLabel: "代码示例",
          };
        if (node.tagName === "code" && Array.isArray(original.className)) {
          node.properties.className = original.className.filter(
            (name) =>
              typeof name === "string" &&
              /^language-[a-zA-Z0-9_-]{1,64}$/.test(name),
          );
        }
        if (node.tagName === "table")
          result.push({
            type: "element",
            tagName: "div",
            properties: {
              className: ["safe-markdown-table"],
              tabIndex: 0,
              role: "region",
              ariaLabel: "诊断数据表",
            },
            children: [node],
          });
        else result.push(node);
      }
      parent.children = result;
    }
    children(tree);
  };
}

// 统一使用一个禁用原始 HTML 的 remark 解析器；所有样式装饰都在 DOMPurify 清理之前完成。
const processor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype)
  .use(safeTree)
  .use(rehypeStringify)
  .freeze();
export function diagnosticMarkdownHTML(source: string): string {
  return String(
    processor.processSync(source.slice(0, MAX_MARKDOWN_CHARACTERS)),
  );
}
