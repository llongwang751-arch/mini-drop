const STATUS_LABELS = {
  CREATED: "已创建",
  QUEUED: "等待下发",
  DELIVERED: "采集节点已接收",
  NEEDS_CLARIFICATION: "需要补充信息",
  HYPOTHESIZING: "正在生成假设",
  PLANNING: "正在规划",
  WAITING_APPROVAL: "等待人工审批",
  PENDING_APPROVAL: "等待人工审批",
  APPROVED: "已批准",
  REJECTED: "已拒绝",
  DENIED: "策略已拒绝",
  TASK_CREATED: "采集任务已创建",
  COLLECTING: "正在取证",
  COLLECTING_EVIDENCE: "正在取证",
  ANALYZING: "正在分析",
  EVALUATING: "正在裁决证据",
  REPORTING: "正在生成报告",
  UPLOADING: "正在上传采集物",
  COLLECTED: "原始数据已保存",
  COMPLETED: "已完成",
  INSUFFICIENT_EVIDENCE: "证据不足",
  FAILED: "执行失败",
  CANCELLED: "已取消",
  CANCELED: "已取消",
  OPEN: "待验证",
  SUPPORTED: "有证据支持",
  COUNTER: "有反证",
  INCONCLUSIVE: "证据不足",
  READY: "可用",
  RUNNING: "运行中",
  PENDING: "等待中",
  DONE: "已完成",
  SUCCEEDED: "成功",
  SUCCESS: "成功",
  RETRY: "等待重试",
  RETRYING: "正在重试",
  NOT_STARTED: "尚未开始",
  SKIPPED: "已跳过",
  ONLINE: "在线",
  OFFLINE: "离线",
  ACTIVE: "已发布",
  CANDIDATE: "候选",
  QUARANTINED: "已隔离",
  RETIRED: "已退役",
  REFUTED: "已被反证",
  FALSIFIED: "已被证伪",
  DEPRIORITIZED: "已降低优先级",
  PRUNED: "已剪枝",
};

const EVIDENCE_ROLE_LABELS = {
  SUPPORT: "支持证据",
  SUPPORTS: "支持证据",
  SUPPORTED: "支持证据",
  COUNTER: "反证",
  COUNTERS: "反证",
  REFUTES: "反证",
  CONTROL: "对照证据",
  NEUTRAL: "中性观察",
  UNVERIFIED_EXTERNAL: "外部信息（未验证）",
};

const EVIDENCE_DECISION_LABELS = {
  ACCEPT: "已采信",
  ACCEPTED: "已采信",
  ACCEPT_SUPPORT: "采信为支持证据",
  ACCEPT_COUNTER: "采信为反证",
  ACCEPT_NEUTRAL: "采信为中性观察",
  ACCEPT_LIMITED: "有限采信",
  USABLE: "可用于结论",
  REJECT: "证据门禁拒绝",
  REJECT_LOW_QUALITY: "因质量不足被拒绝",
};

const POLICY_DECISION_LABELS = {
  ALLOW: "策略允许",
  REQUIRE_APPROVAL: "需要人工审批",
  DENY: "策略拒绝",
  BLOCK: "策略拦截",
  BLOCKED: "策略已拦截",
};

const VERIFICATION_STATUS_LABELS = {
  VERIFIED: "已通过完整验证",
  PARTIAL_WITHOUT_COUNTER: "部分支持，缺少独立反证或对照",
  INSUFFICIENT_EVIDENCE: "证据不足",
  FALSIFIED: "已被反证",
  REJECTED: "未通过证据门禁",
};

