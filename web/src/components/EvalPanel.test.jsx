import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import EvalPanel from "./EvalPanel";

vi.mock("../api/client", () => ({ listDiagnosticSkills: vi.fn().mockResolvedValue([]) }));
vi.mock("./LatsReplayPanel", () => ({
  default: ({ onOpenDiagnosis }) => (
    <section aria-label="测试冻结回放">
      <button type="button" onClick={() => onOpenDiagnosis?.("replay-1")}>打开测试回放</button>
    </section>
  ),
}));
vi.mock("./FaultPlazaPanel", () => ({
  default: () => <section aria-label="测试故障广场">故障广场内容</section>,
}));
vi.mock("./SkillABPanel", () => ({ default: () => null }));
vi.mock("./SkillEvolutionPanel", () => ({ default: () => null }));

describe("EvalPanel replay entry", () => {
  it("places the real Fault Plaza before frozen replay and forwards diagnosis navigation", () => {
    const onOpenDiagnosis = vi.fn();
    render(<EvalPanel onOpenDiagnosis={onOpenDiagnosis} />);

    fireEvent.click(screen.getByText("故障广场"));
    const replay = screen.getByLabelText("测试冻结回放");
    const faultPlaza = screen.getByLabelText("测试故障广场");
    expect(faultPlaza.compareDocumentPosition(replay) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "打开测试回放" }));
    expect(onOpenDiagnosis).toHaveBeenCalledWith("replay-1");
  });
});
