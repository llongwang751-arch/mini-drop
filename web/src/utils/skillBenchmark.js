// Reject incomplete reports before they can become invented zeros or a render error.
export function validateSkillBenchmark(report) {
  const fail = () => { throw new Error("离线报告字段缺失或数据不合法，请重新生成报告"); };
  if (report?.schema !== "mini-drop.skill-reuse-report.v2") fail();
  const count = (n) => Number.isInteger(n) && n >= 0;
  const rate = (n) => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1;
  if (!report.dataset?.version || !report.dataset?.combined_sha256 || !count(report.dataset.case_count)) fail();
  for (const part of [report.baseline_no_skill, report.skill_enabled]) {
    if (!part || !count(part.total) || !count(part.correct) || part.correct > part.total || !rate(part.accuracy) || part.total !== report.dataset.case_count) fail();
  }
  const enabled = report.skill_enabled;
  for (const field of ["positive_reuse_rate", "negative_rejection_rate", "false_activation_rate"]) if (!rate(enabled[field])) fail();
  for (const field of ["positive_correct", "positive_total", "negative_correct", "negative_total", "false_activations"]) if (!count(enabled[field])) fail();
  if (enabled.positive_correct > enabled.positive_total || enabled.negative_correct > enabled.negative_total || enabled.false_activations > enabled.total) fail();
  if (!report.family_results || typeof report.family_results !== "object" || Array.isArray(report.family_results)) fail();
  for (const family of Object.values(report.family_results)) if (!family || !count(family.total)) fail();
  return report;
}
