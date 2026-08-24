import { DatabaseSync } from "node:sqlite";

const SCHEMA_VERSION = 2;

function parseJson(value) {
  return JSON.parse(value);
}

export class SidecarStateStore {
  constructor(path) {
    if (!path) throw new Error("MINI_DROP_PI_STATE_PATH is required");
    this.database = new DatabaseSync(path);
    try {
      this.database.exec("PRAGMA journal_mode=WAL");
      this.database.exec("PRAGMA synchronous=FULL");
      this.database.exec("PRAGMA busy_timeout=5000");
      this.database.exec("PRAGMA foreign_keys=ON");
      this._migrate();
    } catch (error) {
      this.database.close();
      throw error;
    }
  }

  _migrate() {
    this.database.exec(`
      CREATE TABLE IF NOT EXISTS schema_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL
      );
    `);
    const row = this.database.prepare(
      "SELECT schema_version FROM schema_metadata WHERE singleton = 1",
    ).get();
    const version = row ? Number(row.schema_version) : null;
    if (version !== null && version !== 1 && version !== SCHEMA_VERSION) {
      throw new Error(`unsupported Sidecar state schema: ${row.schema_version}`);
    }
    this.database.exec(`
      CREATE TABLE IF NOT EXISTS runtime_bindings (
        diagnosis_id TEXT PRIMARY KEY,
        runtime_generation INTEGER NOT NULL CHECK (runtime_generation >= 1),
        context_json TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS accepted_commands (
        diagnosis_id TEXT NOT NULL,
        client_command_id TEXT NOT NULL,
        runtime_generation INTEGER NOT NULL CHECK (runtime_generation >= 1),
        turn_id TEXT NOT NULL,
        request_json TEXT NOT NULL,
        accepted_json TEXT NOT NULL,
        PRIMARY KEY (diagnosis_id, client_command_id),
        UNIQUE (diagnosis_id, turn_id),
        FOREIGN KEY (diagnosis_id) REFERENCES runtime_bindings(diagnosis_id)
          ON DELETE CASCADE
      );
      CREATE TABLE IF NOT EXISTS runtime_turn_state (
        diagnosis_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        client_command_id TEXT NOT NULL,
        runtime_generation INTEGER NOT NULL CHECK (runtime_generation >= 1),
        lifecycle_status TEXT NOT NULL,
        next_event_seq INTEGER NOT NULL DEFAULT 1 CHECK (next_event_seq >= 1),
        terminal_status TEXT,
        final_message_json TEXT,
        PRIMARY KEY (diagnosis_id, turn_id),
        UNIQUE (diagnosis_id, client_command_id),
        FOREIGN KEY (diagnosis_id, client_command_id)
          REFERENCES accepted_commands(diagnosis_id, client_command_id)
          ON DELETE CASCADE
      );
      CREATE TABLE IF NOT EXISTS callback_outbox (
        callback_id TEXT PRIMARY KEY,
        diagnosis_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        runtime_generation INTEGER NOT NULL CHECK (runtime_generation >= 1),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
        callback_kind TEXT NOT NULL CHECK (callback_kind IN ('EVENT', 'TERMINAL')),
        path TEXT NOT NULL,
        body_json TEXT NOT NULL,
        delivery_status TEXT NOT NULL DEFAULT 'PENDING'
          CHECK (delivery_status IN ('PENDING', 'DELIVERING', 'DELIVERED', 'BLOCKED')),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        next_attempt_at INTEGER NOT NULL DEFAULT 0,
        lease_until INTEGER,
        last_error TEXT,
        UNIQUE (diagnosis_id, turn_id, ordinal),
        FOREIGN KEY (diagnosis_id, turn_id)
          REFERENCES runtime_turn_state(diagnosis_id, turn_id)
          ON DELETE CASCADE
      );
      CREATE INDEX IF NOT EXISTS ix_callback_outbox_due
        ON callback_outbox(delivery_status, next_attempt_at, lease_until);
    `);
    if (version === null) {
      this.database.prepare(
        "INSERT INTO schema_metadata (singleton, schema_version) VALUES (1, ?)",
      ).run(SCHEMA_VERSION);
      return;
    }
    if (version === 1) {
      this.database.prepare(
        "UPDATE schema_metadata SET schema_version = ? WHERE singleton = 1",
      ).run(SCHEMA_VERSION);
    }
  }

