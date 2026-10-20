/**
 * 富文本渲染的最终安全边界：先生成受限 HTML，再用 DOMPurify 白名单清理。
 * 清理器不可用时回退纯文本；不允许模型文本创建脚本、媒体或表单资源。
 */
import { useMemo } from "react";
import createDOMPurify from "dompurify";
import { diagnosticMarkdownHTML, diagnosticTags } from "./markdown-html";
import {
  MAX_MARKDOWN_CHARACTERS,
  safeMarkdownUrl,
} from "./safe-markdown-policy";

const attributes = [
  "href",
  "title",
  "target",
  "rel",
  "referrerpolicy",
  "class",
  "role",
  "tabindex",
  "aria-label",
];
const forbidden = [
  "style",
  "script",
  "iframe",
  "svg",
  "math",
  "img",
  "video",
  "audio",
  "object",
  "embed",
  "link",
  "template",
  "form",
  "input",
];
const purifier =
  typeof window === "undefined" ? undefined : createDOMPurify(window);
if (purifier?.isSupported)
  purifier.addHook("uponSanitizeAttribute", (_node, data) => {
    if (data.attrName === "href" && !safeMarkdownUrl(data.attrValue))
      data.keepAttr = false;
    if (data.attrName === "target" && data.attrValue !== "_blank")
      data.keepAttr = false;
  });

export function sanitizeDiagnosticHTML(html: string): string | undefined {
  if (!purifier?.isSupported) return;
  // DOMPurify 的 USE_PROFILES 会覆盖 ALLOWED_TAGS，因此先限制为 HTML，再应用更窄的白名单。
  // 两步清理的中间字符串都不进入 DOM，只有最终安全片段可被渲染。
  const profile = purifier.sanitize(html, {
    USE_PROFILES: { html: true },
    ALLOWED_ATTR: attributes,
    ADD_ATTR: ["target", "referrerpolicy"],
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: forbidden,
    FORBID_ATTR: ["style", "src", "srcset"],
    RETURN_TRUSTED_TYPE: false,
  });
  return purifier.sanitize(profile, {
    ALLOWED_TAGS: diagnosticTags,
    ALLOWED_ATTR: attributes,
    ALLOW_ARIA_ATTR: false,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: forbidden,
    FORBID_ATTR: ["style", "src", "srcset"],
    RETURN_TRUSTED_TYPE: false,
  });
}

export default function SafeMarkdownContent({
  children,
}: {
  children: string;
}) {
  const html = useMemo(
    () => sanitizeDiagnosticHTML(diagnosticMarkdownHTML(children)),
    [children],
  );
  return (
    <div
      className="safe-markdown"
      data-markdown-sanitizer={html === undefined ? "plaintext" : "dompurify"}
    >
      {html === undefined ? (
        <p>{children.slice(0, MAX_MARKDOWN_CHARACTERS)}</p>
      ) : (
        <div
          className="safe-markdown-body"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )}
      {children.length > MAX_MARKDOWN_CHARACTERS && (
        <p className="safe-markdown-limit">
          内容较长，已显示前 32,768 个字符。
        </p>
      )}
    </div>
  );
}
