import { Typography } from "antd";
import { UserOutlined, RobotOutlined } from "@ant-design/icons";

const { Text } = Typography;

/**
 * 对话气泡：用户提问（右侧高亮）或 AI 回复。AI 消息内含由
 * PlannerBlock / ToolCallCard / EvidenceCard / ConclusionCard 组成的块。
 */
export default function ChatMessage({ role, children, avatar }) {
  const isUser = role === "user";
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
      <div
        style={{
          width: 30,
          height: 30,
          borderRadius: "50%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: isUser ? "rgba(22,119,255,0.18)" : "rgba(114,46,209,0.18)",
          color: isUser ? "#1677ff" : "#722ed1",
          flexShrink: 0,
        }}
      >
        {avatar || (isUser ? <UserOutlined /> : <RobotOutlined />)}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {isUser ? "我" : "AI 诊断助手"}
        </Text>
        <div style={{ marginTop: 4 }}>{children}</div>
      </div>
    </div>
  );
}
