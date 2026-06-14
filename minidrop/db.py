import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


VALID_TRANSITIONS = {
    "PENDING": {"RUNNING", "FAILED"},
    "RUNNING": {"UPLOADING", "FAILED"},
    "UPLOADING": {"DONE", "FAILED"},
    "DONE": set(),
    "FAILED": set(),
}


class Store:
    def __init__(self, path="data/minidrop.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self._init()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init(self):
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents(
                  id TEXT PRIMARY KEY, hostname TEXT, version TEXT, online INTEGER,
                  last_seen REAL, metadata TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks(
                  id TEXT PRIMARY KEY, agent_id TEXT, pid INTEGER, duration INTEGER,
                  rate INTEGER, collector TEXT, status TEXT, reason TEXT,
                  created_at REAL, updated_at REAL, raw_data TEXT, result TEXT,
                  continuous INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS transitions(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, from_status TEXT,
                  to_status TEXT, reason TEXT NOT NULL, created_at REAL
                );
                CREATE TABLE IF NOT EXISTS audit(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, subject TEXT,
                  message TEXT, created_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_agent_status ON tasks(agent_id,status);
                CREATE INDEX IF NOT EXISTS idx_transitions_task ON transitions(task_id);
                """
            )

    @staticmethod
    def _rows(rows):
        return [dict(row) for row in rows]

    def create_task(self, agent_id, pid, duration, rate, collector, continuous=False):
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO tasks(id,agent_id,pid,duration,rate,collector,status,reason,
                   created_at,updated_at,continuous) VALUES(?,?,?,?,?,?,'PENDING',?,?,?,?)""",
                (task_id, agent_id, pid, duration, rate, collector, "task accepted", now, now, int(continuous)),
            )
            db.execute(
                "INSERT INTO transitions(task_id,from_status,to_status,reason,created_at) VALUES(?,NULL,'PENDING',?,?)",
                (task_id, "task accepted", now),
            )
        return self.get_task(task_id)

    def transition(self, task_id, status, reason):
        if not reason or not reason.strip():
            raise ValueError("transition reason is required")
        with self.lock, self.connect() as db:
            row = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            old = row["status"]
            if status not in VALID_TRANSITIONS[old]:
                raise ValueError(f"invalid transition {old} -> {status}")
            now = time.time()
            db.execute("UPDATE tasks SET status=?,reason=?,updated_at=? WHERE id=?", (status, reason, now, task_id))
            db.execute(
                "INSERT INTO transitions(task_id,from_status,to_status,reason,created_at) VALUES(?,?,?,?,?)",
                (task_id, old, status, reason, now),
            )
        return self.get_task(task_id)

    def heartbeat(self, agent_id, hostname, version, metadata):
        now = time.time()
        with self.lock, self.connect() as db:
            old = db.execute("SELECT online FROM agents WHERE id=?", (agent_id,)).fetchone()
            db.execute(
                """INSERT INTO agents(id,hostname,version,online,last_seen,metadata) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET hostname=excluded.hostname,version=excluded.version,
                   online=1,last_seen=excluded.last_seen,metadata=excluded.metadata""",
                (agent_id, hostname, version, 1, now, json.dumps(metadata)),
            )
            if not old or not old["online"]:
                db.execute(
                    "INSERT INTO audit(kind,subject,message,created_at) VALUES('AGENT_ONLINE',?,?,?)",
                    (agent_id, "agent connected or recovered", now),
                )

    def mark_offline(self, cutoff=30):
        now = time.time()
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT id FROM agents WHERE online=1 AND last_seen<?", (now - cutoff,)).fetchall()
            for row in rows:
                db.execute("UPDATE agents SET online=0 WHERE id=?", (row["id"],))
                db.execute(
                    "INSERT INTO audit(kind,subject,message,created_at) VALUES('AGENT_OFFLINE',?,?,?)",
                    (row["id"], f"no heartbeat for {cutoff}s", now),
                )
        return len(rows)

    def claim_task(self, agent_id):
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE agent_id=? AND status='PENDING' ORDER BY created_at LIMIT 1",
                (agent_id,),
            ).fetchone()
        if not row:
            return None
        return self.transition(row["id"], "RUNNING", "claimed by agent")

    def set_payload(self, task_id, field, payload):
        if field not in {"raw_data", "result"}:
            raise ValueError("invalid payload field")
        with self.lock, self.connect() as db:
            db.execute(f"UPDATE tasks SET {field}=?,updated_at=? WHERE id=?", (json.dumps(payload), time.time(), task_id))

    def get_task(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["transitions"] = self._rows(
                db.execute("SELECT * FROM transitions WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
            )
        for key in ("raw_data", "result"):
            item[key] = json.loads(item[key]) if item[key] else None
        return item

    def list_tasks(self):
        with self.connect() as db:
            return self._rows(db.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall())

    def continuous_window(self, agent_id, seconds=300):
        with self.connect() as db:
            rows = self._rows(db.execute(
                """SELECT * FROM tasks WHERE agent_id=? AND continuous=1 AND status='DONE'
                   AND updated_at>=? ORDER BY updated_at""",
                (agent_id, time.time() - seconds),
            ).fetchall())
        for row in rows:
            row["result"] = json.loads(row["result"]) if row["result"] else None
            row["raw_data"] = None
        return rows

    def list_agents(self):
        with self.connect() as db:
            return self._rows(db.execute("SELECT * FROM agents ORDER BY id").fetchall())

    def list_audit(self):
        with self.connect() as db:
            return self._rows(db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100").fetchall())