  close() {
    this.database.close();
  }

  health() {
    const row = this.database.prepare(
      "SELECT schema_version FROM schema_metadata WHERE singleton = 1",
    ).get();
    return Number(row?.schema_version) === SCHEMA_VERSION;
  }

  clearForTests() {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      this.database.exec(
        "DELETE FROM callback_outbox; DELETE FROM runtime_turn_state; " +
        "DELETE FROM accepted_commands; DELETE FROM runtime_bindings;",
      );
      this.database.exec("COMMIT");
    } catch (error) {
      try {
        this.database.exec("ROLLBACK");
      } catch {}
      throw error;
    }
  }

  getBinding(diagnosisId) {
    const row = this.database.prepare(`
      SELECT runtime_generation, context_json
      FROM runtime_bindings
      WHERE diagnosis_id = ?
    `).get(diagnosisId);
    if (!row) return null;
    return {
      generation: Number(row.runtime_generation),
      context: parseJson(row.context_json),
    };
  }

  bind(context) {
    const diagnosisId = context.diagnosis_id;
    const generation = Number(context.runtime_generation);
    const contextJson = JSON.stringify(context);
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const current = this.getBinding(diagnosisId);
      if (current && generation < current.generation) {
        this.database.exec("ROLLBACK");
        return { outcome: "stale", binding: current };
      }
      if (current && generation === current.generation) {
        if (JSON.stringify(current.context) !== contextJson) {
          this.database.exec("ROLLBACK");
          return { outcome: "conflict", binding: current };
        }
        this.database.exec("COMMIT");
        return { outcome: "existing", binding: current };
      }
      this.database.prepare(`
        INSERT INTO runtime_bindings (
          diagnosis_id, runtime_generation, context_json
        ) VALUES (?, ?, ?)
        ON CONFLICT(diagnosis_id) DO UPDATE SET
          runtime_generation = excluded.runtime_generation,
          context_json = excluded.context_json
      `).run(diagnosisId, generation, contextJson);
      if (current) {
        this.database.prepare(
          "DELETE FROM accepted_commands WHERE diagnosis_id = ?",
        ).run(diagnosisId);
      }
      this.database.exec("COMMIT");
      return {
        outcome: current ? "rotated" : "created",
        binding: { generation, context },
      };
    } catch (error) {
      try {
        this.database.exec("ROLLBACK");
      } catch {}
      throw error;
    }
  }

  getAccepted(diagnosisId, commandId) {
    const row = this.database.prepare(`
      SELECT request_json, accepted_json
      FROM accepted_commands
      WHERE diagnosis_id = ? AND client_command_id = ?
    `).get(diagnosisId, commandId);
    if (!row) return null;
    return {
      request: row.request_json,
      accepted: parseJson(row.accepted_json),
    };
  }

  accept({
    diagnosisId,
    commandId,
    generation,
    turnId,
    request,
    accepted,
    lifecycleStatus = "RUNNING",
  }) {
    const acceptedJson = JSON.stringify(accepted);
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const binding = this.getBinding(diagnosisId);
      if (!binding || binding.generation !== Number(generation)) {
        this.database.exec("ROLLBACK");
        return { outcome: "stale", binding };
      }
      const existing = this.getAccepted(diagnosisId, commandId);
      if (existing) {
        this.database.exec("COMMIT");
        return { outcome: "existing", record: existing };
      }
      this.database.prepare(`
        INSERT INTO accepted_commands (
          diagnosis_id, client_command_id, runtime_generation, turn_id,
          request_json, accepted_json
        ) VALUES (?, ?, ?, ?, ?, ?)
      `).run(
        diagnosisId,
        commandId,
        generation,
        turnId,
        request,
        acceptedJson,
      );
      this.database.prepare(`
        INSERT INTO runtime_turn_state (
          diagnosis_id, turn_id, client_command_id, runtime_generation,
          lifecycle_status
        ) VALUES (?, ?, ?, ?, ?)
      `).run(diagnosisId, turnId, commandId, generation, lifecycleStatus);
      this.database.exec("COMMIT");
      return { outcome: "created", record: { request, accepted } };
    } catch (error) {
      try {
        this.database.exec("ROLLBACK");
      } catch {}
      if (String(error?.message || error).includes("UNIQUE constraint failed")) {
        const existing = this.getAccepted(diagnosisId, commandId);
        if (existing) return { outcome: "existing", record: existing };
      }
      throw error;
    }
  }

  getTurnState(diagnosisId, turnId) {
    const row = this.database.prepare(`
      SELECT diagnosis_id, turn_id, client_command_id, runtime_generation,
             lifecycle_status, next_event_seq, terminal_status,
             final_message_json
      FROM runtime_turn_state
      WHERE diagnosis_id = ? AND turn_id = ?
    `).get(diagnosisId, turnId);
    if (!row) return null;
    return {
      diagnosisId: row.diagnosis_id,
      turnId: row.turn_id,
      commandId: row.client_command_id,
      generation: Number(row.runtime_generation),
      lifecycleStatus: row.lifecycle_status,
      nextEventSeq: Number(row.next_event_seq),
      terminalStatus: row.terminal_status || null,
      finalMessage: row.final_message_json === null
        ? null
        : parseJson(row.final_message_json),
    };
  }

  markTurnSealed(diagnosisId, turnId, lifecycleStatus = "SEALED") {
    const result = this.database.prepare(`
      UPDATE runtime_turn_state
      SET lifecycle_status = ?
      WHERE diagnosis_id = ? AND turn_id = ?
        AND lifecycle_status IN ('RUNNING', 'CALLBACK_PENDING', 'RECOVERY_BLOCKED')
    `).run(lifecycleStatus, diagnosisId, turnId);
    return Number(result.changes) === 1;
  }

  enqueueEvent({ diagnosisId, turnId, generation, eventType, payload }) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const turn = this.getTurnState(diagnosisId, turnId);
      if (!turn || turn.generation !== Number(generation)) {
        this.database.exec("ROLLBACK");
        return { outcome: "stale", turn };
      }
      if (turn.lifecycleStatus !== "RUNNING") {
        this.database.exec("ROLLBACK");
        return { outcome: "sealed", turn };
      }
      const eventSeq = turn.nextEventSeq;
      const callbackId = `callback:event:${diagnosisId}:${turnId}:${generation}:${eventSeq}`;
      const pathDiagnosis = encodeURIComponent(diagnosisId);
      const pathTurn = encodeURIComponent(turnId);
      const body = {
        runtime_session_id: `pi:${diagnosisId}:${generation}`,
        runtime_generation: Number(generation),
        events: [{
          event_id: `evt:${diagnosisId}:${turnId}:${generation}:${eventSeq}`,
          event_seq: eventSeq,
          event_type: eventType,
          payload,
        }],
      };
      this.database.prepare(`
        INSERT INTO callback_outbox (
          callback_id, diagnosis_id, turn_id, runtime_generation, ordinal,
          callback_kind, path, body_json
        ) VALUES (?, ?, ?, ?, ?, 'EVENT', ?, ?)
      `).run(
        callbackId,
        diagnosisId,
        turnId,
        generation,
        eventSeq,
        `/internal/runtime/v1/diagnoses/${pathDiagnosis}/turns/${pathTurn}/events`,
        JSON.stringify(body),
      );
      this.database.prepare(`
        UPDATE runtime_turn_state
        SET next_event_seq = next_event_seq + 1
        WHERE diagnosis_id = ? AND turn_id = ?
      `).run(diagnosisId, turnId);
      this.database.exec("COMMIT");
      return { outcome: "created", callbackId, eventSeq, body };
    } catch (error) {
      try {
        this.database.exec("ROLLBACK");
      } catch {}
      throw error;
    }
  }

  enqueueTerminal({ diagnosisId, turnId, generation, terminalStatus, finalMessage }) {
    const finalMessageJson = JSON.stringify(finalMessage || {});
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const turn = this.getTurnState(diagnosisId, turnId);
      if (!turn || turn.generation !== Number(generation)) {
        this.database.exec("ROLLBACK");
        return { outcome: "stale", turn };
      }
      if (turn.terminalStatus !== null) {
        const same = turn.terminalStatus === terminalStatus &&
          JSON.stringify(turn.finalMessage) === finalMessageJson;
        this.database.exec("COMMIT");
        return { outcome: same ? "existing" : "conflict", turn };
      }
      if (turn.lifecycleStatus !== "RUNNING") {
        this.database.exec("ROLLBACK");
        return { outcome: "sealed", turn };
      }
      const ordinal = turn.nextEventSeq;
      const callbackId = `callback:terminal:${diagnosisId}:${turnId}:${generation}`;
      const pathDiagnosis = encodeURIComponent(diagnosisId);
      const pathTurn = encodeURIComponent(turnId);
      const body = {
        runtime_session_id: `pi:${diagnosisId}:${generation}`,
        runtime_generation: Number(generation),
        terminal_status: terminalStatus,
        final_message: finalMessage || {},
      };
      this.database.prepare(`
        INSERT INTO callback_outbox (
          callback_id, diagnosis_id, turn_id, runtime_generation, ordinal,
          callback_kind, path, body_json
        ) VALUES (?, ?, ?, ?, ?, 'TERMINAL', ?, ?)
      `).run(
        callbackId,
        diagnosisId,
        turnId,
        generation,
        ordinal,
        `/internal/runtime/v1/diagnoses/${pathDiagnosis}/turns/${pathTurn}/terminal`,
        JSON.stringify(body),
      );
      this.database.prepare(`
        UPDATE runtime_turn_state
        SET lifecycle_status = 'CALLBACK_PENDING',
            next_event_seq = next_event_seq + 1,
            terminal_status = ?,
            final_message_json = ?
        WHERE diagnosis_id = ? AND turn_id = ?
      `).run(terminalStatus, finalMessageJson, diagnosisId, turnId);
      this.database.exec("COMMIT");
      return { outcome: "created", callbackId, ordinal, body };
    } catch (error) {
      try {
        this.database.exec("ROLLBACK");
      } catch {}
      throw error;
    }
  }

  claimNextCallback(now, leaseMilliseconds) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const row = this.database.prepare(`
        SELECT candidate.*
        FROM callback_outbox AS candidate
        WHERE (
          (candidate.delivery_status = 'PENDING' AND candidate.next_attempt_at <= ?)
          OR
          (candidate.delivery_status = 'DELIVERING' AND candidate.lease_until <= ?)
        )
        AND NOT EXISTS (
          SELECT 1
          FROM callback_outbox AS earlier
          WHERE earlier.diagnosis_id = candidate.diagnosis_id
            AND earlier.turn_id = candidate.turn_id
            AND earlier.ordinal < candidate.ordinal
            AND earlier.delivery_status != 'DELIVERED'
        )
        ORDER BY candidate.next_attempt_at, candidate.diagnosis_id,
                 candidate.turn_id, candidate.ordinal
        LIMIT 1
      `).get(now, now);
      if (!row) {
        this.database.exec("COMMIT");
        return null;
      }
      const leaseUntil = now + leaseMilliseconds;
      const result = this.database.prepare(`
        UPDATE callback_outbox
        SET delivery_status = 'DELIVERING',
            attempt_count = attempt_count + 1,
            lease_until = ?,
            last_error = NULL
        WHERE callback_id = ?
          AND (
            (delivery_status = 'PENDING' AND next_attempt_at <= ?)
            OR
            (delivery_status = 'DELIVERING' AND lease_until <= ?)
          )
      `).run(leaseUntil, row.callback_id, now, now);
      if (Number(result.changes) !== 1) {
        this.database.exec("ROLLBACK");
        return null;
      }
      this.database.exec("COMMIT");
      return {
        callbackId: row.callback_id,
        diagnosisId: row.diagnosis_id,
        turnId: row.turn_id,
        generation: Number(row.runtime_generation),
        ordinal: Number(row.ordinal),
        kind: row.callback_kind,
        path: row.path,
        body: parseJson(row.body_json),
        attemptCount: Number(row.attempt_count) + 1,
        leaseUntil,
      };
    } catch (error) {
      try {
        this.database.exec("ROLLBACK");
      } catch {}
      throw error;
    }
  }

  acknowledgeCallback(callbackId) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const row = this.database.prepare(`
        SELECT diagnosis_id, turn_id, callback_kind, delivery_status
        FROM callback_outbox
        WHERE callback_id = ?
      `).get(callbackId);
      if (!row) {
        this.database.exec("ROLLBACK");
        return null;
      }
      if (row.delivery_status !== "DELIVERED") {
        this.database.prepare(`
          UPDATE callback_outbox
          SET delivery_status = 'DELIVERED', lease_until = NULL,
              next_attempt_at = 0, last_error = NULL
          WHERE callback_id = ?
        `).run(callbackId);
      }
      if (row.callback_kind === "TERMINAL") {
        this.database.prepare(`
          UPDATE runtime_turn_state
          SET lifecycle_status = 'DELIVERED'
          WHERE diagnosis_id = ? AND turn_id = ?
        `).run(row.diagnosis_id, row.turn_id);
      }
      this.database.exec("COMMIT");
      return {
        diagnosisId: row.diagnosis_id,
        turnId: row.turn_id,
        terminal: row.callback_kind === "TERMINAL",
      };
    } catch (error) {
      try {
        this.database.exec("ROLLBACK");
      } catch {}
      throw error;
    }
  }

  retryCallback(callbackId, nextAttemptAt, error) {
    const result = this.database.prepare(`
      UPDATE callback_outbox
      SET delivery_status = 'PENDING', next_attempt_at = ?,
          lease_until = NULL, last_error = ?
      WHERE callback_id = ? AND delivery_status = 'DELIVERING'
    `).run(nextAttemptAt, String(error), callbackId);
    return Number(result.changes) === 1;
  }

  blockCallback(callbackId, error) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const row = this.database.prepare(`
        SELECT diagnosis_id, turn_id
        FROM callback_outbox
        WHERE callback_id = ?
      `).get(callbackId);
      if (!row) {
        this.database.exec("ROLLBACK");
        return false;
      }
      this.database.prepare(`
        UPDATE callback_outbox
        SET delivery_status = 'BLOCKED', lease_until = NULL, last_error = ?
        WHERE callback_id = ?
      `).run(String(error), callbackId);
      this.database.prepare(`
        UPDATE runtime_turn_state
        SET lifecycle_status = 'RECOVERY_BLOCKED'
        WHERE diagnosis_id = ? AND turn_id = ?
          AND lifecycle_status != 'DELIVERED'
      `).run(row.diagnosis_id, row.turn_id);
      this.database.exec("COMMIT");
      return true;
    } catch (error) {
      try {
        this.database.exec("ROLLBACK");
      } catch {}
      throw error;
    }
  }

  nextCallbackDueAt() {
    const row = this.database.prepare(`
      SELECT MIN(
        CASE WHEN delivery_status = 'DELIVERING'
          THEN lease_until ELSE next_attempt_at END
      ) AS due_at
      FROM callback_outbox
      WHERE delivery_status IN ('PENDING', 'DELIVERING')
    `).get();
    return row?.due_at === null || row?.due_at === undefined
      ? null
      : Number(row.due_at);
  }

  liveTurn(diagnosisId, generation) {
    const row = this.database.prepare(`
      SELECT turn_id, lifecycle_status
      FROM runtime_turn_state
      WHERE diagnosis_id = ? AND runtime_generation = ?
        AND lifecycle_status IN ('RUNNING', 'CALLBACK_PENDING', 'RECOVERY_BLOCKED')
      ORDER BY rowid
      LIMIT 1
    `).get(diagnosisId, generation);
    if (!row) return null;
    return { turnId: row.turn_id, lifecycleStatus: row.lifecycle_status };
  }

  recoverOrphanedTurns() {
    const rows = this.database.prepare(`
      SELECT diagnosis_id, turn_id, runtime_generation
      FROM runtime_turn_state
      WHERE lifecycle_status = 'RUNNING'
      ORDER BY diagnosis_id, turn_id
    `).all();
    const recovered = [];
    for (const row of rows) {
      const result = this.enqueueTerminal({
        diagnosisId: row.diagnosis_id,
        turnId: row.turn_id,
        generation: Number(row.runtime_generation),
        terminalStatus: "FAILED",
        finalMessage: {
          text: "Pi Runtime process restarted before prompt settlement",
          recovery_reason: "SIDECAR_RESTART",
        },
      });
      if (result.outcome === "created") {
        recovered.push({ diagnosisId: row.diagnosis_id, turnId: row.turn_id });
      }
    }
    return recovered;
  }
}
