import { chineseDiagnosticText, semanticDiagnosticKey } from "./diagnosisDisplay";

const UNKNOWN_PATTERN = /(?:OTHER\s*\/\s*UNKNOWN|其他(?:尚未识别|未知)?(?:的)?原因|未知原因(?:兜底)?)/i;

// Planner 会在不同轮次用略有差异的自然语言重述同一原因。这里把常见性能
// 因果概念归一成稳定标记；它只影响页面投影，不改写服务端 Hypothesis、事件或证据。
const CONCEPT_ALIASES = [
  ["python", /\bpython(?:3(?:\.\d+)?)?\b|Python\s*运行时/giu],
  ["java", /\bjava\b|\bjvm\b|Java\s*运行时/giu],
  ["golang", /\bgolang\b|\bgo\s*(?:runtime|运行时|服务|进程)\b/giu],
  ["cpp", /\bc\+\+\b|C\+\+\s*(?:服务|进程|运行时)/giu],
  ["cpu_hotspot", /(?:用户态|应用层)?\s*(?:CPU\s*)?(?:热点函数|计算热点|热点路径|热函数)|\bcpu[-_\s]*(?:hotspot|hot[-_\s]*path)\b|\bhot[-_\s]*(?:function|path|spot)\b/giu],
  ["gil_contention", /(?:全局解释器锁|\bGIL\b)\s*(?:竞争|争用|瓶颈|contention)?/giu],
  ["gc_pressure", /(?:垃圾回收|\bGC\b)\s*(?:压力|频繁|抖动|风暴|暂停|pause)?|allocation\s*(?:storm|pressure)/giu],
  ["lock_contention", /(?:互斥锁|读写锁|线程锁|\bmutex\b|\bfutex\b|\block\b)\s*(?:竞争|争用|等待|contention)/giu],
  ["io_wait", /(?:磁盘|块设备|存储)?\s*(?:I\s*\/\s*O|IO)\s*(?:等待|阻塞|延迟|拥塞)|\biowait\b|block\s*device\s*latency|同步\s*(?:write|fsync|写入)/giu],
  ["memory_growth", /(?:内存|RSS|PSS|堆)\s*(?:持续)?\s*(?:增长|泄漏|膨胀|保留)|memory\s*(?:growth|leak|retention)/giu],
  ["memory_pressure", /(?:内存|memory)\s*(?:压力|不足|回收)|(?:swap|交换区|页缓存|page\s*cache)\s*(?:活动|抖动|I\s*\/\s*O|IO)?/giu],
  ["network_wait", /(?:网络|TCP|下游)\s*(?:等待|延迟|超时|重传|退化)|network\s*(?:wait|latency|timeout)|downstream\s*(?:wait|latency|timeout)/giu],
  ["dependency_latency", /(?:下游|依赖|数据库|缓存)\s*(?:调用)?\s*(?:慢|延迟|超时|阻塞)|dependency\s*(?:latency|timeout)/giu],
  ["queue_backlog", /(?:任务|请求|生产消费)?\s*(?:队列|积压)\s*(?:堆积|拥塞|过长)?|queue\s*(?:backlog|saturation)/giu],
  ["same_host_contention", /(?:同机|宿主机|噪声邻居|noisy\s*neighbor)\s*(?:资源)?\s*(?:争抢|竞争|干扰)?/giu],
  ["fd_leak", /(?:文件描述符|\bFD\b)\s*(?:泄漏|耗尽|增长)|file\s*descriptor\s*(?:leak|exhaustion)/giu],
];

const NEGATION_PATTERN = /(?:^|[，,；;。\s])(?:没有|不存在|未发现|并非|不是|排除|否定|无明显|no\b|not\b|without\b|ruled\s*out\b)/iu;

const SCAFFOLDING_PATTERNS = [
  /(?:当前|本轮|首轮|目标|该|此|相关|业务|应用|实例|服务|进程|线程|系统|现象|问题|候选|主|备选|假设)/giu,
  /(?:可能|疑似|推测|预计|倾向于|大概率|或许|看起来|初步认为|存在|出现|观察到|表现为|主要|显著|持续)/giu,
  /(?:导致|造成|引起|触发|使得|解释|源于|由于|因为|从而|对应|成为|属于|限制)/giu,
  /(?:升高|增高|上升|下降|变慢|抖动|异常|瓶颈|饱和|高企|恶化)/giu,
  /(?:and|or|with|without|may|might|possibly|likely|causes?|caused\s+by|because\s+of|due\s+to|results?\s+in|the|a|an|is|are|of|in|on|for|target|process|service|runtime)/giu,
  /(?:以及|或者|和|与|或|而非|而是|且|并且|的|了|在|中|上|由|是|为)/giu,
];

