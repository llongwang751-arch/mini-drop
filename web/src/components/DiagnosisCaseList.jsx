import { Alert, Button, Empty, List, Segmented, Space, Tag, Tooltip, Typography } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";

const { Text } = Typography;

export const CASE_FILTERS = [
  { label: "进行中", value: "active" },
  { label: "待处理", value: "attention" },
  { label: "已完成", value: "completed" },
  { label: "证据不足", value: "partial" },
  { label: "全部", value: "all" },
];

const STATUS_META = {
  CREATED: ["default", "准备中"],
  PLANNING: ["purple", "规划中"],
  COLLECTING: ["processing", "采集中"],
  ANALYZING: ["blue", "分析中"],
  WAITING_APPROVAL: ["gold", "待审批"],
  COMPLETED: ["green", "已完成"],
  PARTIAL: ["orange", "证据不足"],
  FAILED: ["red", "失败"],
  CANCELLED: ["default", "已取消"],
  UNKNOWN: ["default", "状态未知"],
};

const SOURCE_LABELS = {
  drop_insight_v2: "Drop Insight",
  cluster_diagnosis_v1: "集群诊断",
  legacy_rca: "任务 RCA",
};

export function caseMatchesFilter(item, filter) {
  const status = item.canonical_status || "UNKNOWN";
  if (filter === "all") return true;
  if (filter === "active") return ["CREATED", "PLANNING", "COLLECTING", "ANALYZING"].includes(status);
  if (filter === "attention") return status === "WAITING_APPROVAL" || item.status === "NEEDS_CLARIFICATION";
  if (filter === "completed") return status === "COMPLETED";
  if (filter === "partial") return status === "PARTIAL";
  return true;
}

function displayTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

export default function DiagnosisCaseList({
  cases = [],
  selectedKey,
  filter,
  onFilterChange,
  onSelect,
  onNew,
  onArchive,
  loading = false,
  loadError = "",
}) {
  const visible = cases.filter((item) => caseMatchesFilter(item, filter));
  let emptyDescription = "还没有诊断案例，描述一个问题开始调查";
  if (loadError) emptyDescription = loadError;
  else if (cases.length > 0 && visible.length === 0) emptyDescription = "当前筛选下没有案例";

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Button type="primary" block icon={<PlusOutlined />} onClick={onNew}>
        新建诊断
      </Button>
      <Segmented
        block
        size="small"
        options={CASE_FILTERS}
        value={filter}
        onChange={onFilterChange}
        aria-label="诊断案例状态筛选"
      />
      {loadError && cases.length > 0 && (
        <Alert type="warning" showIcon message={loadError} />
      )}
      <List
        className="diagnosis-case-list"
        loading={loading}
        dataSource={visible}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyDescription} /> }}
        renderItem={(item) => {
          const active = item.selection_key === selectedKey;
          const [statusColor, statusLabel] = STATUS_META[item.canonical_status] || STATUS_META.UNKNOWN;
          return (
            <List.Item className={active ? "diagnosis-case-row is-selected" : "diagnosis-case-row"}>
              <button
                type="button"
                className="diagnosis-case-select"
                aria-current={active ? "true" : undefined}
                onClick={() => onSelect(item)}
              >
                <Text ellipsis className="diagnosis-case-query">
                  {item.query || item.case_id || "未命名诊断"}
                </Text>
                <Space size={[4, 4]} wrap>
                  <Tag color={statusColor}>{statusLabel}</Tag>
                  <Tag>{SOURCE_LABELS[item.source] || item.source || "未知来源"}</Tag>
                </Space>
                <Text type="secondary" className="diagnosis-case-time">
                  {displayTime(item.updated_at || item.created_at)}
                </Text>
              </button>
              {onArchive && (
                <Tooltip title="归档并从列表隐藏，保留证据与审计">
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined />}
                    aria-label={`归档诊断：${item.query || item.case_id}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onArchive(item);
                    }}
                  />
                </Tooltip>
              )}
            </List.Item>
          );
        }}
      />
      {cases.length > 0 && (
        <Text type="secondary" style={{ fontSize: 11 }}>
          当前载入 {cases.length} 条；列表范围受服务端分页限制。
        </Text>
      )}
    </Space>
  );
}
