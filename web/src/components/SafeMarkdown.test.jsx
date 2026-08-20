import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import SafeMarkdown from "./SafeMarkdown";

describe("SafeMarkdown", () => {
  it("renders markdown emphasis and lists", () => {
    render(<SafeMarkdown>{"**bold**\n\n- item"}</SafeMarkdown>);
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText("item")).toBeInTheDocument();
  });

  it("strips <script> blocks from model output", () => {
    const { container } = render(
      <SafeMarkdown>
        {"<script>window.__xss = 1</script>\n\nsafe text"}
      </SafeMarkdown>
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).not.toContain("window.__xss");
  });

  it("strips inline event handlers from raw HTML", () => {
    const { container } = render(
      <SafeMarkdown>
        {"<img src=x onerror=\"alert(1)\">\n\nsafe"}
      </SafeMarkdown>
    );
    // DOMPurify either drops the tag entirely or strips the onerror handler;
    // either way no executable attribute survives.
    const img = container.querySelector("img");
    expect(img?.hasAttribute("onerror") ?? false).toBe(false);
    expect(container.textContent).toContain("safe");
  });

  it("renders empty children safely", () => {
    const { container } = render(<SafeMarkdown>{""}</SafeMarkdown>);
    expect(container.textContent).toBe("");
  });
});
