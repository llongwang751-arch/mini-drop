"""只使用导入的账号和数据行夹具，不读数据库、浏览器或凭据。"""
import unittest
from unittest.mock import Mock, patch
import stage_partial_chain as stage


class AccountContractTests(unittest.TestCase):
    def test_import_request_leaves_source_provenance_to_the_api(self):
        account = "61902c09-7239-40d2-84bd-fb9b7458cb50"
        run_id = "a" * 32
        api, database, journal = Mock(), Mock(), Mock()
        def request(method, path, body, **kwargs):
            if path.endswith("/login"):
                return {"user": {"id": account, "role": "admin"}}
            self.assertEqual(path, "/api/v1/tasks")
            self.assertNotIn("source", body["parameters"])
            self.assertEqual(body["collapsed_text"], stage.RAW.decode())
            return {"id": "7"}
        api.request.side_effect = request
        journal.reserve.return_value = {"run_id": run_id, "phase": "reserved", "objects": {}}
        database.snapshot.return_value = {"task_id": 7, "attempt_id": 8, "analysis_job_id": 9, "input_artifact_id": 10,
            "name": stage.PREFIX + run_id, "creator_id": account, "kind": "IMPORT_COLLAPSED", "collection_status": "COLLECTED",
            "collection_attempt": 1, "input_digest": stage.RAW_SHA, "input_size": len(stage.RAW), "analysis_status": "PENDING",
            "job_status": "pending", "analysis_run": 0, "lease_owner": None, "lease_expires_at": None, "result": None}
        with patch.object(stage, "secret", return_value="imported-secret-fixture"):
            self.assertEqual(stage.prepare(api, database, journal, run_id)["task_id"], "7")

    def test_real_account_uuid_contract_is_distinct_from_bigint_task_ids(self):
        account = "61902c09-7239-40d2-84bd-fb9b7458cb50"
        api = Mock()
        api.request.return_value = {"user": {"id": account, "role": "admin"}}
        with patch.object(stage, "secret", return_value="imported-secret-fixture"):
            self.assertEqual(stage.login(api), account)
        self.assertFalse(stage.positive(account))
        self.assertTrue(stage.positive("7"))

    def test_invalid_account_identity_or_role_is_rejected_before_task_creation(self):
        for identity, role in [("7", "admin"), ("00000000-0000-0000-0000-000000000000", "admin"),
                               ("61902c09-7239-40d2-84bd-fb9b7458cb50", "viewer"), ("invalid'; DROP TABLE tasks", "admin")]:
            api = Mock()
            api.request.return_value = {"user": {"id": identity, "role": role}}
            with self.subTest(identity=identity, role=role), patch.object(stage, "secret", return_value="imported-secret-fixture"):
                with self.assertRaises(stage.Failure):
                    stage.login(api)
            self.assertEqual(api.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
