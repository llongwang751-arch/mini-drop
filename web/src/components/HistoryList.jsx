import { Button, Empty, List, Tag, Tooltip, Typography } from "antd";
import { DeleteOutlined } from "@ant-design/icons";

const { Text } = Typography;

const STATUS_COLORS = {
  COMPLETED: "green",
  PARTIAL: "orange",
  FAILED: "red",
  RUNNING: "blue",
};

/**
 * 只读历史列表：通过统一 /diagnostic-cases 展示 v1 集群诊断 / v2 / legacy 全部诊断。
 * 点击打开只读详情（不能继续对话）。
 */
export default function HistoryList({ cases, onOpen, onDelete }) {
  return (
    <List
      dataSource={cases || []}
      locale={{ emptyText: <Empty description="暂无历史诊断" /> }}
      renderItem={(item) => (
        <List.Item
          onClick={() => onOpen(item)}
          style={{ cursor: "pointer", padding: "10px 12px", borderRadius: 8 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <Text ellipsis style={{ display: "block" }}>
                {item.query || item.case_id || item.id}
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {item.source}
              </Text>
              <Tag color={STATUS_COLORS[item.canonical_status] || "default"} style={{ marginLeft: 8 }}>
                {item.canonical_status}
              </Tag>
            </div>
            {onDelete && (
              <Tooltip title="从诊断历史中删除（原始证据和审计保留）">
                <Button
                  type="text"
                  size="small"
                  danger
                  aria-label="删除历史诊断"
                  icon={<DeleteOutlined />}
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete(item);
                  }}
                />
              </Tooltip>
            )}
          </div>
        </List.Item>
      )}
    />
  );
}
