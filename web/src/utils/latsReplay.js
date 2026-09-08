function firstValue(sources, getter) {
  for (const source of sources) {
    const value = getter(source);
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function upper(value) {
  return String(value || "").trim().toUpperCase();
}

/**
 * Read replay semantics from either a diagnosis list row, its detail response,
 * or the exploration-tree projection. Older records simply remain ordinary
 * diagnoses; the UI never infers FULL_LATS from a REPLAY label alone.
 */
export function getFrozenReplayMeta(...sources) {
  const records = sources.filter(Boolean);
  const targets = records.flatMap((record) => [
    record?.target,
    record?.target_json,
  ]).filter(Boolean);
  const nestedSearches = records.flatMap((record) => [
    record?.search,
    record?.explorationTree?.search,
    record?.exploration_tree?.search,
    record?.snapshot?.search,
  ]).filter(Boolean);
  const targetMetadata = targets.flatMap((target) => [
    target,
    target?.replay,
    target?.snapshot,
  ]).filter(Boolean);
  const semanticSources = [
    ...records,
    ...targets,
    ...targetMetadata,
    ...records.map((record) => record?.showcase).filter(Boolean),
    ...nestedSearches,
  ];
  const targetKind = upper(firstValue([
    ...targets,
    ...records.map((record) => record?.showcase).filter(Boolean),
    ...records,
  ], (source) => (
    source?.kind || source?.showcase_kind || source?.target_kind
  )));
  const mode = upper(firstValue(semanticSources, (source) => source?.mode || source?.session_mode));
  const executionMode = upper(firstValue(semanticSources, (source) => (
    source?.execution_mode || source?.semantics?.execution_mode
  )));
  const environmentSemantics = upper(firstValue(semanticSources, (source) => (
    source?.environment_semantics || source?.semantics?.environment_semantics
  )));
  const frozen = firstValue(semanticSources, (source) => (
    source?.frozen ?? source?.snapshot?.frozen ?? source?.semantics?.frozen_observations
  ));
  const snapshotId = String(firstValue(semanticSources, (source) => (
    source?.snapshot_id || source?.snapshot?.snapshot_id
  )) || "");
  const snapshotDigest = String(firstValue(semanticSources, (source) => (
    source?.snapshot_digest || source?.snapshot?.snapshot_digest
  )) || "");
  const hasFrozenShowcaseTarget = targetKind === "FROZEN_LATS_SHOWCASE";
  const isReplaySession = hasFrozenShowcaseTarget || mode === "REPLAY" || environmentSemantics === "FROZEN_REPLAY";
  const isFrozenReplay = hasFrozenShowcaseTarget || environmentSemantics === "FROZEN_REPLAY"
    || (isReplaySession && executionMode === "FULL_LATS" && frozen !== false);
  return {
    targetKind,
    mode,
    executionMode,
    environmentSemantics,
    snapshotId,
    snapshotDigest,
    frozen: frozen === true,
    isReplaySession,
    isFrozenReplay,
  };
}