const ERROR_CODE_LABELS = {
  ANALYSIS_INPUT_INVALID: "分析输入不符合契约",
  ANALYSIS_TIMEOUT: "分析超时",
  ANALYSIS_RETRY_EXHAUSTED: "分析重试次数已用尽",
  DEADLINE_EXCEEDED: "执行超时",
  EMPTY_ANALYSIS_ARTIFACT: "分析结果为空",
  FAILED_PRECONDITION: "执行前置条件不满足",
  INTERNAL: "内部服务异常",
  INTERNAL_ERROR: "内部服务异常",
  INVALID_ARGUMENT: "参数不合法",
  INVALID_SAMPLE_COUNT: "样本数量不合法",
  LEASE_EXPIRED: "任务租约已过期",
  LEASE_LOST: "任务租约已失效",
  NO_FOLDED_STACKS: "没有可用于生成火焰图的调用栈",
  NO_PERF_SAMPLES: "perf 未采集到有效样本",
  NO_PROFILE_SAMPLES: "性能剖析未采集到有效样本",
  NO_RENDERABLE_STACKS: "没有可渲染的调用栈",
  NOT_FOUND: "目标资源不存在",
  PERMISSION_DENIED: "权限不足",
  RESOURCE_EXHAUSTED: "资源或配额已耗尽",
  STORAGE_UNAVAILABLE: "对象存储暂不可用",
  TASK_CANCELED: "采集任务已取消",
  UNAVAILABLE: "服务暂不可用",
  UNUSABLE_PROFILE: "性能剖析结果不可用",
};

const EVENT_LABELS = {
  "diagnosis.created": "创建诊断",
  "diagnosis.clarified": "补充诊断信息",
  "planner.needs_clarification": "规划器请求补充信息",
  "hypothesis.created": "建立候选假设",
  adaptive_probe_planned: "规划自适应探针",
  falsification_round_planned: "规划反证轮次",
  falsification_route_replanned: "根据证据重新规划",
  "planner.insufficient_replanned": "证据不足后重新规划",
  diagnosis_stop_condition_met: "达到诊断停止条件",
  "diagnosis.route_learned": "沉淀诊断路线",
  "diagnosis.completed_from_supported_report": "采用最佳可信报告完成诊断",
  "diagnosis.insufficient_evidence_finalized": "确认搜索范围内证据不足",
  diagnostic_capability_gap: "发现诊断能力缺口",
  "tool_call.requested": "请求工具取证",
  "tool_call.task_created": "创建采集任务",
  "tool_call.approval_decided": "完成人工审批",
  "tool_call.task_terminal": "采集任务结束",
  "lats.search_started": "开始 LATS 搜索",
  "lats.candidates_expanded": "扩展候选分支",
  "lats.candidates_evaluated": "评估候选分支",
  "lats.node_selected": "选择下一调查节点",
  "lats.action_proposed": "提出工具行动",
  "lats.awaiting_approval": "等待工具审批",
  "lats.action_blocked": "工具行动被拦截",
  "lats.simulation_started": "开始环境模拟",
  "lats.action_dispatched": "下发工具行动",
  "lats.observation_recorded": "记录环境观察",
  "lats.reflection_recorded": "记录反思与重规划",
  "lats.backpropagated": "回传路径价值",
  "lats.node_pruned": "剪枝无效分支",
  "lats.search_terminated": "结束 LATS 搜索",
  "lats.replay_snapshot_frozen": "冻结回放快照",
};

const ACTOR_LABELS = {
  SYSTEM: "系统",
  AGENT: "诊断 Agent",
  DIAGNOSIS_AGENT: "诊断 Agent",
  REPLAY_AGENT: "回放诊断 Agent",
  FROZEN_ENVIRONMENT: "冻结回放环境",
  USER: "用户",
  OPERATOR: "操作人员",
  PLANNER: "规划器",
  EVIDENCE_GATE: "证据门禁",
  TOOL_RUNTIME: "工具运行环境",
};

const TOOL_LABELS = {
  collect_sys_metrics: "采集系统指标",
  collect_database_diagnostics: "采集数据库诊断信息",
  start_perf_profile: "采集 CPU 火焰图",
  start_pyspy_profile: "采集 Python 调用栈",
  start_ebpf_io_profile: "采集 eBPF I/O 延迟",
  start_continuous_profile: "启动连续性能采集",
  collect_memory_profile: "采集内存剖析",
  start_jvm_profile: "启动 JVM 性能剖析",
  collect_go_profile: "采集 Go pprof 性能剖析",
  get_agent_status: "检查采集节点状态",
  // 采集物中可能保存的是 Collector/TaskKind 名，仍要用同一套中文主文案。
  sys_metrics: "系统指标采集",
  database_lock: "数据库锁等待诊断",
  perf_cpu: "perf CPU 采样",
  pyspy: "py-spy Python 采样",
  ebpf_io: "eBPF I/O 采样",
  continuous_perf: "连续性能采集",
  memory_smaps: "进程内存剖析",
  java_async: "JVM async-profiler 采样",
  go_pprof: "Go pprof 采样",
};

