import { useEffect, useState } from "react";
import { Alert, Button, Card, Spin, Tag, Typography } from "antd";
import { getFaultPlaza } from "../api/client";
import "./EngineeringCasesPanel.css";

const { Paragraph, Text, Title } = Typography;
const BASE = "/report-assets/engineering-cases/";

function validCounts(value) {
  return value && ["selected", "passed", "failed", "skipped"].every(
    key => Number.isInteger(value[key]) && value[key] >= 0,
  ) && value.selected > 0 && value.selected === value.passed + value.failed + value.skipped;
}

export function validateCatalog(data) {
  if (data?.schema !== "mini-drop.engineering-case-index.v1"
      || data.validation_scope !== "ENGINEERING_DEFECT_REGRESSION"
      || data.model_auto_root_cause !== "NOT_EVALUATED"
      || !Array.isArray(data.cases) || !data.cases.length) throw new Error("案例索引格式或验证范围不正确");
  const ids = new Set();
  for (const item of data.cases) {
    if (!/^[a-z][a-z0-9-]{1,63}$/.test(item.id) || ids.has(item.id)
        || item.status !== "VERIFIED_DEFECT_FIX"
        || item.validation_scope !== data.validation_scope
        || item.model_auto_root_cause !== "NOT_EVALUATED"
        || !validCounts(item.before) || !validCounts(item.after)
        || item.before.failed < 1 || item.before.skipped !== 0
        || item.after.failed !== 0 || item.after.skipped !== 0
        || item.before.selected !== item.after.selected
        || !["title", "area", "symptom", "root_cause", "fix", "scope_note", "reproduce_command"].every(
          key => typeof item[key] === "string" && item[key].trim(),
        ) || !/^https:\/\/github\.com\/llongwang751-arch\/mini-drop\/blob\//.test(item.source_url)
        || !Array.isArray(item.evidence) || !item.evidence.length
        || !item.evidence.every(file => file.filename.startsWith(item.id + "/")
          && /^[a-zA-Z0-9._-]+\/[a-zA-Z0-9._-]+$/.test(file.filename)
          && !file.filename.split("/").includes("..")
          && /^[a-f0-9]{64}$/.test(file.sha256))) throw new Error("案例证据不完整，不能显示为已验证");
    ids.add(item.id);
  }
  return data;
}

export default function EngineeringCasesPanel({ onOpenHistorical }) {
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [faultState, setFaultState] = useState({ loading: true });
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    let mounted = true;
    setCatalog(null);
    setError("");
    fetch(BASE + "index.json", { signal: controller.signal, cache: "no-cache" })
      .then(response => {
        if (!response.ok) throw new Error("案例索引读取失败");
        return response.json();
      }).then(validateCatalog).then(data => { if (mounted) setCatalog(data); })
      .catch(reason => { if (mounted) setError(reason.name === "AbortError" ? "案例读取超时，请重试" : reason.message); })
      .finally(() => clearTimeout(timeout));
    return () => { mounted = false; clearTimeout(timeout); controller.abort(); };
  }, [attempt]);
  useEffect(() => {
    let mounted = true;
    getFaultPlaza().then(data => {
      if (!Array.isArray(data?.scenarios)) throw new Error("未知故障状态");
      if (mounted) setFaultState({ active: data.scenarios.filter(item => item.active === true).length });
    }).catch(() => { if (mounted) setFaultState({ error: true }); });
    return () => { mounted = false; };
  }, []);

  return <section className="engineering-cases" aria-label="已验证工程缺陷">
    <div className="engineering-cases-intro">
      <div>
        <Title level={4}>从失败复现到修复回归</Title>
        <Paragraph>展示项目中真实发生并已修复的缺陷：现象、原因、修复及同一测试的前后结果。</Paragraph>
        <Text type="secondary">工程缺陷修复回归；不计入 AI 自动根因成绩。</Text>
      </div>
      <Button onClick={onOpenHistorical}>查看历史故障实验</Button>
    </div>
    {faultState.active > 0 && <Alert type="warning" showIcon message={`仍有 ${faultState.active} 个历史故障处于启用状态`} description="请进入历史故障实验，使用停止入口撤销并核对恢复。" />}
    {faultState.error && <Alert type="warning" showIcon message="历史故障运行状态未读取" description="可进入历史故障实验刷新状态和使用停止入口。" />}
    {error ? <Alert type="error" showIcon message="无法验证案例数据" description={error}
      action={<Button aria-label="重试" onClick={() => setAttempt(value => value + 1)}>重试</Button>} />
      : !catalog ? <div role="status" className="engineering-cases-loading"><Spin /> 正在读取案例证据…</div>
      : <>
        <div className="engineering-cases-summary" role="status">
          <Text strong>{catalog.cases.length} 个缺陷闭环已验证</Text>
          <Text>每项都有修复前失败与修复后通过的原始记录。</Text>
        </div>
        <div className="engineering-cases-grid">
          {catalog.cases.map(item => <Card key={item.id} title={<span className="engineering-cases-title">{item.title}</span>}>
            <div className="engineering-cases-tags"><Tag>{item.area}</Tag><Tag color="green">修复回归通过</Tag></div>
            <dl>
              <dt>现象</dt><dd>{item.symptom}</dd>
              <dt>根因</dt><dd>{item.root_cause}</dd>
              <dt>修复</dt><dd>{item.fix}</dd>
            </dl>
            <div className="engineering-cases-results">
              <Text>修复前：{item.before.failed}/{item.before.selected} 项失败</Text>
              <Text strong>修复后：{item.after.passed}/{item.after.selected} 项通过，0 跳过</Text>
            </div>
            <Paragraph className="engineering-cases-scope">{item.scope_note}</Paragraph>
            <details>
              <summary>源码、复现命令与原始证据</summary>
              <a href={item.source_url} target="_blank" rel="noreferrer">查看修复源码</a>
              <pre>{item.reproduce_command}</pre>
              <ul>{item.evidence.map(file => <li key={file.role}>
                <a href={BASE + file.filename} download>{file.filename.split("/").pop()}</a>
                <small>SHA-256：{file.sha256}</small>
              </li>)}</ul>
            </details>
          </Card>)}
        </div>
      </>}
  </section>;
}
