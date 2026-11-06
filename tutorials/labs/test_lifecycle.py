import unittest
from .lifecycle import teaching_database, submit, cancel_current, retry, claim, publish_owned


class LifecycleRegression(unittest.TestCase):
    def test_failure_does_not_commit_an_event_without_its_dispatch_fact(self):
        with teaching_database() as connection:
            with self.assertRaises(RuntimeError):
                submit(connection, fail_after="event")
            for table in ("tasks", "attempts", "events", "outbox"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_duplicate_or_stale_cancel_is_observationally_idempotent(self):
        with teaching_database() as connection:
            submit(connection)
            self.assertTrue(cancel_current(connection, 1))
            self.assertFalse(cancel_current(connection, 1))
            retry(connection)
            self.assertFalse(cancel_current(connection, 1))
            self.assertEqual(connection.execute("SELECT status FROM tasks").fetchone()[0], "CREATED")
            self.assertEqual(connection.execute("SELECT count(*) FROM events WHERE kind='CANCELED'").fetchone()[0], 1)

    def test_unexpired_lease_does_not_survive_a_changed_collection_attempt(self):
        with teaching_database() as connection:
            submit(connection)
            with connection:
                connection.execute("INSERT INTO jobs(id,task_id,collection_attempt) VALUES ('lesson-job','lesson-task',1)")
            run = claim(connection, "old-owner", 100, ttl=100)
            self.assertTrue(cancel_current(connection, 1))
            retry(connection)
            self.assertFalse(publish_owned(connection, "old-owner", run, 101))
            self.assertEqual(connection.execute("SELECT count(*) FROM events WHERE kind='ANALYSIS_SUCCEEDED'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
