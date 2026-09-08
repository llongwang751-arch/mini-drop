const PHASE_LABELS = {
  SELECTION: "选择节点",
  EXPANSION: "扩展分支",
  EVALUATION: "评估候选",
  ACTION_PROPOSED: "提出工具行动",
  AWAITING_APPROVAL: "等待人工审批",
  ACTION_BLOCKED: "行动被策略拦截",
  SIMULATION: "环境模拟 / 执行",
  ACTION: "执行行动",
  OBSERVATION: "接收观察",
  REFLECTION: "反思重规划",
  BACKPROPAGATION: "价值回传",
  BACKPROP: "价值回传",
  TERMINATED: "搜索结束",
};

const PHASE_GROUPS = {
  ACTION_PROPOSED: "ACTION",
  AWAITING_APPROVAL: "ACTION",
  ACTION_BLOCKED: "ACTION",
  SIMULATION: "ACTION",
  BACKPROP: "BACKPROPAGATION",
};

const TERMINATION_LABELS = {
  VERIFIED: "根因已通过证据验证",
  BUDGET_EXHAUSTED: "搜索预算已耗尽",
  NO_ELIGIBLE_CHILD: "没有可继续展开的候选",
  AWAITING_OBSERVATION: "等待工具观察结果",
  RUNNING: "仍在搜索",
  CANCELLED: "搜索已取消",
  FAILED: "搜索执行失败",
};

const EXECUTION_MODE_LABELS = {
  FULL_LATS: "完整 LATS（可回放/受控复现）",
  BUDGETED_LATS: "预算约束 LATS（实时环境不可回退）",
};

const ENVIRONMENT_LABELS = {
  FROZEN_REPLAY: "冻结回放",
  CONTROLLED_REPRODUCTION: "受控复现",
  LIVE_PROGRESSIVE: "真实时间推进",
};

const ROLLOUT_LABELS = {
  REPLAY_SIMULATION: "可重复回放模拟",
  CONTROLLED_RESET_REQUIRED: "分支比较前需要重置故障",
  REAL_TOOL_SINGLE_STEP_NO_ROLLBACK: "真实工具单步执行，不可回滚现场",
};

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasOwn(value, key) {
  return isRecord(value) && Object.prototype.hasOwnProperty.call(value, key);
}

function optionalNumber(value) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function optionalBoolean(value) {
  return typeof value === "boolean" ? value : null;
}

function optionalString(value) {
  if (value == null) return "";
  return String(value).trim();
}

function normalizeBudget(raw) {
  if (!isRecord(raw)) return null;
  const normalized = {
    maxIterations: optionalNumber(raw.max_iterations),
    maxToolCalls: optionalNumber(raw.max_tool_calls),
    maxDiagnosisRounds: optionalNumber(raw.max_diagnosis_rounds),
    usedIterations: optionalNumber(raw.used_iterations),
    usedToolCalls: optionalNumber(raw.used_tool_calls),
    usedDiagnosisRounds: optionalNumber(raw.used_diagnosis_rounds),
    remainingIterations: optionalNumber(raw.remaining_iterations),
    remainingToolCalls: optionalNumber(raw.remaining_tool_calls),
    remainingDiagnosisRounds: optionalNumber(raw.remaining_diagnosis_rounds),
    maxSimulations: optionalNumber(raw.max_simulations),
    usedSimulations: optionalNumber(raw.used_simulations),
    remainingSimulations: optionalNumber(raw.remaining_simulations),
  };
  return Object.values(normalized).some((value) => value != null) ? normalized : null;
}

function normalizeSelection(raw) {
  if (!isRecord(raw)) return null;
  const components = isRecord(raw.components) ? {
    q: optionalNumber(raw.components.q),
    exploration: optionalNumber(raw.components.exploration),
    priorBonus: optionalNumber(raw.components.prior_bonus),
    virtualLoss: optionalNumber(raw.components.virtual_loss),
  } : null;
  const normalized = {
    nodeId: optionalString(raw.node_id),
    score: optionalNumber(raw.score),
    reason: optionalString(raw.reason),
    components: components && Object.values(components).some((value) => value != null)
      ? components
      : null,
  };
  return normalized.nodeId || normalized.score != null || normalized.reason || normalized.components
    ? normalized
    : null;
}

function normalizeTermination(raw) {
  if (!isRecord(raw)) return null;
  const normalized = {
    stopped: optionalBoolean(raw.stopped),
    reason: optionalString(raw.reason).toUpperCase(),
    detail: optionalString(raw.detail),
  };
  return normalized.stopped != null || normalized.reason || normalized.detail ? normalized : null;
}

/**
 * Normalize the optional server-side LATS projection without manufacturing default
 * scores. Old diagnoses simply return null and continue using the existing UI.
 */
