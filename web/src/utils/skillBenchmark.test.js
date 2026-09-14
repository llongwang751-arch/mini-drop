import { describe, expect, it } from "vitest";
import report from "../../public/report-assets/skill-evolution/benchmark-report.json";
import { validateSkillBenchmark } from "./skillBenchmark";

describe("offline report boundary", () => {
  it("accepts the generated production report", () => expect(validateSkillBenchmark(report)).toBe(report));
  it.each([null, {}, { schema: report.schema }, { ...report, skill_enabled: null },
    { ...report, skill_enabled: { ...report.skill_enabled, accuracy: "1" } },
    { ...report, baseline_no_skill: { ...report.baseline_no_skill, correct: 999999 } },
  ])("rejects incomplete or impossible data instead of fabricating scores", (invalid) => {
    expect(() => validateSkillBenchmark(invalid)).toThrow();
  });
});
