"""迁移的 PostgreSQL 部分使用真实数据库；对象存储仍由合成夹具替代。"""

import os
from pathlib import Path
import time
import unittest
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from encrypt_existing import Limits, MigrationFailure, SQLRepository, migrate
from test_encrypt_existing import BODY, DIGEST, Storage


@unittest.skipUnless(
    os.getenv("ENCRYPTION_TEST_ALLOW_DISPOSABLE") == "1"
    and os.getenv("ENCRYPTION_TEST_DSN"),
    "explicit fresh disposable loopback PostgreSQL required",
)
class PostgreSQLMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["ENCRYPTION_TEST_DSN"]
        parsed = urlsplit(cls.dsn)
        if parsed.hostname != "127.0.0.1" or parsed.path != "/drop_encryption_fixture":
            raise RuntimeError("Refusing non-fixture PostgreSQL")
        cls.connection = psycopg.connect(cls.dsn, autocommit=True, row_factory=dict_row)
        if (
            not cls.connection.execute("SHOW server_version")
            .fetchone()["server_version"]
            .startswith("17.11")
        ):
            raise RuntimeError("PostgreSQL17.11 is required for this evidence")
        root = Path(__file__).resolve().parents[1]
        for path in sorted((root / "db/migrations").glob("*.sql")):
            cls.connection.execute(path.read_text(encoding="utf8"))
        cls.connection.execute(
            "INSERT INTO users(id,username,display_name,role) "
            "VALUES('encryption-fixture','encryption-fixture','Synthetic fixture','admin')"
        )

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        self.connection.execute("TRUNCATE tasks CASCADE")
        self.task = self.connection.execute(
            "INSERT INTO tasks(name,kind,creator_id,request_id,collection_status,analysis_status) "
            "VALUES('Synthetic imported fixture','IMPORT_COLLAPSED','encryption-fixture',"
            "'encryption-fixture','COLLECTED','SUCCESS') RETURNING id"
        ).fetchone()["id"]
        self.repo = SQLRepository(self.connection)

    def artifact(self, key="private/object", status="ready"):
        self.connection.execute(
            "INSERT INTO artifacts(task_id,kind,name,object_key,size,sha256,status) "
            "VALUES(%s,'RAW','Synthetic imported fixture',%s,%s,%s,%s)",
            (self.task, key, len(BODY), DIGEST, status),
        )

    def test_real_set_local_share_locks_and_named_cursor_include_all_statuses(self):
        for index, status in enumerate(("ready", "expired", "quarantined")):
            self.artifact("synthetic-" + str(index), status)
        with self.connection.transaction():
            with self.repo.quiescent(True):
                self.assertEqual(
                    self.connection.execute("SHOW lock_timeout").fetchone()[
                        "lock_timeout"
                    ],
                    "3s",
                )
                locks = self.connection.execute(
                    "SELECT count(*) AS n FROM pg_locks WHERE pid=pg_backend_pid() "
                    "AND mode='ShareLock' AND granted AND relation IN "
                    "('tasks'::regclass,'task_attempts'::regclass,'analysis_jobs'::regclass,'artifacts'::regclass)"
                ).fetchone()["n"]
                self.assertEqual(locks, 4)
                keys = list(self.repo.keys(1, 3))
                self.assertEqual(len(keys), 3)
                self.assertTrue(
                    all(self.repo.expected(key).sha256 == DIGEST for key in keys)
                )
        self.assertEqual(
            self.connection.execute("SHOW lock_timeout").fetchone()["lock_timeout"], "0"
        )

    def test_each_nonterminal_collection_is_rejected(self):
        for status in ("CREATED", "QUEUED", "DELIVERED", "RUNNING", "UPLOADING"):
            with self.subTest(status=status):
                self.connection.execute(
                    "UPDATE tasks SET collection_status=%s", (status,)
                )
                with self.connection.transaction():
                    with self.assertRaisesRegex(MigrationFailure, "ACTIVE_WRITERS"):
                        with self.repo.quiescent(True):
                            self.fail("Active collection acquired the migration fence")

    def test_pending_running_and_retry_analysis_are_rejected(self):
        for status in ("pending", "running", "retry"):
            with self.subTest(status=status):
                self.connection.execute("DELETE FROM analysis_jobs")
                self.connection.execute(
                    "INSERT INTO analysis_jobs(task_id,status) VALUES(%s,%s)",
                    (self.task, status),
                )
                with self.connection.transaction():
                    with self.assertRaisesRegex(MigrationFailure, "ACTIVE_WRITERS"):
                        with self.repo.quiescent(True):
                            self.fail("Active analysis acquired the migration fence")

    def test_same_advisory_lock_excludes_existing_reconciler(self):
        with psycopg.connect(self.dsn, autocommit=True) as other:
            # 对账器持有事务锁，应等待真实 COMMIT 返回，
            # 不能与异步数据库连接断开争抢时序。
            with other.transaction():
                other.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('drop-maintenance-v1',0))"
                )
                with self.connection.transaction():
                    with self.assertRaisesRegex(
                        MigrationFailure, "MAINTENANCE_ALREADY_RUNNING"
                    ):
                        with self.repo.quiescent(True):
                            self.fail("Maintenance lock was not exclusive")
            with self.connection.transaction():
                with self.repo.quiescent(True):
                    pass

    def test_share_lock_blocks_writes_then_releases_at_transaction_end(self):
        with psycopg.connect(self.dsn, autocommit=True) as other:
            other.execute("SET lock_timeout='150ms'")
            with self.connection.transaction():
                with self.repo.quiescent(True):
                    with self.assertRaises(psycopg.errors.LockNotAvailable):
                        other.execute(
                            "UPDATE tasks SET name='Synthetic writer' WHERE id=%s",
                            (self.task,),
                        )
            other.execute(
                "UPDATE tasks SET name='Synthetic writer' WHERE id=%s", (self.task,)
            )

    def test_existing_writer_causes_bounded_lock_timeout_without_copying(self):
        storage = Storage()
        with psycopg.connect(self.dsn) as other:
            other.execute(
                "UPDATE tasks SET name='Synthetic held writer' WHERE id=%s",
                (self.task,),
            )
            tick = time.monotonic()
            with self.connection.transaction():
                result = migrate(
                    storage, self.repo, apply=True, confirm_writers_stopped=True
                )
            elapsed = time.monotonic() - tick
            self.assertGreaterEqual(elapsed, 2.5)
            self.assertLess(elapsed, 10)
            self.assertFalse(result["complete"])
            self.assertEqual(result["error"], "STORAGE_OR_DATABASE_FAILURE")
            self.assertEqual(storage.copies, [])

    def test_named_cursor_stops_at_configured_database_scan_limit(self):
        for index in range(3):
            self.artifact("synthetic-" + str(index))
        with self.connection.transaction():
            with self.assertRaisesRegex(MigrationFailure, "DATABASE_SCAN_LIMIT"):
                list(self.repo.keys(1, 2))
            count = self.connection.execute(
                "SELECT count(*) AS n FROM pg_cursors WHERE name='encryption_artifact_keys'"
            ).fetchone()["n"]
            self.assertEqual(count, 0)

    def test_real_database_full_migration_with_fake_storage_is_idempotent(self):
        self.artifact(status="quarantined")
        storage = Storage()
        with self.connection.transaction():
            dry = migrate(storage, self.repo, limits=Limits(page_size=1))
        self.assertTrue(dry["scan_complete"])
        self.assertEqual(storage.copies, [])
        for _ in range(2):
            with self.connection.transaction():
                applied = migrate(
                    storage, self.repo, apply=True, confirm_writers_stopped=True
                )
            self.assertTrue(applied["complete"], applied)
            self.assertEqual(applied["baseline_only"], 0)
        self.assertEqual(len(storage.copies), 1)


if __name__ == "__main__":
    unittest.main()
