"""检查计划、保密边界和实际 oracle 不被模型输入替换；不伪装成 CRM 数据库测试。"""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from contract import (
    CASE_INDEX,
    MANDATORY,
    canonical,
    load_plan,
    plan_schema,
    validate_plan,
)
from run import clean_environment, emit, main
from worker import CaseRun, local_only, source_manifest


class PlanBoundaryTests(unittest.TestCase):
    def test_external_plan_cannot_remove_auth_or_tenant_regressions(self):
        selected = validate_plan({"schema_version": 1, "case_ids": ["name_maximum"]})
        self.assertTrue(set(MANDATORY) <= set(selected))
        self.assertEqual(selected[-1], "name_maximum")

    def test_model_cannot_supply_code_url_or_expected_values(self):
        for key, value in (
            ("url", "https://example.invalid"),
            ("code", "arbitrary"),
            ("expected", 200),
            ("actor", "admin"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_plan(
                    {"schema_version": 1, "case_ids": ["create_minimum"], key: value}
                )

    def test_unknown_duplicate_or_non_string_cases_fail_closed(self):
        for cases in (
            ["not_known"],
            [True],
            ["viewer_write", "viewer_write"],
            [],
            ["viewer_write"] * 65,
        ):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                validate_plan({"schema_version": 1, "case_ids": cases})

    def test_bool_is_not_schema_version(self):
        with self.assertRaises(ValueError):
            validate_plan({"schema_version": True, "case_ids": ["create_minimum"]})

    def test_duplicate_json_keys_and_large_plan_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(
                '{"schema_version":1,"case_ids":["viewer_write"],"case_ids":["create_minimum"]}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_plan(path)
            path.write_bytes(b" " * 16_385)
            with self.assertRaises(ValueError):
                load_plan(path)

    def test_plan_never_claims_model_was_called(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_bytes(
                canonical({"schema_version": 1, "case_ids": ["viewer_write"]})
            )
            _selected, provenance = load_plan(path)
            self.assertEqual(provenance["mode"], "external_bounded_plan")
            self.assertFalse(provenance["model_called_by_runner"])

    def test_schema_catalog_matches_runtime_validator(self):
        schema = plan_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]["case_ids"]["items"]["enum"]), set(CASE_INDEX)
        )


class IsolationBoundaryTests(unittest.TestCase):
    def test_business_and_model_credentials_not_in_child_environment(self):
        supplied = {
            "PATH": "synthetic-path",
            "APPDATA": "synthetic-user-directory",
            "SUPABASE_SERVICE_ROLE_KEY": "must-not-propagate",
            "DEEPSEEK_API_KEY": "must-not-propagate",
            "HTTPS_PROXY": "must-not-propagate",
            "PYTHONPATH": "must-not-propagate",
        }
        with patch.dict(os.environ, supplied, clear=True):
            environment = clean_environment()
        self.assertNotIn("DEEPSEEK_API_KEY", environment)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["APPDATA"], "synthetic-user-directory")
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")

    def test_network_and_dotenv_attempts_are_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("SYNTHETIC=not-read", encoding="utf-8")
            with local_only():
                with self.assertRaises(PermissionError):
                    path.read_text(encoding="utf-8")
                with (
                    self.assertRaises(PermissionError),
                    open(path, encoding="utf-8"),
                ):
                    self.fail("dotenv read must be blocked")
                with self.assertRaises(PermissionError):
                    socket.getaddrinfo("example.invalid", 443)
                with socket.socket() as connection, self.assertRaises(PermissionError):
                    connection.connect(("127.0.0.1", 1))

    def test_output_never_overwrites_prior_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            emit(path, {"old": "failure"})
            with self.assertRaises(ValueError):
                emit(path, {"new": "success"})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")), {"old": "failure"}
            )

    def test_source_must_have_actual_contract_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "crm").mkdir()
            (source / "crm" / "__init__.py").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "CRM_SOURCE_CONTRACT_MISSING"):
                source_manifest(source)

    def test_output_inside_source_is_rejected_before_worker_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            with patch("subprocess.run") as spawned, patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--crm-source", str(source), "--output", str(source / "out")])
                spawned.assert_not_called()


class RequestReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_receipt_contains_no_request_body_or_phone_and_hides_cursor(self):
        class Response:
            status_code = 201
            content = b'{"data":{}}'

            def json(self):
                return {"data": {}}

        class Client:
            async def request(self, *_args, **_kwargs):
                return Response()

        run = CaseRun(Client(), None, [0])
        await run.request(
            "POST",
            "/api/v1/contacts?limit=1&cursor=synthetic-signature",
            body={"phone_number": "+12345678", "name": "synthetic-name"},
        )
        encoded = json.dumps(run.trace)
        self.assertNotIn("+12345678", encoded)
        self.assertNotIn("synthetic-name", encoded)
        self.assertNotIn("synthetic-signature", encoded)
        self.assertEqual(run.trace[0]["body_fields"], ["name", "phone_number"])

    async def test_request_count_budget_fails_before_request(self):
        class Client:
            async def request(self, *_args, **_kwargs):
                raise AssertionError("must not reach transport")

        run = CaseRun(Client(), None, [512])
        with self.assertRaisesRegex(ValueError, "REQUEST_BUDGET_EXCEEDED"):
            await run.request("GET", "/api/v1/contacts")

    async def test_oversized_body_fails_before_request(self):
        class Client:
            async def request(self, *_args, **_kwargs):
                raise AssertionError("must not reach transport")

        run = CaseRun(Client(), None, [0])
        with self.assertRaisesRegex(ValueError, "SYNTHETIC_REQUEST_TOO_LARGE"):
            await run.request("POST", "/api/v1/contacts", body={"name": "x" * 65_536})


if __name__ == "__main__":
    unittest.main()
