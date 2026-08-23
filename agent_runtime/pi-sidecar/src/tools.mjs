import { Type } from "typebox";

const READ_ONLY_TOOL_NAMES = new Set([
  "get_diagnosis_snapshot",
  "list_diagnosis_evidence",
  "get_evidence_projection",
  "compare_evidence",
  "get_evidence_gaps",
  "evaluate_hypotheses",
]);

const MAX_PROJECTION_BYTES = 131072;
const RESPONSE_KEYS = new Set(["ok", "data"]);
const EVIDENCE_ID = Type.String({ minLength: 1, maxLength: 128 });
const PROJECTION_KIND = Type.Union([
  Type.Literal("identity"),
  Type.Literal("signal"),
  Type.Literal("context"),
  Type.Literal("quality"),
  Type.Literal("provenance"),
  Type.Literal("claims"),
]);
const COMPARISON_DIMENSION = Type.Union([
  Type.Literal("signal"),
  Type.Literal("target"),
  Type.Literal("window"),
  Type.Literal("quality"),
  Type.Literal("source"),
]);

function validateSuccessEnvelope(payload, name) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error(`tool ${name} returned an invalid response envelope`);
  }
  const keys = Object.keys(payload);
  if (keys.length !== 2 || keys.some((key) => !RESPONSE_KEYS.has(key))) {
    throw new Error(`tool ${name} returned an invalid response envelope`);
  }
  if (payload.ok !== true || !("data" in payload)) {
    throw new Error(`tool ${name} returned a rejected response envelope`);
  }
  return payload.data;
}

async function parseToolResponse(resp, name) {
  let payload;
  try {
    payload = await resp.json();
  } catch {
    throw new Error(`tool ${name} returned invalid JSON`);
  }
  if (!resp.ok) {
    throw new Error(`tool ${name} failed: HTTP ${resp.status}`);
  }
  return validateSuccessEnvelope(payload, name);
}

function renderToolData(data, name) {
  let rendered;
  try {
    rendered = JSON.stringify(data);
  } catch {
    throw new Error(`tool ${name} returned non-serializable data`);
  }
  if (rendered === undefined) {
    throw new Error(`tool ${name} returned non-serializable data`);
  }
  const projectionBytes = Buffer.byteLength(rendered, "utf8");
  if (projectionBytes > MAX_PROJECTION_BYTES) {
    throw new Error(
      `tool ${name} projection exceeds ${MAX_PROJECTION_BYTES} UTF-8 bytes: ${projectionBytes}`,
    );
  }
  return { rendered, projectionBytes };
}

function makeInternalTool(name, label, description, parameters, internalPath, diagnosisId) {
  return {
    name,
    label,
    description,
    parameters,
    execute: async (_toolCallId, params) => {
      const token = process.env.MINI_DROP_PI_INTERNAL_TOKEN || "";
      if (!token) {
        throw new Error("MINI_DROP_PI_INTERNAL_TOKEN is required for diagnosis tools");
      }
      const resp = await fetch(internalPath, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Internal-Token": token,
        },
        body: JSON.stringify({
          ...params,
          diagnosis_id: diagnosisId,
          tool: name,
        }),
      });
      const data = await parseToolResponse(resp, name);
      const { rendered, projectionBytes } = renderToolData(data, name);
      return {
        content: [{ type: "text", text: rendered }],
        details: { projection_bytes: projectionBytes },
      };
    },
  };
}

export function buildToolCatalog({
  diagnosisId,
  internalBase = "http://127.0.0.1:8191",
} = {}) {
  if (!diagnosisId) throw new Error("diagnosisId is required");
  const base = `${internalBase}/internal/agent/tools`;
  return [
    makeInternalTool(
      "get_diagnosis_snapshot",
      "Get Diagnosis Snapshot",
      "Return the current diagnosis goal, scope, revisions and evidence inventory.",
      Type.Object({}, { additionalProperties: false }),
      `${base}/diagnosis-snapshot`,
      diagnosisId,
    ),
    makeInternalTool(
      "list_diagnosis_evidence",
      "List Diagnosis Evidence",
      "List active canonical evidence and projection hashes for this diagnosis.",
      Type.Object(
        {
          filters: Type.Optional(Type.Object({
            source_type: Type.Optional(Type.String({ minLength: 1, maxLength: 64 })),
            source_system: Type.Optional(Type.String({ minLength: 1, maxLength: 128 })),
            evidence_role: Type.Optional(Type.String({ minLength: 1, maxLength: 32 })),
          }, { additionalProperties: false })),
          cursor: Type.Optional(Type.String({ minLength: 1, maxLength: 128 })),
        },
        { additionalProperties: false },
      ),
      `${base}/list-diagnosis-evidence`,
      diagnosisId,
    ),
    makeInternalTool(
      "get_evidence_projection",
      "Get Evidence Projection",
      "Expand bounded projections for explicitly selected evidence.",
      Type.Object(
        {
          evidence_ids: Type.Array(EVIDENCE_ID, { minItems: 1, maxItems: 32 }),
          projection_kinds: Type.Optional(Type.Array(PROJECTION_KIND, { maxItems: 6 })),
          max_bytes: Type.Optional(Type.Integer({ minimum: 1, maximum: MAX_PROJECTION_BYTES })),
        },
        { additionalProperties: false },
      ),
      `${base}/get-evidence-projection`,
      diagnosisId,
    ),
    makeInternalTool(
      "compare_evidence",
      "Compare Evidence",
      "Compare selected evidence by signal, target, window and quality.",
      Type.Object(
        {
          evidence_ids: Type.Array(EVIDENCE_ID, { minItems: 2, maxItems: 32 }),
          dimensions: Type.Optional(Type.Array(COMPARISON_DIMENSION, { maxItems: 5 })),
        },
        { additionalProperties: false },
      ),
      `${base}/compare-evidence`,
      diagnosisId,
    ),
    makeInternalTool(
      "get_evidence_gaps",
      "Get Evidence Gaps",
      "Read current unresolved evidence gaps for this diagnosis.",
      Type.Object({}, { additionalProperties: false }),
      `${base}/get-evidence-gaps`,
      diagnosisId,
    ),
    makeInternalTool(
      "evaluate_hypotheses",
      "Evaluate Hypotheses",
      "Run deterministic hypothesis evaluation over current verified evidence.",
      Type.Object({}, { additionalProperties: false }),
      `${base}/evaluate-hypotheses`,
      diagnosisId,
    ),
  ];
}

export const ALLOWED_TOOL_NAMES = [...READ_ONLY_TOOL_NAMES];
