import { useEffect, useState } from "react";
import { Alert, Button, Collapse, Space, Table, Tag } from "antd";

const CATEGORIES = {
  LEGACY_LINEAGE_ACCEPTED: "通过旧链路检查",
  OUTCOME_REJECTED_BY_LINEAGE_WRAPPER: "证据不足被旧包装器算作链路失败",
  SUPPORT_COVERAGE_REJECTED_BY_LINEAGE_WRAPPER: "支持结论不足被算作链路失败",
  WRONG_TARGET: "选错 Agent / 进程",
};

export default function PerformanceDiagnosisSummary() {
  const [audit, setAudit] = useState(null);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setAudit(null); setError(false);
    fetch("/report-assets/performance-audit/index.json", { cache: "no-store" })
      .then(response => { if (!response.ok) throw new Error("audit unavailable"); return response.json(); })
      .then(data => {
        if (data.schema !== "mini-drop.performance-failure-audit.v1" || !Array.isArray(data.cases)
            || data.cases.length !== data.historical_case_count || data.historical_case_count !== 21
            || data.historical_root_passes !== 0) throw new Error("invalid audit");
        if (active) setAudit(data);
      }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [reload]);
  return <section aria-label="性能诊断能力与历史复盘">
    <Alert type="info" showIcon message="CPU、内存、I/O、网络、锁、GC、队列：继续保留 21 类性能实验"
      description="检查结果分为：本次检查正常（已检查范围）、证据不足、发现异常但原因未明、已验证性能观测、因果根因已验证。工程缺陷回归单独统计。" />
    {error ? <Alert type="warning" showIcon message="历史复盘读取失败，不显示推测成绩"
      action={<Button aria-label="重试" onClick={() => setReload(value => value + 1)}>重试</Button>} /> : audit ?
      <Collapse style={{ marginTop: 12 }} items={[{ key: "history", label: "为什么旧 21 类根因为 0：查看逐项复盘",
        children: <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Space wrap><Tag>原始因果根因 {audit.historical_root_passes}/{audit.historical_case_count}</Tag>
            <Tag>归档记录证据链一致 {audit.recorded_chain_consistent_count}/{audit.historical_case_count}</Tag>
            <Tag>新版本云端根因：尚未验收</Tag></Space>
          <p>证据链一致只表示归档记录中的任务、尝试、产物 SHA 与证据来源能够对应，不是重新下载校验，也不表示根因成立。旧实验撤销了注入负载，没有同负载代码修复验证；原成绩保持不变。</p>
          <Table rowKey="scenario_id" size="small" pagination={false} scroll={{ x: 620 }} dataSource={audit.cases}
            columns={[{ title: "性能场景", dataIndex: "scenario_id" },
              { title: "旧验收的阻塞点", dataIndex: "failure_category", render: code => CATEGORIES[code] || "其他执行失败" },
              { title: "归档证据链", dataIndex: "chain_audit", render: chain => chain?.recorded_chain_consistent === true ? "记录一致" : "未通过" },
              { title: "因果根因", render: () => "未验证" }]} />
          <a href={audit.download_url} download>下载逐项复盘与原始文件 SHA</a>
        </Space> }]} /> : <p role="status">正在读取原始验收复盘…</p>}
  </section>;
}
