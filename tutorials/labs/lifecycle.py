"""Real temporary SQLite transactions over explicitly synthetic task records.

The SQL illustrates persistence boundaries. It does not reproduce PostgreSQL
row locking, production schemas, HTTP authorization or distributed failures.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from .helpers import check


@contextmanager
def teaching_database():
    with tempfile.TemporaryDirectory(prefix="drop-course-") as directory:
        connection = sqlite3.connect(Path(directory) / "synthetic.sqlite")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            CREATE TABLE tasks(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, current_attempt INTEGER NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('CREATED','RUNNING','SUCCESS','CANCELED')));
            CREATE TABLE attempts(
              task_id TEXT REFERENCES tasks(id), number INTEGER NOT NULL,
              status TEXT NOT NULL, PRIMARY KEY(task_id,number));
            CREATE TABLE events(
              id INTEGER PRIMARY KEY, task_id TEXT REFERENCES tasks(id),
              attempt INTEGER NOT NULL, kind TEXT NOT NULL);
            CREATE TABLE outbox(
              id INTEGER PRIMARY KEY, task_id TEXT REFERENCES tasks(id),
              attempt INTEGER NOT NULL, published INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE jobs(
              id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id),
              collection_attempt INTEGER NOT NULL, run INTEGER NOT NULL DEFAULT 0,
              owner TEXT, lease_until INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending');
        """)
        try:
            yield connection
        finally:
            connection.close()


def submit(connection, task_id="lesson-task", name="synthetic task", fail_after=None):
    with connection:
        connection.execute("INSERT INTO tasks VALUES (?,?,1,'CREATED')", (task_id, name))
        connection.execute("INSERT INTO attempts VALUES (?,1,'CREATED')", (task_id,))
        connection.execute("INSERT INTO events(task_id,attempt,kind) VALUES (?,1,'CREATED')", (task_id,))
        if fail_after == "event":
            raise RuntimeError("Synthetic failure before Outbox insertion")
        connection.execute("INSERT INTO outbox(task_id,attempt) VALUES (?,1)", (task_id,))


def retry(connection, task_id="lesson-task"):
    with connection:
        task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None or task["status"] not in ("CANCELED", "SUCCESS"):
            raise ValueError("Teaching retry requires an existing terminal task")
        attempt = task["current_attempt"] + 1
        connection.execute("UPDATE tasks SET current_attempt=?,status='CREATED' WHERE id=?",
                           (attempt, task_id))
        connection.execute("INSERT INTO attempts VALUES (?,?,'CREATED')", (task_id, attempt))
        connection.execute("INSERT INTO events(task_id,attempt,kind) VALUES (?,?,'RETRIED')",
                           (task_id, attempt))
        connection.execute("INSERT INTO outbox(task_id,attempt) VALUES (?,?)", (task_id, attempt))
        return attempt


def cancel_current(connection, attempt, task_id="lesson-task"):
    with connection:
        update = connection.execute("""UPDATE tasks SET status='CANCELED'
            WHERE id=? AND current_attempt=? AND status IN ('CREATED','RUNNING')""",
            (task_id, attempt))
        if update.rowcount != 1:
            return False
        connection.execute("UPDATE attempts SET status='CANCELED' WHERE task_id=? AND number=?",
                           (task_id, attempt))
        connection.execute("INSERT INTO events(task_id,attempt,kind) VALUES (?,?,'CANCELED')",
                           (task_id, attempt))
        return True


def claim(connection, owner, now, ttl=10, job_id="lesson-job"):
    with connection:
        cursor = connection.execute("""UPDATE jobs SET run=run+1,owner=?,lease_until=?,status='running'
          WHERE id=? AND (status='pending' OR (status='running' AND lease_until<=?))
          AND collection_attempt=(SELECT current_attempt FROM tasks WHERE id=jobs.task_id)
          AND (SELECT status FROM tasks WHERE id=jobs.task_id)!='CANCELED'""",
          (owner, now + ttl, job_id, now))
        if cursor.rowcount != 1:
            return None
        return connection.execute("SELECT run FROM jobs WHERE id=?", (job_id,)).fetchone()[0]


def publish_owned(connection, owner, run, now, job_id="lesson-job"):
    with connection:
        cursor = connection.execute("""UPDATE jobs SET status='success'
          WHERE id=? AND owner=? AND run=? AND status='running' AND lease_until>?
          AND collection_attempt=(SELECT current_attempt FROM tasks WHERE id=jobs.task_id)
          AND (SELECT status FROM tasks WHERE id=jobs.task_id)!='CANCELED'""",
          (job_id, owner, run, now))
        if cursor.rowcount != 1:
            return False
        connection.execute("""INSERT INTO events(task_id,attempt,kind)
          SELECT task_id,collection_attempt,'ANALYSIS_SUCCEEDED' FROM jobs WHERE id=?""", (job_id,))
        return True


