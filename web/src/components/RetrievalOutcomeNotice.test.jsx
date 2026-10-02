import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import RetrievalOutcomeNotice from "./RetrievalOutcomeNotice";

afterEach(cleanup);
describe("no-answer retrieval has no diagnostic health meaning", () => {
  it.each([
    ["HEALTHY", "未找到相关知识"],
    ["DEGRADED", "检索已降级，本次未找到相关知识"],
  ])("shows %s retrieval without claiming business health", (health, message) => {
    render(<RetrievalOutcomeNotice trace={{ outcome: "NO_RELEVANT_KNOWLEDGE", health_scope: "RETRIEVAL_ONLY", health }} />);
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByText(/这不表示业务正常，也不是故障证据/)).toBeInTheDocument();
  });
  it("keeps matched and old unknown traces outside the no-answer contract", () => {
    const { container, rerender } = render(<RetrievalOutcomeNotice trace={{ outcome: "MATCHED", health_scope: "RETRIEVAL_ONLY" }} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<RetrievalOutcomeNotice trace={{ matches: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
