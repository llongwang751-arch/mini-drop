/** 教程是随源码发布的 Markdown；保留语义标题，原始 HTML 不进入渲染树。 */
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import type { Element, Root, RootContent } from "hast";

export interface BookSection {
  id: string;
  title: string;
}

const processor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype)
  .freeze();

export function nodeText(node: RootContent): string {
  return node.type === "text"
    ? node.value
    : "children" in node
      ? node.children.map(nodeText).join("")
      : "";
}

export function parseBookMarkdown(source: string): {
  tree: Root;
  sections: BookSection[];
} {
  const tree = processor.runSync(processor.parse(source)) as Root;
  const sections: BookSection[] = [];
  for (const node of tree.children) {
    if (node.type !== "element" || node.tagName !== "h2") continue;
    const id = `section-${sections.length + 1}`;
    (node as Element).properties.id = id;
    sections.push({ id, title: nodeText(node) });
  }
  return { tree, sections };
}
