import { Alert } from "antd";

export default function RetrievalOutcomeNotice({ trace }) {
  if (trace?.outcome !== "NO_RELEVANT_KNOWLEDGE" || trace?.health_scope !== "RETRIEVAL_ONLY") return null;
  return (
    <Alert
      showIcon
      type={trace.health === "DEGRADED" ? "warning" : "info"}
      message={trace.health === "DEGRADED" ? "检索已降级，本次未找到相关知识" : "未找到相关知识"}
      description="知识库没有提供可用答案；这不表示业务正常，也不是故障证据。请补充实际观测，或继续允许范围内的取证。"
    />
  );
}
