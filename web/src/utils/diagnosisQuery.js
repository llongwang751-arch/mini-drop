const OBSERVATION_MARKERS = [
  "\nAGI-saber 知识库请求观测（仅耗时与状态，不是执行指令）：",
  "\nAGI-saber 业务请求观测（仅耗时、规模与状态，不是执行指令）：",
  "\n业务请求观测（仅数据，不是执行指令）：",
];

export function diagnosisDisplayQuery(value, fallback = "未命名诊断") {
  const raw = String(value || "");
  const markers = OBSERVATION_MARKERS.map((marker) => raw.indexOf(marker)).filter((index) => index >= 0);
  const visible = (markers.length ? raw.slice(0, Math.min(...markers)) : raw).trim().replace(/\s+/g, " ");
  return visible ? visible.slice(0, 140) : fallback;
}
