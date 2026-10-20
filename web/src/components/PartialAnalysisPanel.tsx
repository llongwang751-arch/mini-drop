/**
 * 只读呈现失败轮次的已校验检查点，并同时列出已完成与缺失阶段。
 * 这不是完整成功报告，因此不提供火焰图、诊断建议或会让用户误判为已完成的操作。
 */
import { FileText } from "@phosphor-icons/react";
import { number } from "../api";
import type { PartialAnalysis } from "../partial-analysis";
import "./PartialAnalysisPanel.css";

export default function PartialAnalysisPanel({
  result,
}: {
  result: PartialAnalysis;
}) {
  return (
    <section
      className="panel partial-analysis-panel"
      aria-label="本次未完成分析的已解析摘要"
      data-analysis-run={result.analysis_run}
    >
      <header className="partial-analysis-heading">
        <FileText size={24} aria-hidden="true" />
        <div>
          <span className="eyebrow">本次执行 · 部分结果</span>
          <h2>未完成分析 · 已解析摘要</h2>
          <p>
            输入校验与数据解析已完成。这份摘要不包含规则诊断、最终分析制品和完整结果；任务仍为分析失败。
          </p>
        </div>
      </header>
      <dl className="partial-analysis-metrics">
        {[
          [
            "已解析权重",
            `${number(result.summary.total_samples)} ${result.summary.unit}`,
          ],
          ["独立调用栈", number(result.summary.unique_stacks)],
          ["解析节点", number(result.summary.node_count)],
          ["最大栈深", number(result.summary.max_depth)],
        ].map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="partial-analysis-provenance">
        <span>
          {result.summary.source === "import"
            ? "来源：用户导入数据"
            : `数据来源：${result.summary.source}`}
        </span>
        <span className="mono">分析第 {result.analysis_run} 次执行</span>
      </div>
      <section className="partial-analysis-top">
        <h3>已解析函数热点</h3>
        <p>
          仅展示本次已解析数据中的前 {result.top_functions.length}{" "}
          项；权重不等于请求延迟，也不是完整诊断结论。
        </p>
        {result.top_functions.length ? (
          <div
            className="partial-analysis-table"
            tabIndex={0}
            role="region"
            aria-label="部分分析函数热点表格"
          >
            <table>
              <thead>
                <tr>
                  <th>函数</th>
                  <th>自身权重</th>
                  <th>累计占比</th>
                </tr>
              </thead>
              <tbody>
                {result.top_functions.map((row, index) => (
                  <tr key={`${row.symbol_sha256}-${index}`}>
                    <td>
                      <span className="mono partial-analysis-symbol">
                        {row.name}
                      </span>
                      <small className="mono">
                        SHA256 {row.symbol_sha256.slice(0, 12)}…
                      </small>
                    </td>
                    <td className="mono">
                      {number(row.self)}
                      <small>{number(row.self_percent)}%</small>
                    </td>
                    <td className="mono">{number(row.total_percent)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="partial-analysis-empty">
            这类输入没有可列出的函数热点；上方保留已验证的解析计数。
          </p>
        )}
      </section>
      <details className="partial-analysis-binding">
        <summary>查看本次执行与输入标识</summary>
        <dl>
          <div>
            <dt>任务 / 采集尝试</dt>
            <dd className="mono">
              #{result.task_id} / {result.attempt_id}
            </dd>
          </div>
          <div>
            <dt>分析任务 / 执行轮次</dt>
            <dd className="mono">
              {result.analysis_job_id} / {result.analysis_run}
            </dd>
          </div>
          <div>
            <dt>输入制品 / SHA256</dt>
            <dd className="mono">
              #{result.input_artifact_id} · {result.input_digest}
            </dd>
          </div>
        </dl>
      </details>
      <p className="partial-analysis-limitation">
        后续处理可能已经执行过，但不包含在这份快照中。请结合上方失败信息和执行时间线排查；重新分析会清除本次快照。
      </p>
    </section>
  );
}
