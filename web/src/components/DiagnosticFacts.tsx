/**
 * v2 事实字段由 Go 对照真实工具逐项核对；模板描述与自由模型解释分别展示。
 * “字段核对”不表示业务因果或根因已确认，旧报告也不能因为共用视图获得新验收标签。
 */
import "../styles/knowledge.css";

export interface DiagnosticVerifiedFact {
  id: string;
  evidence_id: string;
  tool: string;
  path: string;
  operator: "eq" | "count" | "count_positive_self";
  value: string | number | boolean;
  unit: string;
  description: string;
}
export function parseDiagnosticFacts(
  input: unknown,
): DiagnosticVerifiedFact[] | undefined {
  if (!Array.isArray(input) || input.length < 1 || input.length > 24)
    return undefined;
  const seen = new Set<string>();
  for (const value of input) {
    if (!value || typeof value !== "object" || Array.isArray(value))
      return undefined;
    const row = value as Record<string, unknown>;
    if (
      typeof row.id !== "string" ||
      !/^F[1-9]\d{0,2}$/.test(row.id) ||
      seen.has(row.id) ||
      typeof row.evidence_id !== "string" ||
      !/^E(?:[1-9]|1[0-5])$/.test(row.evidence_id) ||
      typeof row.tool !== "string" ||
      ![
        "read_profile_summary",
        "inspect_hotspots",
        "check_collection_quality",
        "inspect_call_paths",
        "propose_verification",
        "compare_profiles",
        "query_business_signals",
        "inspect_service_topology",
      ].includes(row.tool) ||
      typeof row.path !== "string" ||
      !row.path.startsWith("/") ||
      row.path.length > 160 ||
      !["eq", "count", "count_positive_self"].includes(String(row.operator)) ||
      !["string", "number", "boolean"].includes(typeof row.value) ||
      (typeof row.value === "number" && !Number.isFinite(row.value)) ||
      typeof row.unit !== "string" ||
      !/^[a-z_]{1,40}$/.test(row.unit) ||
      typeof row.description !== "string" ||
      !row.description.trim() ||
      row.description.length > 1800
    )
      return undefined;
    seen.add(row.id);
  }
  return input as DiagnosticVerifiedFact[];
}

export default function DiagnosticFacts({
  reportVersion,
  facts,
  hypotheses,
  runID,
}: {
  reportVersion?: number;
  facts?: unknown;
  hypotheses?: unknown;
  runID?: string;
}) {
  if (reportVersion !== 2) return null;
  const rows = parseDiagnosticFacts(facts);
  const explanations = Array.isArray(hypotheses)
    ? hypotheses.flatMap((item, index) => {
        if (!item || typeof item !== "object") return [];
        const text = (item as Record<string, unknown>)
          .unverified_interpretation;
        return typeof text === "string" && text.trim() && text.length <= 600
          ? [{ index, text }]
          : [];
      })
    : [];
  const validRun = !!runID && /^[1-9]\d{0,18}$/.test(runID);
  return (
    <section className="knowledge-prior" aria-label="结构化事实与模型解释">
      <h3>本轮结构化事实</h3>
      <p className="knowledge-prior-note">
        程序核对字段、数值、单位与工具出处，并生成下面的描述。它们只陈述本轮观测，不能自动证明根因、修复收益或完整业务语义。
      </p>
      {rows ? (
        <>
          <p>
            <span className="badge active">字段、数值、单位与出处已核对</span>
          </p>
          <div
            className="table-scroll"
            role="region"
            aria-label="已核对事实来源表"
            tabIndex={0}
          >
            <table className="data-table" style={{ whiteSpace: "normal" }}>
              <thead>
                <tr>
                  <th scope="col">事实</th>
                  <th scope="col">程序生成的观测描述</th>
                  <th scope="col">出处</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.id}
                    id={validRun ? `fact-${runID}-${row.id}` : undefined}
                  >
                    <th scope="row">{row.id}</th>
                    <td>
                      {row.description}
                      <details>
                        <summary>字段与单位</summary>
                        <p className="mono">
                          {row.tool} · {row.path} · {row.operator} · {row.unit}
                        </p>
                      </details>
                    </td>
                    <td>
                      {validRun ? (
                        <a href={`#diagnosis-${runID}-${row.evidence_id}`}>
                          {row.evidence_id}
                        </a>
                      ) : (
                        row.evidence_id
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p role="alert">
          本报告缺少完整的结构化事实字段，未显示事实核对通过标签。
        </p>
      )}
      {explanations.map((item) => (
        <section className="diagnosis-hypothesis" key={item.index}>
          <h4>未经事实核验的模型解释 · 假设 {item.index + 1}</h4>
          <p>{item.text}</p>
          <p className="knowledge-prior-note">
            这是模型的待验证解释，不属于上方已核对事实。
          </p>
        </section>
      ))}
    </section>
  );
}
