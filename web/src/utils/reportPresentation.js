import { chineseDiagnosticText } from "./diagnosisDisplay";

function verificationStatus(report) {
  return String(report?.verification?.status || "").toUpperCase();
}

export function reportConclusionTitle(report) {
  if (hasUnattributedHostIO(report)) return "尚未定位根因";
  const status = verificationStatus(report);
  if (/尚未定位|仍待验证|当前没有能够支持/.test(report?.conclusion || "")) return "尚未定位根因";
  if (status === "VERIFIED") return "根因结论";
  if (status === "PARTIAL_WITHOUT_COUNTER") return "阶段性发现（待验证）";
  if (["INSUFFICIENT_EVIDENCE", "FALSIFIED", "REJECTED"].includes(status)) {
    return "本轮判断";
  }
  return "诊断判断";
}

export function hasUnattributedHostIO(report) {
  return validClaims(report).some(claim => /host block-device tracepoints/i.test(claim.statement || ""));
}

export function reportLimitations(report) {
  if (hasUnattributedHostIO(report)) return ["历史报告引用的是主机级 I/O，未证明来自目标进程，原支持标记不能用于确认进程根因。缺少正常基线和修复前后对照。"];
  return (report?.limitations || []).map(text => String(text).replace("结论已完成，但仍应在修复复测中补充独立验证", "调查记录已生成，因果关系与修复效果仍待验证"));
}

export function reportNextActions(report) {
  if (hasUnattributedHostIO(report)) return ["采集同窗口的目标进程读写计数与阻塞调用栈，再关联主机 I/O；队列积压需同时比较生产速率、消费速率和队列长度。"];
  if (/尚未定位|仍待验证|当前没有能够支持/.test(report?.conclusion || "")) return ["先补充目标进程的资源变化与运行时调用栈，定位具体瓶颈后再制定修复；当前不能直接给出修复方案。"];
  return report?.next_actions || [];
}

function validClaims(report) {
  const claims = Array.isArray(report?.claims)
    ? report.claims
    : Array.isArray(report?.verification?.claims)
      ? report.verification.claims
      : [];
  return claims.filter((claim) => claim && claim.valid !== false);
}

function allocationType(claims) {
  for (const claim of claims) {
    if (claim.claim_type !== "TOP_FUNCTION_PERCENT") continue;
    const match = String(claim.statement || "").match(/^(.+?\[\])\s+占\s+[\d.]+%\s+样本$/u);
    if (match) return match[1].trim();
  }
  return "";
}

function javaBusinessFunction(claims, fallback) {
  for (const claim of claims) {
    if (claim.claim_type !== "TOP_FUNCTION_PERCENT") continue;
    const match = String(claim.statement || "").match(/^(.+?)\s+占\s+[\d.]+%\s+样本$/u);
    const name = match?.[1]?.trim() || "";
    if (!name || name.includes("$$Lambda") || name.endsWith("[]")) continue;
    if (name.startsWith("java/") || name.startsWith("jdk/")) continue;
    return name;
  }
  return fallback;
}

/**
 * Older immutable reports stored the planner hypothesis as their conclusion.
 * Recover a specific display finding from the already verified claim list so
 * historical sessions become readable without rewriting audit records.
 */
