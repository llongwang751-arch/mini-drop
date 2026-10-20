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
  // USE_PROFILES overrides ALLOWED_TAGS. Apply the HTML-only profile first,
  // then the stricter allowlist; neither intermediate string enters the DOM.
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
