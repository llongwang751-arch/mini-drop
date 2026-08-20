import { Button, List, Tag, Typography, Empty, Tooltip } from "antd";
import { PlusOutlined, DeleteOutlined } from "@ant-design/icons";

const { Text } = Typography;

const STATUS_COLORS = {
  COMPLETED: "green",
  RUNNING: "blue",
  COLLECTING_EVIDENCE: "geekblue",
  NEEDS_CLARIFICATION: "orange",
  INSUFFICIENT_EVIDENCE: "gold",
  FAILED: "red",
  CANCELLED: "default",
};

/**
 * 会话列表：左侧面板。列出 v2 诊断会话，支持新建、选择与删除（软归档）。
 * 会话标题取用户 query；状态用颜色标签区分。
 */
export default function SessionList({ sessions, selectedId, onSelect, onNew, onDelete }) {
  return (
    <div>
      <Button
        type="primary"
        block
        icon={<PlusOutlined />}
        onClick={onNew}
        style={{ marginBottom: 12 }}
      >
        新对话
      </Button>
      <List
        dataSource={sessions || []}
        locale={{ emptyText: <Empty description="暂无会话，描述一个问题开始" /> }}
        renderItem={(item) => {
          const active = item.diagnosis_id === selectedId;
          return (
            <List.Item
              onClick={() => onSelect(item.diagnosis_id)}
              style={{
                cursor: "pointer",
                padding: "10px 12px",
                borderRadius: 8,
                background: active ? "rgba(22,119,255,0.12)" : "transparent",
                border: active ? "1px solid rgba(22,119,255,0.5)" : "1px solid transparent",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <Text ellipsis style={{ display: "block" }}>
                    {item.query || item.id}
                  </Text>
                  <Tag color={STATUS_COLORS[item.status] || "default"} style={{ marginTop: 4 }}>
                    {item.status}
                  </Tag>
                </div>
                {onDelete && (
                  <Tooltip title="删除（软归档，保留证据与审计）">
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(item.diagnosis_id, item.query || item.id);
                      }}
                    />
                  </Tooltip>
                )}
              </div>
            </List.Item>
          );
        }}
      />
    </div>
  );
}
