/**
 * 安全富文本的轻量入口：解析依赖按需载入，等待时只显示受长度限制的纯文本。
 * 模型或规则输出都是不可信内容，不能直接当 HTML 注入页面。
 */
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
