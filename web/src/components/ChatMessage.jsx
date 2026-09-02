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
    <div className={`chat-message chat-message-${role}`}>
      <div className="chat-message-avatar">
        {avatar || (isUser ? <UserOutlined /> : <RobotOutlined />)}
      </div>
      <div className="chat-message-body">
        <Text type="secondary" className="chat-message-role">
          {isUser ? "我" : "AI 诊断助手"}
        </Text>
        <div className="chat-message-content">{children}</div>
      </div>
    </div>
  );
}