def outbox_case():
    with teaching_database() as connection:
        submit(connection)
        message = connection.execute("SELECT * FROM outbox WHERE published=0").fetchone()
        identity = (message["task_id"], message["attempt"])
        accepted = set()
        effects = []

        def deliver():
            if identity in accepted:
                return "replayed"
            accepted.add(identity)
            effects.append(identity)
            return "accepted"

        first = deliver()
        # A real transport is deliberately not used: simulate loss AFTER acceptance.
        pending = connection.execute("SELECT count(*) FROM outbox WHERE published=0").fetchone()[0]
        second = deliver()
        with connection:
            connection.execute("UPDATE outbox SET published=1 WHERE id=?", (message["id"],))
        naive_effects = []

        def deliver_without_deduplication(message_identity):
            naive_effects.append(message_identity)

        deliver_without_deduplication(identity)
        deliver_without_deduplication(identity)
        checks = [check(first == "accepted", "first delivery is accepted"),
                  check(pending == 1, "lost response leaves a durable pending message"),
                  check(second == "replayed" and len(effects) == 1, "receiver deduplicates redelivery"),
                  check(len(naive_effects) == 2, "negative control without deduplication repeats the effect"),
                  check(connection.execute("SELECT count(*) FROM outbox WHERE published=0").fetchone()[0] == 0,
                        "only acknowledged delivery retires the message")]
        return {"summary": "响应丢失后重发同一身份，下游只接受一次业务动作",
                "checks": checks, "observations": [first, "response_lost", second, "acknowledged"]}


def attempt_case():
    with teaching_database() as connection:
        submit(connection)
        first_cancel = cancel_current(connection, 1)
        next_attempt = retry(connection)
        before = connection.execute("SELECT count(*) FROM events").fetchone()[0]
        stale_cancel = cancel_current(connection, 1)
        task = connection.execute("SELECT * FROM tasks").fetchone()
        after = connection.execute("SELECT count(*) FROM events").fetchone()[0]
        checks = [check(first_cancel, "current attempt can be canceled"),
                  check(next_attempt == 2, "retry creates a distinct monotone attempt"),
                  check(not stale_cancel and task["status"] == "CREATED", "old cancellation cannot mutate new execution"),
                  check(before == after, "rejected late message does not append a fake event"),
                  check(connection.execute("SELECT count(*) FROM attempts").fetchone()[0] == 2,
                        "previous attempt history is retained")]
        # Deliberately broken update in the temporary fixture, after the protected observation.
        with connection:
            connection.execute("UPDATE tasks SET status='CANCELED' WHERE id=?", ("lesson-task",))
        broken = connection.execute("SELECT status FROM tasks").fetchone()[0]
        checks.append(check(broken == "CANCELED", "negative control without attempt predicate reproduces corruption"))
        return {"summary": "旧轮次消息被 SQL 条件拒绝，坏谓词能重现误取消",
                "checks": checks, "observations": [{"current_attempt": 2, "stale_message_attempt": 1,
                    "protected_status": task["status"], "broken_status": broken}]}


def lease_case():
    with teaching_database() as connection:
        submit(connection)
        with connection:
            connection.execute("INSERT INTO jobs(id,task_id,collection_attempt) VALUES ('lesson-job','lesson-task',1)")
        run_a = claim(connection, "worker-A", 100)
        premature = claim(connection, "worker-B", 109)
        expired = publish_owned(connection, "worker-A", run_a, 110)
        run_b = claim(connection, "worker-B", 111)
        late_a = publish_owned(connection, "worker-A", run_a, 112)
        current_b = publish_owned(connection, "worker-B", run_b, 112)
        duplicate_b = publish_owned(connection, "worker-B", run_b, 113)
        count = connection.execute("SELECT count(*) FROM events WHERE kind='ANALYSIS_SUCCEEDED'").fetchone()[0]
        checks = [check(run_a == 1 and premature is None, "valid lease excludes a second owner"),
                  check(not expired, "expiry alone revokes publication even before takeover"),
                  check(run_b == 2, "expired work is claimed under a new run"),
                  check(not late_a, "previous owner cannot publish after takeover"),
                  check(current_b and not duplicate_b and count == 1, "current owner publishes one atomic success event")]
        return {"summary": "逻辑时钟重现 A 过期、B 接管、A 迟到的发布竞争",
                "checks": checks, "observations": [{"A_run": run_a, "B_run": run_b,
                    "A_late_accepted": late_a, "B_accepted": current_b, "success_events": count}]}


def atomic_case():
    with teaching_database() as connection:
        failed = False
        try:
            submit(connection, fail_after="event")
        except RuntimeError:
            failed = True
        counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("tasks", "attempts", "events", "outbox")}
        # Table names above are fixed program constants, never user-provided SQL.
        submit(connection, name="synthetic'); DROP TABLE tasks; --")
        stored = connection.execute("SELECT name FROM tasks WHERE id=?", ("lesson-task",)).fetchone()[0]
        checks = [check(failed and all(count == 0 for count in counts.values()), "intermediate failure rolls back every lifecycle record"),
                  check(stored == "synthetic'); DROP TABLE tasks; --", "parameterized SQL stores hostile-looking text as data"),
                  check(connection.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1,
                        "successful transaction commits its dispatch fact")]
        return {"summary": "任务、轮次、事件和待办一起提交或一起回滚",
                "checks": checks, "observations": [{"after_injected_failure": counts}]}


CASES = {
    "lifecycle-outbox": outbox_case,
    "attempt-fence": attempt_case,
    "lease-handover": lease_case,
    "atomic-events": atomic_case,
}