const EXACT_TEXT = new Map([
  [
    "Continuous profiling over an extended window captures the synchronous write/fsync call path in the python3.12 target that single-shot probes missed due to transient I/O activity.",
    "延长连续性能采集窗口，可以捕获 python3.12 目标中因瞬时 I/O 活动而被单次探针漏掉的同步 write/fsync 调用路径。",
  ],
  [
    "Single-shot probes (sys_metrics, eBPF, perf, pyspy) all returned insufficient evidence, possibly missing transient I/O activity. Continuous profiling over an extended window can capture the synchronous write call path that intermittent sampling missed.",
    "单次探针（sys_metrics、eBPF、perf、py-spy）均返回证据不足，可能漏掉了瞬时 I/O 活动。延长连续性能采集窗口，可捕获间歇采样遗漏的同步写调用路径。",
  ],
  [
    "Continuous profile captures the target blocked in synchronous write/fsync syscalls over an extended window",
    "在延长观测窗口内，连续性能采集发现目标持续阻塞在同步 write/fsync 系统调用中",
  ],
  [
    "Stack traces reveal the application code path issuing synchronous writes that single-shot probes missed",
    "调用栈显示了单次探针遗漏的应用同步写代码路径",
  ],
  [
    "I/O wait states appear consistently across the continuous sampling window",
    "整个连续采样窗口内持续出现 I/O 等待状态",
  ],
  [
    "Continuous profile shows no synchronous write activity and instead reveals a CPU-bound hot path",
    "连续性能采集没有发现同步写活动，反而显示出受 CPU 限制的热点路径",
  ],
  [
    "No I/O wait or blocked syscall states appear across the extended sampling window",
    "延长采样窗口内未出现 I/O 等待或系统调用阻塞状态",
  ],
  [
    "Continuous profiling confirms the target is consistently I/O-bound (blocked on I/O wait) rather than CPU-bound across the extended observation window.",
    "连续性能采集确认：在延长观测窗口内，目标始终受 I/O 限制（阻塞于 I/O 等待），而不是受 CPU 限制。",
  ],
  [
    "If the target is genuinely I/O-bound, continuous profiling should consistently show the process blocked on I/O rather than executing compute, confirming block device latency as the bottleneck over a longer observation window.",
    "如果目标确实受 I/O 限制，连续性能采集应持续显示进程阻塞于 I/O，而不是执行计算，从而在更长观测窗口内确认块设备延迟是瓶颈。",
  ],
  [
    "Memory pressure in the python3.12 target causes swap/page-cache I/O that manifests as elevated block device latency, explaining the I/O wait that write-path probes could not attribute.",
    "python3.12 目标的内存压力引发交换区/页缓存 I/O，表现为块设备延迟升高，这可以解释写路径探针无法归因的 I/O 等待。",
  ],
  [
    "The target's memory footprint is stable with no swap activity, ruling out memory pressure as the source of the observed I/O wait.",
    "目标的内存占用保持稳定且没有交换区活动，可排除内存压力是已观察到 I/O 等待的来源。",
  ],
  [
    "If memory is not the cause, the memory profile should show a stable footprint with no swap activity, ruling out memory pressure as the source of block device I/O and narrowing the diagnosis.",
    "如果内存不是原因，内存剖析应显示占用稳定且没有交换区活动，从而排除内存压力引发块设备 I/O，并缩小诊断范围。",
  ],
  [
    "All four single-shot probes (collect_sys_metrics, start_ebpf_io_profile, start_perf_profile, start_pyspy_profile) returned insufficient evidence, possibly missing transient I/O activity. Remaining viable allowed tool is start_continuous_profile. Extended-window continuous profiling may capture the synchronous write call path and confirm I/O-bound behavior that intermittent sampling missed.",
    "全部四个单次探针（collect_sys_metrics、start_ebpf_io_profile、start_perf_profile、start_pyspy_profile）均返回证据不足，可能漏掉了瞬时 I/O 活动。剩余可用且获准的工具是 start_continuous_profile；延长窗口的连续性能采集可能捕获同步写调用路径，并确认间歇采样遗漏的 I/O 瓶颈。",
  ],
  [
    "All I/O-focused probes (sys_metrics, eBPF, perf, pyspy, continuous) returned insufficient evidence. Memory pressure causing swap/page-cache I/O is an unexplored alternative that could manifest as block device latency without synchronous write call sites in the application code.",
    "所有面向 I/O 的探针（sys_metrics、eBPF、perf、py-spy、continuous）均返回证据不足。尚未探索的一种可能是：内存压力引发交换区/页缓存 I/O，即使应用代码中没有同步写调用点，也会表现为块设备延迟。",
  ],
  [
    "All I/O-focused probes (sys_metrics, eBPF, perf, pyspy, continuous) returned insufficient evidence. Remaining applicable allowed tool for a python3.12 target is collect_memory_profile (jvm/go profiles are runtime-mismatched). Investigating whether memory pressure/swap I/O underlies the block device latency that write-path probes could not attribute.",
    "所有面向 I/O 的探针（sys_metrics、eBPF、perf、py-spy、continuous）均返回证据不足。对 python3.12 目标，剩余适用且获准的工具是 collect_memory_profile（JVM/Go 剖析与运行时不匹配）。下一步调查内存压力/交换区 I/O 是否造成了写路径探针无法归因的块设备延迟。",
  ],
  [
    "PostgreSQL checkpoint initialization failed; using process-local memory",
    "PostgreSQL 会话保存初始化失败，已降级为当前进程内存",
  ],
  ["process-local only", "仅当前进程有效"],
]);

