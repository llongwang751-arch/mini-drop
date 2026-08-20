import { Drawer, Tabs, Descriptions, Table, Timeline, Typography } from "antd";
import DiagnosisPathPanel from "./DiagnosisPathPanel";

const { Text } = Typography;

/**
 * 技术细节抽屉（方案 §4.2）：默认对话只展示结论；Task ID、TaskAttempt、
 * 证据哈希、Analyzer Job、内部状态机与原始 JSON 都收进这里，供导师 / SRE 审计。
 */
export default function TechnicalDetailDrawer({ open, onClose, detail, toolCalls, evidence, reports, events }) {
  const sourceContexts = (reports || [])
    .map((report) => ({
      reportId: report.report_id,
      context: report.verification?.source_context,
    }))
    .filter((item) => item.context);
  const sourceMappings = sourceContexts.flatMap((item) =>
    (item.context?.mappings || []).map((mapping) => ({
      ...mapping,
      report_id: item.reportId,
      row_key: `${item.reportId}:${mapping.file}:${mapping.line_start}:${mapping.symbol}`,
    }))
  );
  return (
    <Drawer title="技术细节" open={open} onClose={onClose} width={760}>
      <Tabs
        items={[
          {
            key: "session",
            label: "会话",
            children: detail ? (
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="diagnosis_id">{detail.id || detail.diagnosis_id}</Descriptions.Item>
                <Descriptions.Item label="状态">{detail.status}</Descriptions.Item>
                <Descriptions.Item label="version (CAS)">{detail.version}</Descriptions.Item>
                <Descriptions.Item label="目标">
                  <Text style={{ fontSize: 12 }}>{JSON.stringify(detail.target_json || detail.target)}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="时间范围">
                  <Text style={{ fontSize: 12 }}>{JSON.stringify(detail.time_range_json || detail.time_range)}</Text>
                </Descriptions.Item>
              </Descriptions>
            ) : (
              "无会话数据"
            ),
          },
          {
            key: "tools",
            label: `工具调用 (${(toolCalls || []).length})`,
            children: (
              <Table
                rowKey="tool_call_id"
                size="small"
                dataSource={toolCalls || []}
                pagination={false}
                columns={[
                  { title: "tool_call_id", dataIndex: "tool_call_id", ellipsis: true },
                  { title: "工具", dataIndex: "tool_name" },
                  { title: "状态", dataIndex: "status" },
                  { title: "策略", dataIndex: "policy_decision" },
                  { title: "task_id", dataIndex: "task_id", render: (v) => v || "-" },
                  {
                    title: "参数",
                    dataIndex: "arguments_json",
                    render: (v) => <Text style={{ fontSize: 11 }}>{JSON.stringify(v)}</Text>,
                  },
                ]}
              />
            ),
          },
          {
            key: "evidence",
            label: `证据 (${(evidence || []).length})`,
            children: (
              <Table
                rowKey="evidence_id"
                size="small"
                dataSource={evidence || []}
                pagination={false}
                columns={[
                  { title: "evidence_id", dataIndex: "evidence_id", ellipsis: true },
                  { title: "角色", dataIndex: "role" },
                  { title: "判定", render: (_, r) => r.classification?.decision || "-" },
                  { title: "Artifact", render: (_, r) => r.envelope?.source?.artifact_id || "-" },
                  { title: "SHA-256", render: (_, r) => (r.envelope?.source?.artifact_sha256 || "-").slice(0, 12) },
                  { title: "TaskAttempt", render: (_, r) => r.envelope?.source?.task_attempt_id || "-" },
                ]}
              />
            ),
          },
          {
            key: "source",
            label: `源码定位 (${sourceMappings.length})`,
            children: sourceContexts.length ? (
              <>
                <Text type="secondary">
                  仅扫描管理员配置的源码目录；源码行是热点复核线索，需与运行时证据和反证结果共同确认。
                </Text>
                <Table
                  rowKey="row_key"
                  size="small"
                  style={{ marginTop: 12 }}
                  dataSource={sourceMappings}
                  pagination={false}
                  locale={{
                    emptyText: `已扫描 ${sourceContexts.reduce((sum, item) => sum + (item.context?.scanned_files || 0), 0)} 个源码文件，当前热点符号尚未匹配到源码定义`,
                  }}
                  columns={[
                    { title: "符号", dataIndex: "symbol", ellipsis: true },
                    { title: "语言", dataIndex: "language", width: 80 },
                    { title: "文件", dataIndex: "file", ellipsis: true },
                    { title: "行号", render: (_, row) => `${row.line_start}-${row.line_end}` },
                    { title: "复核信号", render: (_, row) => (row.review_signals || []).join("、") || "-" },
                  ]}
                />
              </>
            ) : (
              <Text type="secondary">
                当前报告尚无源码映射。请配置 MINI_DROP_SOURCE_ROOTS，并使用包含函数符号的 perf、py-spy、pprof 或 async-profiler 证据。
              </Text>
            ),
          },
          {
            key: "path",
            label: `决策路径 (${(events || []).length})`,
            children: <DiagnosisPathPanel events={events} />,
          },
          {
            key: "events",
            label: `事件 (${(events || []).length})`,
            children: (
              <Timeline
                items={(events || []).map((e) => ({
                  children: (
                    <div>
                      <Text strong>{e.event_type}</Text>
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {e.actor} · {e.occurred_at}
                        </Text>
                      </div>
                    </div>
                  ),
                }))}
              />
            ),
          },
          {
            key: "reports",
            label: `报告 (${(reports || []).length})`,
            children: (
              <Descriptions column={1} size="small" bordered>
                {(reports || []).slice(0, 3).map((r) => (
                  <Descriptions.Item key={r.report_id} label={r.report_id}>
                    <Text style={{ fontSize: 12 }}>{JSON.stringify(r, null, 2)}</Text>
                  </Descriptions.Item>
                ))}
              </Descriptions>
            ),
          },
        ]}
      />
    </Drawer>
  );
}