function normalizedText(value) {
  return chineseDiagnosticText(value, "")
    .normalize("NFKC")
    .toLowerCase();
}

function stableUnique(values) {
  return [...new Set(values.filter(Boolean))];
}

/**
 * 返回页面层的“因果假设语义键”。它只合并原因概念和关键技术标识都相同的
 * 重述；相反极性、不同函数名或不同诊断域不会被折叠。
 */
export function hypothesisSemanticKey(value) {
  const original = normalizedText(value);
  if (!original) return "hypothesis:empty";
  if (UNKNOWN_PATTERN.test(original)) return "hypothesis:unknown";

  let canonical = original;
  const concepts = [];
  CONCEPT_ALIASES.forEach(([name, pattern]) => {
    pattern.lastIndex = 0;
    if (pattern.test(canonical)) concepts.push(name);
    pattern.lastIndex = 0;
    canonical = canonical.replace(pattern, " ");
  });
  SCAFFOLDING_PATTERNS.forEach((pattern) => {
    pattern.lastIndex = 0;
    canonical = canonical.replace(pattern, " ");
  });

  const detailTokens = stableUnique(
    canonical
      .replace(/[\p{P}\p{S}\s]+/gu, " ")
      .split(/\s+/u)
      .map((token) => token.trim())
      .filter((token) => token.length >= 2 && !/^(?:cpu|io|i\/o|性能|延迟)$/iu.test(token)),
  ).sort();
  const polarity = NEGATION_PATTERN.test(original) ? "negative" : "asserted";
  const conceptKey = stableUnique(concepts).sort().join("+");
  if (conceptKey) return `hypothesis:${polarity}:${conceptKey}:${detailTokens.join("+")}`;

  // 未命中已知诊断域时保持保守：只做中文映射、标点和空白归一，不按模糊
  // 相似度强行合并，避免把两个未知但不同的原因误认为同一个。
  return `hypothesis:${polarity}:literal:${semanticDiagnosticKey(original)}`;
}

function timestampOf(item) {
  const value = new Date(item?.updated_at || item?.created_at || item?.changed_at || 0).getTime();
  return Number.isFinite(value) ? value : 0;
}

function roundOf(item) {
  const value = Number(item?.round_index ?? item?.round ?? 1);
  return Number.isFinite(value) && value > 0 ? value : 1;
}

function dedupeTextList(items = []) {
  const seen = new Set();
  return items.filter((item) => {
    const key = semanticDiagnosticKey(item);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * 把跨轮重述投影为一个语义实体。representative 使用最近一轮记录，另外保留
 * 全部 hypothesis_id、轮次和原始记录，调用方仍可按原 ID 关联工具和证据。
 */
export function mergeSemanticHypotheses(items = [], valueOf = (item) => item?.statement ?? item?.title) {
  const groups = new Map();
  (Array.isArray(items) ? items : []).forEach((item, index) => {
    const key = hypothesisSemanticKey(valueOf(item));
    const entry = { item, index, round: roundOf(item), timestamp: timestampOf(item) };
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(entry);
  });

  return [...groups.entries()].map(([semanticKey, entries]) => {
    const ordered = [...entries].sort((left, right) => (
      left.round - right.round || left.timestamp - right.timestamp || left.index - right.index
    ));
    const latest = ordered[ordered.length - 1].item;
    const roundIndices = stableUnique(ordered.map((entry) => entry.round)).sort((a, b) => a - b);
    const hypothesisIds = stableUnique(ordered.map(({ item }) => item?.hypothesis_id || item?.id));
    return {
      ...latest,
      semantic_key: semanticKey,
      semantic_history: ordered.map(({ item }) => item),
      semantic_count: ordered.length,
      round_indices: roundIndices,
      first_round: roundIndices[0] || 1,
      last_round: roundIndices[roundIndices.length - 1] || 1,
      hypothesis_ids: hypothesisIds,
      expected_observations: dedupeTextList(ordered.flatMap(({ item }) => item?.expected_observations || [])),
      falsification_criteria: dedupeTextList(ordered.flatMap(({ item }) => item?.falsification_criteria || [])),
    };
  }).sort((left, right) => left.first_round - right.first_round);
}

export function shortDiagnosisId(value) {
  const text = String(value || "");
  if (text.length <= 16) return text || "未创建";
  const withoutPrefix = text.replace(/^insight_/, "");
  return `${withoutPrefix.slice(0, 6)}…${withoutPrefix.slice(-4)}`;
}
