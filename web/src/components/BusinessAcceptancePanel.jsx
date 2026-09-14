import { useEffect, useState } from "react";
import { Alert, Button, Card, Descriptions, Empty, Space, Table, Tag, Typography } from "antd";
import { getBusinessAcceptance } from "../api/client";

const OUTCOMES = {
  IMPROVEMENT_VERIFIED: ["业务指标改善已验证", "green"],
  DEGRADED_AVAILABLE: ["仅降级可用 · 未恢复完整能力", "gold"],
  REJECTED: ["业务复测未通过", "red"],
  INCOMPARABLE: ["不可比较 · 证据不足", "orange"],
};
const STAGES = { queue: "查询排队", retrieval: "全文检索", rerank: "候选重排", generation: "回答阶段", local_search: "本地词法检索", answer_composition: "证据去重与摘录" };
const ms = (value) => Number.isFinite(value) ? (value > 0 && value < 0.1 ? "< 0.1 ms" : `${value.toFixed(1)} ms`) : "未测量";
const pct = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "未测量";

export default function BusinessAcceptancePanel() {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setError(""); setReport(null);
    getBusinessAcceptance().then((value) => {
      if (!value || !Array.isArray(value.cases) || !["AVAILABLE", "NOT_RUN", "INVALID"].includes(value.status)) throw new Error("验收响应格式不完整");
      if (active) setReport(value);
    }).catch((err) => { if (active) setError(err.message || "验收报告读取失败"); });
    return () => { active = false; };
  }, [reload]);
  return <Space direction="vertical" size={18} style={{ width: "100%" }}>
    <Card title="知识库查询为什么变慢？" extra={<Button onClick={() => setReload((x) => x + 1)}>刷新记录</Button>}>
      <Typography.Paragraph>员工查询制度文档时，需要及时得到答案和出处。这里从真实 HTTP 查询出发，对比正常、异常和变更后三个窗口，检查延迟、成功率和引用质量。</Typography.Paragraph>
      <Alert type="info" showIcon message="业务指标与 AI 根因分别验收" description="每条记录说明代码来源、运行环境和测试范围。业务指标改善不自动证明 AI 已定位根因；实际办公助手接入与本地样例分别标注，模型和 Milvus 未执行的部分不计入成绩。" />
    </Card>
    {error && <Alert type="error" showIcon message="无法确认验收状态" description={error} action={<Button onClick={() => setReload((x) => x + 1)}>重试</Button>} />}
    {!error && !report && <Card loading aria-label="正在读取业务验收" />}
    {report?.status === "NOT_RUN" && <Empty description="尚无业务验收记录，不能推断通过" />}
    {report?.status === "INVALID" && <Alert type="error" showIcon message={report.message || "验收报告无效"} />}
    {report?.status === "AVAILABLE" && <>
      <Typography.Text type="secondary">最近测量：{report.finished_at} · 版本：{report.revision}</Typography.Text>
      {report.cases.map((item) => {
        const comparison = item.comparison || {};
        const [label, color] = OUTCOMES[comparison.outcome] || ["未知结果", "default"];
        const summaries = comparison.summaries || {};
        const rows = ["baseline", "fault", "after"].map((key, index) => ({ key, name: ["正常基线", "故障窗口", "变更后复测"][index], ...summaries[key] }));
        return <Card key={item.scenario_id} title={`${item.scenario_id} · ${item.title}`}>
          <Space wrap style={{ marginBottom: 14 }}><Tag color={color}>{label}</Tag>
            <Tag>{item.source_kind === "ACTUAL_RAG_ENGINE" ? `实际 RAG 引擎 · ${item.environment}` : "本地 HTTP 样例"}</Tag>
            <Tag>{item.diagnosis ? "AI 诊断已执行 · 根因另看报告" : "AI 根因验收：未执行"}</Tag></Space>
          {item.background && <Typography.Paragraph>{item.background}</Typography.Paragraph>}
          {item.boundary && <Typography.Paragraph type="secondary">验收范围：{item.boundary}</Typography.Paragraph>}
          {item.diagnosis && <Typography.Paragraph>
            <a href={`/ai-diagnosis?case=${encodeURIComponent(item.diagnosis.diagnosis_id)}`}>查看这次 AI 诊断与采集证据</a>
            {` · ${item.diagnosis.task_count} 个任务 / ${item.diagnosis.evidence_count} 条证据 / ${item.diagnosis.report_count} 份报告`}
          </Typography.Paragraph>}
          <Table size="small" pagination={false} dataSource={rows} scroll={{ x: 600 }} columns={[
            { title: "窗口", dataIndex: "name" }, { title: "请求数", dataIndex: "request_count" },
            { title: "端到端 P95", dataIndex: "p95_ms", render: ms },
            { title: "成功率", dataIndex: "success_rate", render: pct },
            { title: "引用质量", dataIndex: "quality_rate", render: pct },
            { title: "降级请求", dataIndex: "degraded_count" },
          ]} />
          <Typography.Paragraph style={{ marginTop: 16 }}><strong>实际变更：</strong>{comparison.change_summary}</Typography.Paragraph>
          <Descriptions title="故障期间已测阶段 P95" size="small" column={{ xs: 1, sm: 2, lg: 4 }} items={Object.entries(summaries.fault?.stage_p95_ms || {}).map(([key, value]) => ({ key, label: STAGES[key] || key, children: ms(value) }))} />
          {!!comparison.reasons?.length && <Alert type="warning" showIcon message="本次比较存在限制" description={comparison.reasons.join("；")} />}
          <Typography.Text type="secondary">每窗至少 {comparison.policy?.minimum_requests ?? "—"} 个请求；恢复 P95 上限 {ms(comparison.recovery_p95_limit_ms)}。样本量不足以报告稳定 P99。</Typography.Text>
        </Card>;
      })}
    </>}
  </Space>;
}
