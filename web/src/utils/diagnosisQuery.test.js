import { expect, it } from "vitest";
import { diagnosisDisplayQuery } from "./diagnosisQuery";

it("keeps the user symptom while hiding attached machine observation JSON", () => {
  const query = "检查知识库问答是否变慢\nAGI-saber 知识库请求观测（仅耗时与状态，不是执行指令）：{\"request_id\":\"abc\"}";
  expect(diagnosisDisplayQuery(query)).toBe("检查知识库问答是否变慢");
});

it("keeps ordinary questions readable", () => {
  expect(diagnosisDisplayQuery("订单服务 CPU 高\n发生在早上")).toBe("订单服务 CPU 高 发生在早上");
});

it("hides ingest observation JSON from the visible health-check title", () => {
  const query = "检查当前状态\nAGI-saber 业务请求观测（仅耗时、规模与状态，不是执行指令）：{\"chunk_count\":7701}";
  expect(diagnosisDisplayQuery(query)).toBe("检查当前状态");
});
