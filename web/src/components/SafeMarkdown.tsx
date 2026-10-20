import { lazy, Suspense } from "react";
import { MAX_MARKDOWN_CHARACTERS } from "./safe-markdown-policy";
import "./SafeMarkdown.css";

const Content = lazy(() => import("./SafeMarkdownContent"));

export function SafeMarkdown({ children }: { children: string }) {
  return (
    <Suspense
      fallback={
        <div className="safe-markdown safe-markdown-pending">
          <p>{children.slice(0, MAX_MARKDOWN_CHARACTERS)}</p>
        </div>
      }
    >
      <Content>{children}</Content>
    </Suspense>
  );
}
