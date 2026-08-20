import { useState } from "react";
import { Alert, Button, Card, Input, Space, Tag, Typography } from "antd";

const { Text, Paragraph } = Typography;

const LABELS = {
  correct: { text: "结论正确", color: "green" },
  partial: { text: "部分正确", color: "orange" },
  wrong: { text: "结论错误", color: "red" },
};

export default function DiagnosisFeedbackCard({ report, latestFeedback, onSubmit, submitting }) {
  const [label, setLabel] = useState("");
  const [correctedCause, setCorrectedCause] = useState("");
  const [note, setNote] = useState("");
  const verificationStatus = String(report?.verification?.status || "").toUpperCase();
  const conclusion = String(report?.conclusion || "").toUpperCase();
  const isInsufficient = verificationStatus === "INSUFFICIENT_EVIDENCE"
    || conclusion.startsWith("INSUFFICIENT_EVIDENCE");

  const labels = isInsufficient
    ? {
        correct: { text: "确实证据不足", color: "green" },
        partial: { text: "已有证据被遗漏", color: "orange" },
        wrong: { text: "诊断方向错误", color: "red" },
      }
    : LABELS;

  async function submit(selected = label) {
    if (!selected) return;
    await onSubmit({
      report_id: report?.report_id,
      hypothesis_id: report?.hypothesis_id,
      feedback_label: selected,
      corrected_cause: correctedCause.trim() || undefined,
      feedback_note: note.trim() || undefined,
      request_replan: selected !== "correct",
    });
    setLabel("");
    setCorrectedCause("");
    setNote("");
  }

  return (
    <Card size="small" title="评价本轮诊断" style={{ marginBottom: 10 }}>
      <Paragraph type="secondary" style={{ marginBottom: 10 }}>
        {isInsufficient
          ? "本轮没有确认根因，而是因证据不足主动停止。请选择系统为什么没有得到根因；后两项会开启下一轮取证。"
          : "你的反馈不会覆盖旧结论。系统会保留本轮证据；部分正确或错误时，新建下一轮假设继续取证。"}
      </Paragraph>
      {isInsufficient && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 10 }}
          message="这不是根因结论"
          description="“证据不足”表示当前数据不足以证明 CPU 热点假设。若你也无法从现有证据确认根因，选择“确实证据不足”；若页面已有能证明根因的数据，选择“已有证据被遗漏”；若采集方向本身不对，选择“诊断方向错误”。"
        />
      )}
      {latestFeedback && (
        <Alert
          type={latestFeedback.feedback_label === "correct" ? "success" : "info"}
          showIcon
          style={{ marginBottom: 10 }}
          message={
            <Space wrap>
              <Text>最近反馈</Text>
              <Tag color={labels[latestFeedback.feedback_label]?.color}>
                {labels[latestFeedback.feedback_label]?.text || latestFeedback.feedback_label}
              </Tag>
              {latestFeedback.revision_hypothesis_id && <Tag color="purple">已开启下一轮</Tag>}
            </Space>
          }
        />
      )}
      <Space wrap>
        <Button loading={submitting} onClick={() => submit("correct")}>{labels.correct.text}</Button>
        <Button type={label === "partial" ? "primary" : "default"} onClick={() => setLabel("partial")}>{labels.partial.text}</Button>
        <Button danger type={label === "wrong" ? "primary" : "default"} onClick={() => setLabel("wrong")}>{labels.wrong.text}</Button>
      </Space>
      {label && label !== "correct" && (
        <Space direction="vertical" style={{ width: "100%", marginTop: 12 }}>
          <Input
            value={correctedCause}
            onChange={(event) => setCorrectedCause(event.target.value)}
            placeholder={isInsufficient && label === "partial"
              ? "填写被系统遗漏的证据或你已确认的原因"
              : "你认为更可能的原因，例如：同宿主机噪声邻居导致 CPU 争抢"}
          />
          <Input.TextArea
            rows={2}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder={isInsufficient
              ? "说明下一轮应该补采什么，或为什么当前采集方向不对"
              : "补充你观察到的现象或希望下一轮验证的方向"}
          />
          <Button
            type="primary"
            loading={submitting}
            disabled={!correctedCause.trim() && !note.trim()}
            onClick={() => submit()}
          >
            提交反馈并开始下一轮取证
          </Button>
        </Space>
      )}
    </Card>
  );
}