const PHRASE_REPLACEMENTS = [
  [/All four single-shot probes/gi, "全部四个单次探针"],
  [/All I\/O-focused probes/gi, "所有面向 I/O 的探针"],
  [/returned insufficient evidence/gi, "均返回证据不足"],
  [/possibly missing transient I\/O activity/gi, "可能漏掉了瞬时 I/O 活动"],
  [/Remaining viable allowed tool is/gi, "剩余可用且获准的工具是"],
  [/Extended-window continuous profiling/gi, "延长窗口的连续性能采集"],
  [/Continuous profiling/gi, "连续性能采集"],
  [/Continuous profile/gi, "连续性能采集"],
  [/single-shot probes/gi, "单次探针"],
  [/extended observation window/gi, "延长观测窗口"],
  [/extended sampling window/gi, "延长采样窗口"],
  [/transient I\/O activity/gi, "瞬时 I/O 活动"],
  [/Memory pressure/gi, "内存压力"],
  [/CPU-bound/gi, "受 CPU 限制"],
  [/I\/O-bound/gi, "受 I/O 限制"],
  [/insufficient evidence/gi, "证据不足"],
  [/Other\/Unknown/gi, "其他尚未识别的原因"],
  [/OTHER\/UNKNOWN/g, "其他尚未识别的原因"],
  [/tool call budget (?:is )?exhausted/gi, "工具调用预算已耗尽"],
  [/permission denied/gi, "权限不足"],
  [/request timed out/gi, "请求超时"],
  [/connection refused/gi, "连接被拒绝"],
  [/target process (?:was )?not found/gi, "未找到目标进程"],
  [/collector (?:is )?unavailable/gi, "采集器不可用"],
  [/task failed/gi, "采集任务失败"],
  [/policy denied/gi, "策略拒绝执行"],
];

function normalizedCode(value) {
  return String(value || "").trim().toUpperCase();
}

function isProtocolLike(value) {
  const text = String(value || "").trim();
  return /^[A-Za-z][A-Za-z0-9_.:/-]*$/.test(text);
}

function readableOrFallback(value, fallback) {
  const translated = chineseDiagnosticText(value, fallback);
  if (translated !== String(value || "").trim()) return translated;
  return isProtocolLike(value) ? fallback : translated;
}

export function isKnownDiagnosticStatus(value) {
  return Object.hasOwn(STATUS_LABELS, normalizedCode(value));
}

export function isKnownEvidenceRole(value) {
  return Object.hasOwn(EVIDENCE_ROLE_LABELS, normalizedCode(value));
}

export function isKnownEvidenceDecision(value) {
  return Object.hasOwn(EVIDENCE_DECISION_LABELS, normalizedCode(value));
}

export function isKnownPolicyDecision(value) {
  return Object.hasOwn(POLICY_DECISION_LABELS, normalizedCode(value));
}

export function isKnownVerificationStatus(value) {
  return Object.hasOwn(VERIFICATION_STATUS_LABELS, normalizedCode(value));
}

