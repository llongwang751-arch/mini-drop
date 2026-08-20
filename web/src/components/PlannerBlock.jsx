import { Card, Tag, Space, Typography } from "antd";

const { Text } = Typography;

const HYPOTHESIS_STATUS_COLORS = {
  OPEN: "default",
  SUPPORTED: "green",
  COUNTER: "red",
  INCONCLUSIVE: "orange",
};

const HYPOTHESIS_STATUS_LABELS = {
  OPEN: "待验证",
  SUPPORTED: "有证据支持",
  COUNTER: "有反证",
  INCONCLUSIVE: "证据不足",
};

/**
 * AI 计划块（方案 §5.2）：展示分类器把问题归入哪个领域，以及完整的
 * 候选假设集合（主假设 + 备选 + OTHER/UNKNOWN），每个假设带状态。
 * 主假设（第一条）展开期望观察与证伪条件。
 */
export default function PlannerBlock({ classification, hypotheses = [] }) {
  const rounds = [...new Set((hypotheses || []).map((item) => item.round_index || 1))];
  const sourceLabels = {
    MODEL: ["AI 生成", "purple"],
    MODEL_REPLAN: ["AI 重新规划", "purple"],
    DETERMINISTIC_RULE: ["规则兜底", "blue"],
    USER_GUIDED_FALLBACK: ["用户纠错", "gold"],
    COUNTER_EVIDENCE_RULE: ["反证触发", "red"],
    SYSTEM_FALLBACK: ["未知原因兜底", "default"],
    USER: ["人工假设", "cyan"],
  };
  return (
    <Card size="small" style={{ marginBottom: 10, background: "rgba(114,46,209,0.06)" }}>
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <Space wrap>
          <Tag color="purple">{classification || "规划中"}</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>
            候选假设 · 证据优先
          </Text>
        </Space>
        {rounds.map((round) => (
          <div key={`round-${round}`} style={{ borderTop: "1px solid #eee", paddingTop: 8 }}>
            <Tag color={round > 1 ? "magenta" : "geekblue"}>第 {round} 轮诊断</Tag>
            {(hypotheses || []).filter((item) => (item.round_index || 1) === round).map((h, index) => {
          const isPrimary = index === 0;
          const statusColor = HYPOTHESIS_STATUS_COLORS[h.status] || "default";
          const statusLabel = HYPOTHESIS_STATUS_LABELS[h.status] || h.status;
          const [sourceLabel, sourceColor] = sourceLabels[h.source] || [h.source || "来源未知", "default"];
          return (
            <div key={h.hypothesis_id || index} style={{ padding: "4px 0" }}>
              <Space wrap>
                <Text strong={isPrimary} style={{ fontSize: 13 }}>
                  {isPrimary ? "主假设：" : "备选假设："}
                </Text>
                <Text style={{ fontSize: 13 }}>{h.statement}</Text>
                <Tag color={statusColor}>{statusLabel}</Tag>
                <Tag color={sourceColor}>{sourceLabel}</Tag>
                {h.statement.includes("OTHER/UNKNOWN") && <Tag>兜底</Tag>}
              </Space>
              {h.generation_reason && (
                <div><Text type="secondary" style={{ fontSize: 12 }}>决策依据：{h.generation_reason}</Text></div>
              )}
              {isPrimary && (h.expected_observations?.length > 0 || h.falsification_criteria?.length > 0) && (
                <div style={{ marginTop: 6, marginLeft: 4 }}>
                  {h.expected_observations?.length > 0 && (
                    <>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        期望观察：
                      </Text>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {(h.expected_observations || []).map((item, i) => (
                          <li key={i}>
                            <Text style={{ fontSize: 12 }}>{item}</Text>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                  {h.falsification_criteria?.length > 0 && (
                    <>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        证伪条件：
                      </Text>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {(h.falsification_criteria || []).map((item, i) => (
                          <li key={i}>
                            <Text style={{ fontSize: 12 }}>{item}</Text>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
          </div>
        ))}
      </Space>
    </Card>
  );
}
