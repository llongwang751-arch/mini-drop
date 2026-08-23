import {
  createAgentSession,
  createExtensionRuntime,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
import { buildToolCatalog } from "./tools.mjs";

const RUNTIME_VERSION = "pi-0.83.0";
const SYSTEM_PROMPT =
  "You are the Mini-Drop AI Investigator. Use only registered diagnosis and Evidence projections. " +
  "Never use shell or file access. Never fabricate Evidence. If Evidence is insufficient, state the " +
  "precise Evidence Gap and abstain. Cite evidence_id and projection_hash for every factual claim.";
const EMPTY_DIAGNOSTICS = { diagnostics: [] };

class LockedResourceLoader {
  constructor() {
    this.extensions = {
      extensions: [],
      errors: [],
      runtime: createExtensionRuntime(),
    };
  }

  getExtensions() {
    return this.extensions;
  }

  getSkills() {
    return { skills: [], ...EMPTY_DIAGNOSTICS };
  }

  getPrompts() {
    return { prompts: [], ...EMPTY_DIAGNOSTICS };
  }

  getThemes() {
    return { themes: [], ...EMPTY_DIAGNOSTICS };
  }

  getAgentsFiles() {
    return { agentsFiles: [] };
  }

  getSystemPrompt() {
    return SYSTEM_PROMPT;
  }

  getSystemPromptSource() {
    return undefined;
  }

  getAppendSystemPrompt() {
    return [];
  }

  getAppendSystemPromptSources() {
    return [];
  }

  extendResources() {}

  async reload() {}
}

const PERSISTED_EVENT_TYPES = new Set([
  "message_start",
  "message_end",
  "tool_execution_start",
  "tool_execution_end",
  "turn_start",
  "turn_end",
  "agent_start",
  "agent_end",
  "agent_settled",
]);

function immutableTurnRequest(turn) {
  return JSON.stringify({
    diagnosis_id: turn.diagnosis_id,
    runtime_generation: turn.runtime_generation,
    message: turn.message,
    references: turn.references || [],
    requested_mode: turn.requested_mode ?? null,
    client_command_id: turn.client_command_id,
  });
}

export class RuntimeConflict extends Error {
  constructor(message, status = 409) {
    super(message);
    this.status = status;
  }
}

export class RuntimeManager {
  constructor({ modelRuntime, internalBase, sessionFactory } = {}) {
    this.modelRuntime = modelRuntime;
    this.internalBase = internalBase || "http://127.0.0.1:8191";
    this.sessionFactory = sessionFactory || null;
    this.sessions = new Map();
    this.acceptedCommands = new Map();
  }

  async ensureModelRuntime() {
    if (this.modelRuntime) return this.modelRuntime;
    const { ModelRuntime } = await import("@earendil-works/pi-coding-agent");
    this.modelRuntime = await ModelRuntime.create({
      modelsPath: null,
      allowModelNetwork: false,
    });
    const provider = process.env.MINI_DROP_PI_MODEL_PROVIDER || "deepseek";
    const modelId = process.env.MINI_DROP_PI_MODEL || "deepseek-v4-flash";
    const apiKey = process.env.DEEPSEEK_API_KEY || process.env.MINI_DROP_AI_API_KEY || "";
    if (apiKey && typeof this.modelRuntime.setRuntimeApiKey === "function") {
      await this.modelRuntime.setRuntimeApiKey(provider, apiKey);
    }
    this.selectedModel = typeof this.modelRuntime.getModel === "function"
      ? this.modelRuntime.getModel(provider, modelId)
      : undefined;
    if (!this.selectedModel) {
      throw new RuntimeConflict(
        `configured Pi model is unavailable: ${provider}/${modelId}`,
        503,
      );
    }
    return this.modelRuntime;
  }

  async _createSession(context) {
    if (this.sessionFactory) return this.sessionFactory(context);
    await this.ensureModelRuntime();
    const tools = buildToolCatalog({
      diagnosisId: context.diagnosis_id,
      internalBase: this.internalBase,
    });
    const { session } = await createAgentSession({
      modelRuntime: this.modelRuntime,
      model: this.selectedModel,
      thinkingLevel: process.env.MINI_DROP_PI_THINKING_LEVEL || "high",
      noTools: "all",
      tools: tools.map((tool) => tool.name),
      customTools: tools,
      sessionManager: SessionManager.inMemory(),
      resourceLoader: new LockedResourceLoader(),
    });
    return session;
  }

  async startOrResume(context) {
    const diagnosisId = context?.diagnosis_id;
    const generation = Number(context?.runtime_generation);
    if (!diagnosisId || !Number.isInteger(generation) || generation < 1) {
      throw new RuntimeConflict("invalid diagnosis runtime context", 400);
    }
    const existing = this.sessions.get(diagnosisId);
    if (existing) {
      if (generation < existing.generation) {
        throw new RuntimeConflict("stale runtime generation");
      }
      if (generation === existing.generation) {
        existing.context = context;
        return this._binding(diagnosisId, existing);
      }
      const activeTurn = existing.activeTurnId === null
        ? null
        : existing.turns.get(existing.activeTurnId) || null;
      for (const turn of existing.turns.values()) {
        turn.sealed = true;
        turn.cancelled = true;
        this._releaseActiveTurn(existing, turn);
      }
      if (activeTurn) await this._abortTurn(existing, activeTurn);
      if (typeof existing.session.dispose === "function") existing.session.dispose();
      this.sessions.delete(diagnosisId);
      for (const key of this.acceptedCommands.keys()) {
        if (key.startsWith(`${diagnosisId}\u0000`)) this.acceptedCommands.delete(key);
      }
    }
    const session = await this._createSession(context);
    const entry = {
      session,
      generation,
      context,
      turns: new Map(),
      activeTurnId: null,
      lastEventSeq: 0,
      lastError: "",
    };
    this.sessions.set(diagnosisId, entry);
    return this._binding(diagnosisId, entry);
  }

  _binding(diagnosisId, entry) {
    return {
      diagnosis_id: diagnosisId,
      runtime_session_id: `pi:${diagnosisId}:${entry.generation}`,
      runtime_generation: entry.generation,
      runtime_type: "pi",
      runtime_version: RUNTIME_VERSION,
    };
  }

  _entry(diagnosisId, generation) {
    const entry = this.sessions.get(diagnosisId);
    if (!entry) throw new RuntimeConflict("runtime binding does not exist", 404);
    if (entry.generation !== Number(generation)) {
      throw new RuntimeConflict("stale runtime generation");
    }
    return entry;
  }

  _commandKey(diagnosisId, commandId) {
    return `${diagnosisId}\u0000${commandId}`;
  }

  getAcceptedTurn(diagnosisId, commandId) {
    return this.acceptedCommands.get(this._commandKey(diagnosisId, commandId))?.accepted || null;
  }

  async submitTurn(diagnosisId, envelope) {
    const turn = envelope?.turn;
    if (!turn || turn.diagnosis_id !== diagnosisId || !turn.turn_id || !turn.client_command_id) {
      throw new RuntimeConflict("invalid turn identity", 400);
    }
    const entry = this._entry(diagnosisId, turn.runtime_generation);
    const commandKey = this._commandKey(diagnosisId, turn.client_command_id);
    const existing = this.acceptedCommands.get(commandKey);
    if (existing) {
      if (existing.request !== immutableTurnRequest(turn)) {
        throw new RuntimeConflict("client_command_id already used for a different request");
      }
      return existing.accepted;
    }
    if (entry.turns.has(turn.turn_id)) {
      throw new RuntimeConflict("turn_id already exists");
    }
    if (envelope.shadow !== true && entry.activeTurnId !== null) {
      throw new RuntimeConflict("another model turn is active for this diagnosis");
    }

    const turnContext = {
      diagnosisId,
      turnId: turn.turn_id,
      generation: entry.generation,
      eventSeq: 0,
      sealed: false,
      cancelled: false,
      abort: typeof entry.session.abort === "function"
        ? () => entry.session.abort()
        : null,
      unsubscribe: null,
    };
    entry.turns.set(turn.turn_id, turnContext);
    const accepted = {
      turn_id: turn.turn_id,
      accepted: true,
      mode: envelope.shadow === true ? "pi_shadow" : "pi",
      detail: envelope.shadow === true
        ? "accepted without model execution"
        : "accepted by Pi Runtime",
    };
    this.acceptedCommands.set(commandKey, {
      request: immutableTurnRequest(turn),
      accepted,
    });

    if (envelope.shadow !== true) {
      entry.activeTurnId = turn.turn_id;
      const observe = (event) => {
        void this._forwardEvent(entry, turnContext, event);
      };
      const unsubscribe = entry.session.subscribe(observe);
      turnContext.unsubscribe = typeof unsubscribe === "function" ? unsubscribe : null;
      const contextBlock = JSON.stringify({
        diagnosis_id: diagnosisId,
        runtime_generation: entry.generation,
        context_snapshot_id: entry.context.context_snapshot_id ?? null,
        case_id: entry.context.case_id ?? null,
        case_goal: entry.context.case_goal || "",
        target_scope: entry.context.target_scope || {},
        references: turn.references || [],
      });
      try {
        const promptRun = entry.session.prompt(
          `[DiagnosisContext]\n${contextBlock}\n\n[User]\n${turn.message}`,
          { expandPromptTemplates: false, source: "rpc" },
        );
        Promise.resolve(promptRun).catch((error) => {
          entry.lastError = `prompt failed: ${String(error)}`;
        }).finally(() => {
          this._releaseActiveTurn(entry, turnContext);
        });
      } catch (error) {
        entry.lastError = `prompt failed: ${String(error)}`;
        this._releaseActiveTurn(entry, turnContext);
      }
    }
    return accepted;
  }

  _releaseActiveTurn(entry, turnContext) {
    if (turnContext.unsubscribe) {
      turnContext.unsubscribe();
      turnContext.unsubscribe = null;
    }
    if (entry.activeTurnId === turnContext.turnId) entry.activeTurnId = null;
  }

  async _abortTurn(entry, turnContext) {
    if (!turnContext.abort) return;
    try {
      await turnContext.abort();
    } catch (error) {
      entry.lastError = `abort failed: ${String(error)}`;
    }
  }

  state(diagnosisId) {
    const entry = this.sessions.get(diagnosisId);
    if (!entry) throw new RuntimeConflict("runtime binding does not exist", 404);
    return {
      diagnosis_id: diagnosisId,
      runtime_session_id: `pi:${diagnosisId}:${entry.generation}`,
      runtime_generation: entry.generation,
      status: entry.activeTurnId === null ? "READY" : "RUNNING",
      active_turn_id: entry.activeTurnId,
      last_error: entry.lastError || null,
    };
  }

  sealTurn(diagnosisId, turnId) {
    const entry = this.sessions.get(diagnosisId);
    if (!entry) throw new RuntimeConflict("runtime binding does not exist", 404);
    const turn = entry.turns.get(turnId);
    if (!turn) throw new RuntimeConflict("runtime turn does not exist", 404);
    turn.sealed = true;
    this._releaseActiveTurn(entry, turn);
    return { sealed: true };
  }

  async cancelTurn(diagnosisId, turnId) {
    const entry = this.sessions.get(diagnosisId);
    if (!entry) throw new RuntimeConflict("runtime binding does not exist", 404);
    const turn = entry.turns.get(turnId);
    if (!turn) throw new RuntimeConflict("runtime turn does not exist", 404);
    turn.cancelled = true;
    turn.sealed = true;
    this._releaseActiveTurn(entry, turn);
    await this._abortTurn(entry, turn);
    return { sealed: true, cancelled: true };
  }

  _auditProjection(event) {
    const stripThinking = (value) => {
      if (Array.isArray(value)) {
        return value
          .filter((item) => item?.type !== "thinking")
          .map(stripThinking);
      }
      if (value && typeof value === "object") {
        const copy = { ...value };
        delete copy.thinking;
        delete copy.thinkingSignature;
        if (Array.isArray(copy.content)) copy.content = stripThinking(copy.content);
        return copy;
      }
      return value;
    };
    const projection = {};
    for (const key of ["text", "toolCallId", "toolName", "message", "content", "role"]) {
      if (event[key] !== undefined) projection[key] = stripThinking(event[key]);
    }
    const encoded = JSON.stringify(projection);
    return JSON.parse(encoded.length <= 4000 ? encoded : JSON.stringify({ truncated: encoded.slice(0, 4000) }));
  }

  async _forwardEvent(entry, turnContext, event) {
    if (turnContext.sealed || turnContext.cancelled) return;
    if (!event || typeof event.type !== "string" || event.type.startsWith("thinking")) return;
    if (!PERSISTED_EVENT_TYPES.has(event.type)) return;
    const live = this.sessions.get(turnContext.diagnosisId);
    if (live !== entry || live.generation !== turnContext.generation) return;
    const token = process.env.MINI_DROP_PI_INTERNAL_TOKEN || "";
    if (!token) {
      entry.lastError = "event forward disabled: internal token is required";
      return;
    }
    turnContext.eventSeq += 1;
    const eventSeq = turnContext.eventSeq;
    const pathDiagnosis = encodeURIComponent(turnContext.diagnosisId);
    const pathTurn = encodeURIComponent(turnContext.turnId);
    try {
      const response = await fetch(
        `${this.internalBase}/internal/runtime/v1/diagnoses/${pathDiagnosis}/turns/${pathTurn}/events`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Internal-Token": token,
          },
          body: JSON.stringify({
            runtime_generation: turnContext.generation,
            events: [{
              event_id: `evt:${turnContext.diagnosisId}:${turnContext.turnId}:${turnContext.generation}:${eventSeq}`,
              event_seq: eventSeq,
              event_type: event.type,
              payload: this._auditProjection(event),
            }],
          }),
        },
      );
      if (!response.ok) entry.lastError = `event forward failed: ${response.status}`;
    } catch (error) {
      entry.lastError = `event forward failed: ${String(error)}`;
    }
  }
}