export function isKnownDiagnosticTool(value) {
  return Object.hasOwn(TOOL_LABELS, String(value || "").trim());
}

export function diagnosticStatusLabel(value, fallback = "暂无") {
  if (value == null || value === "") return fallback;
  const key = normalizedCode(value);
  return STATUS_LABELS[key] || readableOrFallback(value, "状态未知");
}

export function evidenceRoleLabel(value, fallback = "证据角色未知") {
  if (value == null || value === "") return fallback;
  return EVIDENCE_ROLE_LABELS[normalizedCode(value)] || readableOrFallback(value, fallback);
}

export function evidenceDecisionLabel(value, fallback = "门禁判定未知") {
  if (value == null || value === "") return fallback;
  return EVIDENCE_DECISION_LABELS[normalizedCode(value)] || readableOrFallback(value, fallback);
}

export function policyDecisionLabel(value, fallback = "策略判定未知") {
  if (value == null || value === "") return fallback;
  return POLICY_DECISION_LABELS[normalizedCode(value)] || readableOrFallback(value, fallback);
}

export function verificationStatusLabel(value, fallback = "验证状态未知") {
  if (value == null || value === "") return fallback;
  return VERIFICATION_STATUS_LABELS[normalizedCode(value)] || readableOrFallback(value, fallback);
}

export function diagnosticErrorText(value, fallback = "执行失败，请展开技术详情查看原始错误") {
  if (value == null || value === "") return fallback;
  const original = String(value).trim();
  const direct = ERROR_CODE_LABELS[normalizedCode(original)];
  if (direct) return direct;
  const translated = chineseDiagnosticText(original, fallback);
  // 未识别的英文报错只作为折叠技术详情展示，主界面保持可读的中文说明。
  if (!/[\u3400-\u9fff]/u.test(translated)) return fallback;
  return translated;
}

export function diagnosisEventLabel(value) {
  if (!value) return "诊断事件";
  return EVENT_LABELS[String(value)] || readableOrFallback(value, "未识别的诊断事件");
}

export function diagnosisActorLabel(value) {
  if (!value) return "系统";
  return ACTOR_LABELS[String(value).toUpperCase()] || readableOrFallback(value, "未知执行方");
}

export function diagnosticToolLabel(value) {
  if (!value) return "诊断工具";
  return TOOL_LABELS[String(value)] || "未知诊断工具";
}

export function skillPolicyLabel(value) {
  const key = String(value || "AUTO").toUpperCase();
  if (key === "AUTO") return "自动复用 Skill";
  if (key === "DISABLED") return "不使用 Skill";
  return "Skill 策略未知";
}

export function runtimeStatusLabel(value, degraded = false) {
  if (degraded) return "降级运行";
  const key = String(value || "").toUpperCase();
  if (key === "HEALTHY") return "健康";
  if (key === "DEGRADED") return "降级运行";
  if (key === "UNAVAILABLE") return "不可用";
  return diagnosticStatusLabel(value, "状态未知");
}

export function checkpointLabel(value) {
  const key = String(value || "").toLowerCase();
  if (!key) return "未返回";
  if (key === "postgres" || key === "postgresql") return "PostgreSQL 持久化";
  if (key === "memory" || key === "in_memory") return "内存临时保存";
  if (key === "sqlite") return "SQLite 持久化";
  return String(value);
}

export function chineseDiagnosticText(value, fallback = "暂无") {
  if (value == null || value === "") return fallback;
  if (typeof value !== "string" && typeof value !== "number") return fallback;
  const original = String(value).trim();
  if (!original) return fallback;
  const exact = EXACT_TEXT.get(original);
  if (exact) return exact;
  return PHRASE_REPLACEMENTS.reduce((text, [pattern, replacement]) => text.replace(pattern, replacement), original);
}

export function semanticDiagnosticKey(value) {
  const text = chineseDiagnosticText(value, "")
    .toLowerCase()
    .replace(/其他尚未识别的原因|other\s*\/\s*unknown/g, "unknown")
    .replace(/[\s\p{P}\p{S}]+/gu, "");
  return text || "empty";
}

export function dedupeDiagnosticRows(items = [], valueOf = (item) => item?.statement) {
  const seen = new Set();
  return (Array.isArray(items) ? items : []).filter((item) => {
    const key = semanticDiagnosticKey(valueOf(item));
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