function legacyEvidenceFinding(report) {
  const claims = validClaims(report);
  for (const claim of claims) {
    if (claim.claim_type !== "HYPOTHESIS_PREDICATE") continue;
    const statement = String(claim.statement || "").trim();
    let match = statement.match(
      /^async-profiler\s+(alloc|lock|wall|cpu)\s+profile captured Java path\s+(.+?)\s+at\s+([\d.]+)%$/i,
    );
    if (match) {
      const [, event, functionName, percent] = match;
      const labels = {
        alloc: "Java 对象分配热点",
        lock: "Java 锁等待热点",
        wall: "Java 阻塞/等待热点",
        cpu: "Java CPU 执行热点",
      };
      const boundaries = {
        alloc: "该证据确认了集中对象分配路径，但没有独立证明 GC 暂停或锁竞争是主瓶颈。",
        lock: "该证据确认了锁等待路径，但仍需修复前后对照证明它对整体延迟的因果贡献。",
        wall: "该证据确认了阻塞路径，但仍需依赖侧或系统侧证据区分具体等待来源。",
        cpu: "该证据确认了 CPU 热路径，但仍需修复前后对照确认其因果贡献。",
      };
      const kind = event.toLowerCase();
      const objectType = kind === "alloc" ? allocationType(claims) : "";
      const businessFunction = javaBusinessFunction(claims, functionName);
      return `${labels[kind]}定位在业务调用路径 \`${businessFunction}\`，占有效样本的 ${percent}%${
        objectType ? `，主要分配对象为 \`${objectType}\`` : ""
      }。${boundaries[kind]}`;
    }

    match = statement.match(
      /^Go pprof captured source-mapped application hotspot\s+(.+?)\s+at\s+([\d.]+)%$/i,
    );
    if (match) {
      return `Go CPU 热点定位在 \`${match[1]}\`，占有效样本的 ${match[2]}%。该函数是当前证据窗口内最集中的执行路径；仍需修复前后对照确认因果贡献。`;
    }

    match = statement.match(
      /^dominant user-space hotspot\s+(.+?)\s+accounts for\s+([\d.]+)%/i,
    );
    if (match) {
      return `用户态性能热点定位在 \`${match[1]}\`，占有效样本的 ${match[2]}%。该函数是当前证据窗口内最集中的执行路径；仍需修复前后对照确认因果贡献。`;
    }
  }
  return "";
}

// Reports describe different hypotheses. A later inconclusive branch must not
// replace an earlier verified finding in the session summary.
export function selectBestReport(reports = []) {
  const rank = { VERIFIED: 3, PARTIAL_WITHOUT_COUNTER: 2, INSUFFICIENT_EVIDENCE: 1 };
  return [...reports].sort((left, right) => {
    const status = (item) => String(item?.verification?.status || item?.verification_status || "").toUpperCase();
    return (rank[status(right)] || 0) - (rank[status(left)] || 0)
      || (right?.evidence_refs?.length || 0) - (left?.evidence_refs?.length || 0)
      || Number(right?.confidence || 0) - Number(left?.confidence || 0)
      || new Date(right?.updated_at || right?.created_at || 0) - new Date(left?.updated_at || left?.created_at || 0)
      || Number(right?.version || 0) - Number(left?.version || 0);
  })[0] || null;
}

export function reportConclusionText(report) {
  if (hasUnattributedHostIO(report)) {
    const claim = validClaims(report).find(item => item.claim_type === "SYS_METRIC_IO_LATENCY" && Number.isFinite(item.claimed_value));
    return `尚未确定目标进程的故障原因。采到了宿主机块设备 I/O 延迟分布${claim ? `，报告记录的延迟指标为 ${claim.claimed_value} 微秒` : ""}，但没有把这些 I/O 归属到目标进程。没有正常基线，不能仅凭该数值认定磁盘异常，更不能证明它导致队列积压。`;
  }
  const original = String(report?.conclusion || "暂无可信判断").trim();
  const isLegacyGeneric = /^(?:SUPPORTED|MIXED_EVIDENCE|INSUFFICIENT_EVIDENCE)[：:]/i.test(original);
  if (isLegacyGeneric) {
    const recovered = legacyEvidenceFinding(report);
    if (recovered) return recovered;
  }

  const withoutTitle = original.replace(
    /^(?:根因结论|阶段性根因|阶段性判断|本轮判断)[：:]\s*/u,
    "",
  );
  return chineseDiagnosticText(
    withoutTitle
      .replace(/^SUPPORTED[：:]\s*/i, "现有可信证据支持：")
      .replace(/^MIXED_EVIDENCE[：:]\s*/i, "现有证据存在冲突：")
      .replace(/^INSUFFICIENT_EVIDENCE[：:]\s*/i, "当前证据不足："),
  );
}
