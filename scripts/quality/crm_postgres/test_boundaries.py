"""本地issuer及可销毁PG入口的离线边界；不伪装成已执行真实RLS。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gateway import gateway, validate_rest_origin
from local_issuer import AUDIENCE, ISSUER, issue, verify

# PG worker刻意导入business/worker；用唯一模块名加载，避免同名模块污染测试。
spec = importlib.util.spec_from_file_location(
    "crm_pg_worker_boundary", HERE / "worker.py"
)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

SECRET = "synthetic-secret-" + "x" * 40
USER = "20000000-0000-4000-8000-000000000001"
SESSION = "30000000-0000-4000-8000-000000000001"
USERS = {USER: {"session_id": SESSION}}


def resign(claims, *, header=None):
    def encode(value):
        return (
            base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )

    unsigned = encode(header or {"alg": "HS256", "typ": "JWT"}) + "." + encode(claims)
    return (
        unsigned
        + "."
        + base64.urlsafe_b64encode(
            hmac.new(SECRET.encode(), unsigned.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )


class LocalIssuerTests(unittest.TestCase):
    def test_actual_signature_is_required_and_user_role_not_granted_by_verifier(self):
        token = issue(SECRET, "authenticated", user=USER, session=SESSION, now=100)
        claims, user = verify(token, SECRET, users=USERS, now=101)
        self.assertEqual(claims["sub"], USER)
        self.assertEqual(user, {"id": USER, "factors": []})
        self.assertNotIn("organization_id", user)
        self.assertNotIn("role", user)
        with self.assertRaises(ValueError):
            verify(token, "wrong" * 10, users=USERS, now=101)

    def test_expiry_and_not_before_are_strict(self):
        token = issue(
            SECRET, "authenticated", user=USER, session=SESSION, expires_in=10, now=100
        )
        for now in (97, 110, 111):
            with self.subTest(now=now), self.assertRaises(ValueError):
                verify(token, SECRET, users=USERS, now=now)

    def test_audience_issuer_algorithm_and_service_role_are_not_accepted_as_user(self):
        claims = {
            "role": "authenticated",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": USER,
            "session_id": SESSION,
            "nbf": 98,
            "exp": 200,
        }
        for field, value in (
            ("iss", "external-pretend"),
            ("aud", "other-app"),
            ("role", "service_role"),
        ):
            token = resign({**claims, field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify(token, SECRET, users=USERS, now=101)
        with self.assertRaises(ValueError):
            verify(
                resign(claims, header={"alg": "none", "typ": "JWT"}),
                SECRET,
                users=USERS,
                now=101,
            )

    def test_session_binding_unknown_subject_and_revocation(self):
        token = issue(SECRET, "authenticated", user=USER, session=SESSION, now=100)
        for users in (
            {},
            {USER: {"session_id": "40000000-0000-4000-8000-000000000001"}},
            {USER: {"session_id": SESSION, "revoked": True}},
        ):
            with self.subTest(users=bool(users)), self.assertRaises(ValueError):
                verify(token, SECRET, users=users, now=101)

    def test_real_loopback_http_adapter_verifies_without_external_issuer(self):
        counters = {
            "gateway_requests": 0,
            "local_issuer_checks": 0,
            "local_issuer_rejections": 0,
        }
        with gateway("http://rest:3000", SECRET, USERS, counters) as origin:
            opener = build_opener(ProxyHandler({}))
            token = issue(SECRET, "authenticated", user=USER, session=SESSION)
            request = Request(
                origin + "/auth/v1/user", headers={"Authorization": "Bearer " + token}
            )
            with opener.open(request, timeout=2) as response:
                self.assertEqual(json.load(response)["id"], USER)
            with self.assertRaises(HTTPError) as caught:
                opener.open(origin + "/auth/v1/user", timeout=2)
            self.assertEqual(caught.exception.code, 401)
            caught.exception.close()
        self.assertEqual(counters["local_issuer_checks"], 2)
        self.assertEqual(counters["local_issuer_rejections"], 1)


class DisposableConfigTests(unittest.TestCase):
    def config(self):
        return {
            "schema_version": 1,
            "disposable_only": True,
            "database_host": "db",
            "database_port": 5432,
            "database_name": "crm_quality",
            "admin_password": "a" * 40,
            "authenticator_password": "b" * 40,
            "jwt_secret": "c" * 48,
            "rest_origin": "http://rest:3000",
        }

    def test_production_host_database_and_unbounded_secrets_rejected(self):
        for key, value in (
            ("database_host", "82.157.81.79"),
            ("database_name", "postgres"),
            ("disposable_only", False),
            ("admin_password", "secret' sql"),
            ("jwt_secret", "x" * 129),
        ):
            config = self.config()
            config[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker.validate_config(config)

    def test_rest_proxy_only_has_fixed_internal_origin(self):
        self.assertEqual(validate_rest_origin("http://rest:3000"), "http://rest:3000")
        for value in (
            "https://82.157.81.79",
            "http://rest:3000/path",
            "http://user:pass@rest:3000",
            "http://rest:3000?url=remote",
            "http://evil.example:3000",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_rest_origin(value)


if __name__ == "__main__":
    unittest.main()
