import { useMemo } from "react";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeRaw from "rehype-raw";
import rehypeStringify from "rehype-stringify";
import DOMPurify from "dompurify";

/**
 * SafeMarkdown renders AI / Analyzer produced Markdown without ever treating
 * the model output as trusted HTML.
 *
 * Pipeline: markdown -> GFM -> raw HTML parse -> HTML string -> DOMPurify
 * sanitize -> dangerouslySetInnerHTML. rehype-raw intentionally turns inline
 * HTML into real nodes so it can be stripped by DOMPurify rather than leaked
 * as an escaped string that still carries an XSS payload in some UIs.
 */
export default function SafeMarkdown({ children = "", className }) {
  const html = useMemo(() => {
    const raw = unified()
      .use(remarkParse)
      .use(remarkGfm)
      .use(remarkRehype)
      .use(rehypeRaw)
      .use(rehypeStringify)
      .processSync(String(children ?? ""))
      .toString();
    return DOMPurify.sanitize(raw, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ["style", "iframe", "form", "input", "button", "textarea"],
    });
  }, [children]);

  return (
    <div
      className={className}
      // Only the DOMPurify-cleaned markup ever reaches the DOM.
      dangerouslySetInnerHTML={{ __html: html }}
      dir="auto"
    />
  );
}
