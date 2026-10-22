"""只由独立质量脚本启动：当前 C++ PROC_CPU 实采 → 真正 S3 → 真正 Analyzer。

任务行是显式标记的 synthetic 编排夹具，RAW 必须来自本次 Runner 实采且 SHA 相符。
这不是完整 HTTP/gRPC/浏览器 E2E，也不使用历史 imported profile 替代现场采样。
"""

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from drop_analyzer.config import Config
from drop_analyzer.repository import Repository
from drop_analyzer.storage import ObjectStore
from drop_analyzer.worker import process_job


@unittest.skipUnless(os.getenv("TEST_LIVE_CAPTURE_DIR"), "Explicit fresh Runner capture required; use scripts/quality/protocol/run-live-chain.sh")
class LiveCollectionChainTests(unittest.TestCase):
    def test_current_runner_capture_through_storage_and_atomic_analysis(self):
        relative = os.environ["TEST_LIVE_CAPTURE_DIR"]
        self.assertRegex(relative, r"\A\.local/drop-quality-chain-[a-f0-9]{16}/capture\Z")
        root = Path(__file__).resolve().parents[2]
        # RAW 使用额外只读挂载，不放宽宿主 .local 私有目录的权限以让容器穿透父目录。
        capture = Path("/quality-live-capture")
        self.assertFalse(capture.is_symlink())
        raw_path, receipt_path = capture / "profile.folded", capture / "receipt.json"
        for path in (raw_path, receipt_path):
            self.assertFalse(path.is_symlink())
            self.assertTrue(path.is_file())
        self.assertLessEqual(raw_path.stat().st_size, 1024 * 1024)
        self.assertLessEqual(receipt_path.stat().st_size, 64 * 1024)
        raw, receipt = raw_path.read_bytes(), json.loads(receipt_path.read_bytes())
        binary_path = capture / "binaries.sha256"
        self.assertFalse(binary_path.is_symlink())
        self.assertLessEqual(binary_path.stat().st_size, 1024)
        binary_rows = [line.split() for line in binary_path.read_text().splitlines()]
        self.assertEqual(len(binary_rows), 2)
        binary_digests = {}
        for row in binary_rows:
            self.assertEqual(len(row), 2)
            self.assertRegex(row[0], r"\A[a-f0-9]{64}\Z")
            name = Path(row[1]).name
            self.assertIn(name, ("drop_runner_probe", "drop_workload"))
            binary_digests[name] = row[0]
        self.assertEqual(len(binary_digests), 2)
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual((receipt["kind"], receipt["format"], receipt["size"], receipt["sha256"]), ("PROC_CPU", "folded", len(raw), digest))
        self.assertGreater(len(raw), 0)
        self.assertEqual(receipt["metadata"]["sample_unit"], "CPU ticks")
        # procfs 的权重是 CPU tick，不能把线程计数伪称为函数栈或毫秒耗时。
        expected_ticks = sum(int(line.rsplit(b" ", 1)[1]) for line in raw.splitlines())
        self.assertGreater(expected_ticks, 0)
        schema = "quality_chain_" + uuid4().hex
        base_dsn = os.environ["TEST_DATABASE_URL"]
        dsn = make_conninfo(base_dsn, options=f"-csearch_path={schema}")
        bucket = schema.replace("_", "-")
        storage = None
        created = False
        bucket_created = False
        try:
            with psycopg.connect(base_dsn) as connection:
                connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                created = True
            with psycopg.connect(dsn, row_factory=dict_row) as connection:
                for migration in sorted((root / "db/migrations").glob("*.sql")):
                    connection.execute(migration.read_text(encoding="utf-8"))
                connection.execute("INSERT INTO users(id,username,display_name,role) VALUES('synthetic-chain','synthetic-chain','Synthetic quality orchestration','operator')")
                task = connection.execute("INSERT INTO tasks(name,kind,creator_id,request_id,collection_status,params) VALUES('Synthetic orchestration, fresh real PROC_CPU RAW','PROC_CPU','synthetic-chain','synthetic-quality','COLLECTED',%s) RETURNING id", (Jsonb({"duration_seconds": 3, "frequency_hz": 10, "scope": "process"}),)).fetchone()["id"]
                attempt = connection.execute("INSERT INTO task_attempts(task_id,attempt,status,raw_object_key,runner_metadata) VALUES(%s,1,'COLLECTED','raw/current/profile.folded',%s) RETURNING id", (task, Jsonb(receipt["metadata"]))).fetchone()["id"]
                connection.execute("INSERT INTO artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_type,format,metadata) VALUES(%s,%s,'RAW','profile.folded','raw/current/profile.folded',%s,%s,'text/plain','folded',%s)", (task, attempt, len(raw), digest, Jsonb(receipt["metadata"])))
                connection.execute("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline) VALUES(%s,%s,'folded_v1')", (task, attempt))
            with tempfile.TemporaryDirectory() as directory:
                config = Config(dsn, os.environ["TEST_S3_ENDPOINT"], os.environ["TEST_S3_ACCESS_KEY"], os.environ["TEST_S3_SECRET_KEY"], s3_bucket=bucket, work_dir=directory)
                storage = ObjectStore(config)
                storage.client.make_bucket(bucket)
                bucket_created = True
                storage.client.put_object(bucket, "raw/current/profile.folded", io.BytesIO(raw), len(raw), content_type="text/plain", metadata={"sha256": digest})
                repository = Repository(dsn, "quality-live-chain", 60)
                job = repository.claim()
                self.assertIsNotNone(job)
                self.assertEqual((job.task_id, job.attempt_id, job.collection_attempt), (task, attempt, 1))
                process_job(job, repository, storage, config)
            with psycopg.connect(dsn, row_factory=dict_row) as connection:
                state = connection.execute("SELECT t.collection_status,t.analysis_status,j.status,j.result FROM tasks t JOIN analysis_jobs j ON j.task_id=t.id WHERE t.id=%s", (task,)).fetchone()
                self.assertEqual((state["collection_status"], state["analysis_status"], state["status"]), ("COLLECTED", "SUCCESS", "success"))
                self.assertEqual(state["result"]["total_samples"], expected_ticks)
                self.assertEqual((state["result"]["summary"]["unit"], state["result"]["summary"]["source"]), ("cpu_ticks", "procfs"))
                artifacts = connection.execute("SELECT name,object_key,size,sha256 FROM artifacts WHERE task_id=%s AND kind='RESULT' ORDER BY name", (task,)).fetchall()
                events = connection.execute("SELECT type FROM task_events WHERE task_id=%s AND attempt_id=%s ORDER BY sequence", (task, attempt)).fetchall()
                self.assertEqual(sum(e["type"] == "ANALYSIS_SUCCEEDED" for e in events), 1)
            downloaded = {}
            for artifact in artifacts:
                response = storage.client.get_object(bucket, artifact["object_key"])
                try:
                    body = response.read(16 * 1024 * 1024 + 1)
                finally:
                    response.close()
                    response.release_conn()
                self.assertEqual((len(body), hashlib.sha256(body).hexdigest()), (artifact["size"], artifact["sha256"]))
                downloaded[artifact["name"]] = body
            self.assertIn("flamegraph.json.gz", downloaded)
            self.assertEqual(json.loads(gzip.decompress(downloaded["flamegraph.json.gz"]))["value"], expected_ticks)
            self.assertGreaterEqual(len(artifacts), 3)
            safe = {"schema_version": 1, "passed": True, "provenance": "fresh real C++ PROC_CPU Runner capture; synthetic orchestration rows", "scope": "Runner -> MinIO -> Analyzer child -> PostgreSQL atomic result -> result download", "excluded_scope": ["Go HTTP task creation", "Agent gRPC delivery", "browser", "paid model"], "raw_sha256": digest, "raw_size": len(raw), "total_cpu_ticks": expected_ticks, "producer_binary_sha256": binary_digests, "result_artifacts_verified": len(artifacts), "analysis_success_events": 1, "database": "fresh owned PostgreSQL schema, all authoritative migrations", "storage": "fresh owned MinIO bucket", "production_mutations": False}
            print("DROP_QUALITY_CHAIN_RECEIPT=" + json.dumps(safe), flush=True)
        finally:
            try:
                if bucket_created:
                    for item in storage.client.list_objects(bucket, recursive=True):
                        storage.client.remove_object(bucket, item.object_name)
                    storage.client.remove_bucket(bucket)
            finally:
                if created:
                    with psycopg.connect(base_dsn) as connection:
                        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    unittest.main()
