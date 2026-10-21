/** 服务端附加的知识引用视图，不把历史案例编号伪装成当前诊断的 evidence_id。 */
import { Link } from "react-router-dom";
import type { DiagnosisKnowledge } from "../diagnostic-knowledge";
import {
  caseAssuranceLabel,
  caseOutcomeLabel,
  knowledgeReasonLabel,
} from "../diagnostic-knowledge";
import "../styles/knowledge.css";

export default function DiagnosticKnowledge({
  knowledge,
}: {
  knowledge: DiagnosisKnowledge;
}) {
  return (
    <section className="knowledge-prior" aria-label="本轮参考的诊断知识">
      <strong>本轮参考的诊断知识</strong>
      <p className="knowledge-prior-note">
        Skill
        和历史经验是排查先验，不是本次采集证据。结论仍须由本轮工具观察、反证与复测支持。
      </p>
      <div className="knowledge-prior-tags">
        <span className="knowledge-tag">服务端附加引用</span>
        <span className="knowledge-tag">
          {knowledge.history_enabled ? "已启用历史经验匹配" : "未启用历史经验"}
        </span>
      </div>
      {knowledge.skills.length ? (
        <ul>
          {knowledge.skills.map((skill) => (
            <li key={`${skill.id}:${skill.version}`}>
              <Link to={`/knowledge#skill-${encodeURIComponent(skill.id)}`}>
                {skill.title} · v{skill.version}
              </Link>
              {skill.match_reasons?.length ? (
                <p>
                  匹配原因：
                  {skill.match_reasons.map(knowledgeReasonLabel).join("；")}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="knowledge-prior-note">本轮未匹配诊断 Skill。</p>
      )}
      {knowledge.cases.length ? (
        <ul aria-label="参考的历史案例">
          {knowledge.cases.map((item) => (
            <li key={item.id}>
              <strong>
                历史案例 #{item.id} · {caseOutcomeLabel(item.outcome)}
              </strong>
              <span className="knowledge-tag">
                {caseAssuranceLabel(item.assurance)}
              </span>
              <p>{item.conclusion}</p>
              <p>
                匹配原因：
                {item.match_reasons.map(knowledgeReasonLabel).join("；") ||
                  "服务端未返回匹配说明"}
              </p>
              {item.skill_refs.length ? (
                <p className="knowledge-prior-note">
                  关联 Skill：
                  {item.skill_refs
                    .map((skill) => `${skill.id}@${skill.version}`)
                    .join("、")}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="knowledge-prior-note">本轮没有可复用的历史案例。</p>
      )}
      <p>
        <Link to="/knowledge?tab=cases">管理我复核的案例</Link>
      </p>
    </section>
  );
}
