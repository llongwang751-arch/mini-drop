import { useEffect, useState } from "react";
import { Alert, Button, Space, Table, Tag } from "antd";

const LABELS = { LOCALIZED_ANOMALY: "工程定位通过", SUPPORTED_OBSERVATION: "有证据支持的诊断候选", REFUTED: "判断通过 · 异常假设被反驳" };

export default function EngineeringDiagnosisSummary({ onLoaded, onOpenDiagnosis }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setData(null); setError(false); onLoaded?.(null);
    (async () => {
      try {
        const response = await fetch("/report-assets/engineering-diagnosis/index.json", { cache: "no-store" });
        if (!response.ok) throw new Error("acceptance unavailable");
        const doc = await response.json();
        if (doc.schema !== "mini-drop.engineering-diagnosis-index.v1" || doc.profile !== "engineering-diagnosis.v1"
            || !Array.isArray(doc.cases) || !Array.isArray(doc.not_evaluated) || doc.fresh_live_run !== false
            || doc.evaluated_scenarios !== doc.cases.length
            || doc.registered_scenarios !== doc.cases.length + doc.not_evaluated.length
            || doc.diagnosis_accepted !== doc.cases.filter(c => c.diagnosis_accepted === true).length
            || doc.localization_accepted !== doc.cases.filter(c => c.localization_accepted === true).length
            || doc.cases.some(c => c.causal_root_cause_verified !== false || c.same_load_fix_verified !== false)) {
          throw new Error("invalid acceptance index");
        }
        if (active) { setData(doc); onLoaded?.(doc); }
      } catch { if (active) setError(true); }
    })();
    return () => { active = false; };
  }, [reload, onLoaded]);
  return <section aria-label="当前工程诊断验收">
    <Alert type="info" showIcon message="默认按工程诊断验收，严格因果实验单独统计"
      description="要求目标正确、证据可信、判断有测量依据、实验撤销与清理完成；独立因果对照、固定调查轮次和同负载代码修复不再是默认必选项。" />
    {error ? <Alert type="warning" message="工程诊断成绩读取失败，不推测通过数量"
      action={<Button onClick={() => setReload(n => n + 1)}>重试工程验收</Button>} /> : data ? <>
      <Space wrap style={{ marginTop: 12 }}>
        <Tag color="green">工程诊断判断通过 {data.diagnosis_accepted}/{data.evaluated_scenarios}</Tag>
        <Tag color="green">异常路径定位 {data.localization_accepted}/{data.evaluated_scenarios}</Tag>
        <Tag color="blue">有效反证 {data.refuted}</Tag>
        <Tag>已注册 {data.registered_scenarios} 类 · 待验收 {data.not_evaluated.length} 类</Tag>
      </Space>
      <p>当前按工程标准重评已冻结的真实案例，不是新一轮 21 类验收。异常未复现也可以有完整判断，但不计为根因定位成功。历史因果成绩只描述旧批次。</p>
      <Table rowKey="scenario_id" size="small" pagination={false} scroll={{ x: 620 }} dataSource={data.cases}
        columns={[{ title: "案例", dataIndex: "title" }, { title: "工程验收", dataIndex: "outcome", render: x => LABELS[x] || "证据不足" },
          { title: "已定位路径", dataIndex: "location", render: x => x || "未宣称异常路径" },
          { title: "原始诊断", render: (_, row) => <Button size="small" onClick={() => onOpenDiagnosis?.(row.diagnosis_id)}>查看判断</Button> }]} />
      <a href={data.download_url} download>下载工程验收规则版本与成绩</a>
    </> : <p role="status">正在读取当前工程诊断验收…</p>}
  </section>;
}