export function normalizeLatsSearch(raw, context = null) {
  if (!isRecord(raw)) return null;
  const knownKeys = [
    "algorithm",
    "algorithm_version",
    "execution_mode",
    "environment_semantics",
    "rollout_semantics",
    "phase",
    "iteration",
    "selected_node_id",
    "best_path_node_ids",
    "latest_selection",
    "budget",
    "termination",
    "candidate_count",
    "snapshot_id",
    "snapshot_digest",
    "reset_count",
    "semantics",
  ];
  if (!knownKeys.some((key) => hasOwn(raw, key))) return null;
  const semantics = isRecord(raw.semantics) ? raw.semantics : {};
  const snapshot = isRecord(raw.snapshot)
    ? raw.snapshot
    : isRecord(context?.snapshot) ? context.snapshot : {};
  return {
    algorithm: optionalString(raw.algorithm),
    algorithmVersion: optionalString(raw.algorithm_version),
    executionMode: optionalString(raw.execution_mode || semantics.execution_mode).toUpperCase(),
    environmentSemantics: optionalString(raw.environment_semantics || semantics.environment_semantics).toUpperCase(),
    rolloutSemantics: optionalString(raw.rollout_semantics || semantics.rollout_semantics).toUpperCase(),
    phase: optionalString(raw.phase).toUpperCase(),
    iteration: optionalNumber(raw.iteration),
    selectedNodeId: optionalString(raw.selected_node_id),
    bestPathNodeIds: Array.isArray(raw.best_path_node_ids)
      ? raw.best_path_node_ids.filter((item) => item != null).map(String)
      : [],
    latestSelection: normalizeSelection(raw.latest_selection),
    budget: normalizeBudget(raw.budget),
    termination: normalizeTermination(raw.termination),
    candidateCount: optionalNumber(raw.candidate_count),
    snapshotId: optionalString(raw.snapshot_id || semantics.snapshot_id || snapshot.snapshot_id || context?.snapshot_id),
    snapshotDigest: optionalString(raw.snapshot_digest || semantics.snapshot_digest || snapshot.snapshot_digest || context?.snapshot_digest),
    resetCount: optionalNumber(raw.reset_count ?? semantics.reset_count ?? snapshot.reset_count ?? context?.reset_count),
  };
}

/** Normalize metrics attached to a hypothesis node. Zero is a real value. */
export function normalizeLatsNodeMetrics(raw) {
  if (!isRecord(raw)) return null;
  const knownKeys = [
    "visits",
    "value_sum",
    "mean_value",
    "prior",
    "uct_score",
    "reward",
    "depth",
    "selected",
    "best_path",
    "pruned",
    "last_observation_summary",
    "last_reflection",
  ];
  if (!knownKeys.some((key) => hasOwn(raw, key))) return null;
  return {
    visits: optionalNumber(raw.visits),
    valueSum: optionalNumber(raw.value_sum),
    meanValue: optionalNumber(raw.mean_value),
    prior: optionalNumber(raw.prior),
    uctScore: optionalNumber(raw.uct_score),
    reward: optionalNumber(raw.reward),
    depth: optionalNumber(raw.depth),
    selected: optionalBoolean(raw.selected),
    bestPath: optionalBoolean(raw.best_path),
    pruned: optionalBoolean(raw.pruned),
    lastObservationSummary: optionalString(raw.last_observation_summary),
    lastReflection: optionalString(raw.last_reflection),
  };
}

export function latsPhaseLabel(phase) {
  const normalized = optionalString(phase).toUpperCase();
  return PHASE_LABELS[normalized] || normalized || "阶段未记录";
}

/** Map durable sub-phases to the seven compact steps shown in the cockpit. */
export function latsPhaseGroup(phase) {
  const normalized = optionalString(phase).toUpperCase();
  return PHASE_GROUPS[normalized] || normalized;
}

export function latsTerminationLabel(reason) {
  const normalized = optionalString(reason).toUpperCase();
  return TERMINATION_LABELS[normalized] || normalized || "停止条件未记录";
}

export function latsExecutionModeLabel(mode) {
  const normalized = optionalString(mode).toUpperCase();
  return EXECUTION_MODE_LABELS[normalized] || normalized || "LATS 模式未记录";
}

export function latsEnvironmentLabel(value) {
  const normalized = optionalString(value).toUpperCase();
  return ENVIRONMENT_LABELS[normalized] || normalized || "环境语义未记录";
}

export function latsRolloutLabel(value) {
  const normalized = optionalString(value).toUpperCase();
  return ROLLOUT_LABELS[normalized] || normalized || "Rollout 语义未记录";
}

export function formatLatsScore(value) {
  const parsed = optionalNumber(value);
  return parsed == null ? "未记录" : parsed.toFixed(3);
}
