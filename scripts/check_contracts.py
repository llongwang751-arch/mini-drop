"""静态检查共享任务类型、protobuf 服务和 SQL 不变量，防止跨服务契约漂移。"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
kinds = json.loads((ROOT / "contracts/task-kinds.json").read_text(encoding="utf-8"))
assert len({kind["id"] for kind in kinds}) == len(kinds)
for kind in kinds:
    assert kind["default_duration_seconds"] <= kind["max_duration_seconds"]
    assert kind["default_frequency_hz"] <= kind["max_frequency_hz"]
proto = (ROOT / "contracts/drop.proto").read_text(encoding="utf-8")
for name in ("InitAgent", "HealthCheck", "Hotmethod", "Control"):
    assert f"service {name}" in proto
assert re.search(r"message TaskReference.*int64 attempt_id = 3", proto)
sql = (ROOT / "db/migrations/001_initial.sql").read_text(encoding="utf-8")
for table in ("tasks", "task_attempts", "task_events", "analysis_jobs", "artifacts", "outbox", "sessions", "schedule_records"):
    assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql
assert "UNIQUE(creator_id,idempotency_key)" in sql
assert "UNIQUE(schedule_id,scheduled_at)" in sql
print(f"Shared contracts valid: {len(kinds)} TaskKinds, 4 gRPC services, attempt fencing and SQL uniqueness")
