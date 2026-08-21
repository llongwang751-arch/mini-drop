"""HTTP API 测试。

通过 FastAPI TestClient 验证各 REST 端点，
测试使用独立 repo 实例避免与 gRPC 测试共享状态。

注意：TestClient 会触发 FastAPI startup 事件尝试启动 gRPC server。
50051 端口被占用时 gRPC 启动失败不影响 HTTP 端点功能，
测试在 setUp 中直接清理 repo 状态。
"""

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from server.app import main as main_module
from server.app import storage as store
from server.app.database import init_db, reset_engine
from server.app.evaluation.evaluator import ArtifactEvaluationError
from server.app.main import _ensure_minio_bucket_with_retry, app, repo
from server.app.models import Base
from server.app.prometheus_metrics import REGISTRY
from server.app.state_machine import Actor, TaskStatus


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    """每个测试使用独立 SQLite 内存库，确保用例间无状态交叉。"""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINI_DROP_AI_API_KEY", raising=False)
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("MINI_DROP_API_KEY", raising=False)
    monkeypatch.delenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("MINI_DROP_EVALUATOR_ORACLE_PATH", raising=False)
    monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKET", "0")
    REGISTRY.clear()
    reset_engine()
    init_db()
    repo._task_queues.clear()
    repo.agent_metrics.clear()
    repo.register_agent("agent_local_demo", "demo-host", "10.0.0.10")
    repo.register_agent("a1", "agent-one", "10.0.0.11")
    repo.register_agent("a2", "agent-two", "10.0.0.12")
    repo.register_agent("a3", "agent-three", "10.0.0.13")
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    """提供预配置的 TestClient 实例。"""
    return TestClient(app)


class TestHealthz:
    """健康与用户信息端点。"""

    def test_healthz_returns_service_info(self, client: TestClient):
        resp = client.get("/api/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["service"] == "mini-drop-server"

    def test_me_returns_demo_user(self, client: TestClient):
        resp = client.get("/api/me")
        assert resp.status_code == 200
        assert resp.json()["data"]["user_id"] == "demo_user"

    def test_continuous_diagnosis_triggers_endpoint(self, client: TestClient):
        resp = client.get("/api/v1/continuous-diagnosis-triggers")
        assert resp.status_code == 200
        assert resp.json()["data"] == {
            "items": [],
            "total": 0,
            "offset": 0,
            "limit": 100,
        }

    def test_ai_config_never_returns_key(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_AI_API_KEY", "secret-must-not-leak")
        monkeypatch.setenv("MINI_DROP_AI_MODEL", "deepseek-v4-flash")
        resp = client.get("/api/ai-config")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["has_api_key"] is True
        assert data["model"] == "deepseek-v4-flash"
        assert "secret-must-not-leak" not in resp.text

    def test_ai_validation_endpoint(self, client: TestClient):
        result = {
            "run_id": "ai_validation_test",
            "status": "PASSED",
            "passed_count": 8,
            "failed_count": 0,
            "total_count": 8,
            "checks": [],
        }
        with mock.patch("server.app.main.run_ai_validation_suite", return_value=result):
            resp = client.post("/api/ai-validation/runs")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "PASSED"

    def test_golden_diagnosis_gate_endpoint_and_metrics(self, client: TestClient):
        resp = client.get("/api/v1/diagnosis-evaluations/golden")

        assert resp.status_code == 200
        report = resp.json()["data"]
        assert report["dataset_version"] == "2.0.0"
        assert report["gate_status"] == "PASSED"
        assert report["metrics"]["falsification_plan_rate"] == 1.0

        metrics = client.get("/api/metrics").text
        assert "mini_drop_ai_golden_gate_passed" in metrics
        assert 'dataset_version="2.0.0"' in metrics


class TestAnalysisJobsApi:
    def _create_job(self):
        from server.app.schemas import CreateTaskRequest

        task = repo.create_task(CreateTaskRequest(
            name="analysis-api",
            agent_id="a1",
            target_pid=101,
            collector_type="sys_metrics",
        ))
        return repo.enqueue_analysis_job(
            task.id,
            analyzer_type="artifact-set",
            analyzer_version="1.0.0",
            input_checksum="e" * 64,
            max_retries=0,
        )

    def test_list_and_detail(self, client: TestClient):
        job = self._create_job()

        listing = client.get("/api/analysis-jobs", params={"task_id": job.task_id})
        detail = client.get(f"/api/analysis-jobs/{job.id}")

        assert listing.status_code == 200
        assert listing.json()["data"][0]["id"] == job.id
        assert detail.status_code == 200
        assert detail.json()["data"]["analyzer_version"] == "1.0.0"

    def test_replay_dead_letter(self, client: TestClient):
        job = self._create_job()
        assert repo.claim_analysis_job("api-worker") is not None
        repo.fail_analysis_job(
            job.id,
            "api-worker",
            error_code="TEST",
            error_message="forced",
            retry_delay_sec=0,
        )

        response = client.post(f"/api/analysis-jobs/{job.id}/replay")

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "PENDING"


class TestStartupMinio:
    def test_bucket_init_retries_transient_failure(self, monkeypatch):
        calls = {"count": 0}

        def flaky_ensure(bucket: str) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("not ready")
            assert bucket == "mini-drop"

        monkeypatch.setenv("MINI_DROP_MINIO_READY_RETRIES", "2")
        monkeypatch.setenv("MINI_DROP_MINIO_READY_DELAY_SEC", "0")
        monkeypatch.setattr(store, "ensure_bucket", flaky_ensure)

        _ensure_minio_bucket_with_retry("mini-drop")

        assert calls["count"] == 2

    def test_bucket_init_raises_after_retry_exhausted(self, monkeypatch):
        monkeypatch.setenv("MINI_DROP_MINIO_READY_RETRIES", "2")
        monkeypatch.setenv("MINI_DROP_MINIO_READY_DELAY_SEC", "0")
        monkeypatch.setattr(store, "ensure_bucket", lambda bucket: (_ for _ in ()).throw(RuntimeError("down")))

        with pytest.raises(RuntimeError, match="down"):
            _ensure_minio_bucket_with_retry("mini-drop")
    def test_lifespan_stops_embedded_outbox_workers(self, monkeypatch):
        task_started = main_module.threading.Event()
        artifact_started = main_module.threading.Event()
        task_stopped = main_module.threading.Event()
        artifact_stopped = main_module.threading.Event()

        def wait_for_stop(_target, _worker_id, *, stop_event, **kwargs):
            if _worker_id.endswith(":task"):
                task_started.set()
                stopped = task_stopped
            else:
                artifact_started.set()
                stopped = artifact_stopped
            if stop_event.wait(60):
                stopped.set()

        monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKET", "0")
        monkeypatch.setenv("MINI_DROP_EMBED_GRPC", "0")
        monkeypatch.setenv("MINI_DROP_EMBED_MAINTENANCE", "0")
        monkeypatch.setenv("MINI_DROP_OUTBOX_DISPATCH_ENABLED", "1")
        monkeypatch.setattr(
            "server.app.outbox_dispatcher.run_worker",
            wait_for_stop,
        )
        monkeypatch.setattr(
            "server.app.outbox_dispatcher.run_artifact_worker",
            wait_for_stop,
        )

        with TestClient(app) as started_client:
            assert task_started.wait(1)
            assert artifact_started.wait(1)
            assert started_client.get("/api/healthz").status_code == 200
            assert not task_stopped.is_set()
            assert not artifact_stopped.is_set()

        assert task_stopped.wait(1)
        assert artifact_stopped.wait(1)


class TestApiAuth:
    def test_auth_disabled_by_default(self, client: TestClient):
        resp = client.get("/api/tasks")
        assert resp.status_code == 200

    def test_auth_enabled_rejects_missing_token(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/tasks")
        assert resp.status_code == 401

    def test_auth_enabled_accepts_bearer_token(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/tasks", headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 200

    def test_auth_enabled_accepts_x_api_key(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/tasks", headers={"X-API-Key": "secret-token"})
        assert resp.status_code == 200

    def test_auth_enabled_accepts_trusted_gateway_identity(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.delenv("MINI_DROP_API_KEY", raising=False)
        monkeypatch.setenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN", "internal-secret")
        resp = client.get(
            "/api/tasks",
            headers={
                "X-Mini-Drop-Gateway-Token": "internal-secret",
                "X-Mini-Drop-Principal": "operator-a",
                "X-Mini-Drop-Roles": "operator",
                "X-Mini-Drop-Agent-Scope": "agent-a",
            },
        )
        assert resp.status_code == 200

    def test_spoofed_gateway_identity_is_rejected(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.delenv("MINI_DROP_API_KEY", raising=False)
        monkeypatch.setenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN", "internal-secret")
        resp = client.get(
            "/api/tasks",
            headers={
                "X-Mini-Drop-Gateway-Token": "attacker-controlled",
                "X-Mini-Drop-Principal": "admin",
            },
        )
        assert resp.status_code == 401

    def test_healthz_stays_public_when_auth_enabled(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/healthz")
        assert resp.status_code == 200

    def test_metrics_stays_public_when_auth_enabled(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        assert "mini_drop" in resp.text or resp.text.strip() == ""


class TestArtifactEvaluatorApi:
    path = "/api/v1/diagnosis-evaluations/artifacts"
    payload = {
        "artifact_id": "artifact:diag-1",
        "expected_artifact_hash": "sha256:" + "1" * 64,
        "evaluator_version": "v1",
    }

    @staticmethod
    def _gateway_headers(role: str = "evaluator") -> dict[str, str]:
        return {
            "X-Mini-Drop-Gateway-Token": "internal-secret",
            "X-Mini-Drop-Principal": "evaluation-service",
            "X-Mini-Drop-Roles": role,
        }

    @staticmethod
    def _enable_auth(monkeypatch) -> None:
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv(
            "MINI_DROP_INTERNAL_GATEWAY_TOKEN",
            "internal-secret",
        )

    def test_missing_authentication_is_rejected(
        self,
        client: TestClient,
        monkeypatch,
    ):
        self._enable_auth(monkeypatch)

        response = client.post(self.path, json=self.payload)

        assert response.status_code == 401

    def test_role_is_required_even_when_global_auth_is_disabled(
        self,
        client: TestClient,
    ):
        response = client.post(self.path, json=self.payload)

        assert response.status_code == 403

    def test_ordinary_api_key_cannot_invoke_evaluator(
        self,
        client: TestClient,
        monkeypatch,
    ):
        self._enable_auth(monkeypatch)
        monkeypatch.setenv("MINI_DROP_API_KEY", "ordinary-key")

        response = client.post(
            self.path,
            json=self.payload,
            headers={"X-API-Key": "ordinary-key"},
        )

        assert response.status_code == 403

    def test_operator_role_cannot_invoke_evaluator(
        self,
        client: TestClient,
        monkeypatch,
    ):
        self._enable_auth(monkeypatch)

        response = client.post(
            self.path,
            json=self.payload,
            headers=self._gateway_headers("operator"),
        )

        assert response.status_code == 403

    def test_missing_server_owned_oracle_configuration_fails_closed(
        self,
        client: TestClient,
        monkeypatch,
    ):
        self._enable_auth(monkeypatch)

        response = client.post(
            self.path,
            json=self.payload,
            headers=self._gateway_headers(),
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "EVALUATOR_UNAVAILABLE"

    def test_evaluator_role_returns_only_public_boolean_result(
        self,
        client: TestClient,
        monkeypatch,
    ):
        self._enable_auth(monkeypatch)
        evaluator = mock.Mock()
        evaluator.evaluate.return_value = {
            "evaluation_id": "evaluation:public",
            "artifact_id": "artifact:diag-1",
            "artifact_hash": self.payload["expected_artifact_hash"],
            "evaluator_version": "v1",
            "status": "COMPLETED",
            "failure_code": None,
            "result": {
                "passed": True,
                "matches": {
                    "instance_id": True,
                    "location_type": True,
                    "domain_type": True,
                    "classification": True,
                },
            },
        }
        monkeypatch.setattr(
            main_module,
            "_artifact_evaluator",
            lambda: evaluator,
        )

        response = client.post(
            self.path,
            json=self.payload,
            headers=self._gateway_headers(),
        )

        assert response.status_code == 200
        rendered = response.text.lower()
        assert response.json()["data"]["result"]["passed"] is True
        assert "expected_" not in rendered
        assert "oracle" not in rendered
        evaluator.evaluate.assert_called_once()

    @pytest.mark.parametrize(
        "code,status_code",
        [
            ("ARTIFACT_NOT_FOUND", 404),
            ("ARTIFACT_HASH_MISMATCH", 409),
            ("ARTIFACT_HASH_INVALID", 409),
            ("ARTIFACT_MALFORMED", 409),
            ("ORACLE_UNAVAILABLE", 503),
        ],
    )
    def test_failures_project_stable_non_sensitive_codes(
        self,
        client: TestClient,
        monkeypatch,
        code: str,
        status_code: int,
    ):
        self._enable_auth(monkeypatch)
        evaluator = mock.Mock()
        evaluator.evaluate.side_effect = ArtifactEvaluationError(code)
        monkeypatch.setattr(
            main_module,
            "_artifact_evaluator",
            lambda: evaluator,
        )

        response = client.post(
            self.path,
            json=self.payload,
            headers=self._gateway_headers(),
        )

        assert response.status_code == status_code
        assert response.json() == {"detail": code}
        assert "oracle.json" not in response.text.lower()


class TestAgents:
    def test_list_agents_includes_latest_metrics(self, client: TestClient):
        repo.record_agent_metrics("a1", {
            "self": {"cpu_percent": 1.5, "rss_mb": 32.0, "read_kb_s": 0.1, "write_kb_s": 0.2},
            "children": {"children_count": 2},
        })
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()["data"]
        agents = data if isinstance(data, list) else data.get("items", [])
        agent = next(item for item in agents if item["id"] == "a1")
        assert agent["latest_metrics"]["self"]["cpu_percent"] == 1.5


class TestCreateTask:
    """任务创建端点。"""

    def test_create_task_records_pending_status(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "demo cpu profile",
            "agent_id": "agent_local_demo",
            "target_pid": 1234,
            "collector_type": "perf_cpu",
            "sample_rate": 99,
            "duration_sec": 10,
        })
        assert resp.status_code == 200
        body = resp.json()
        task_id = body["data"]["task_id"]
        assert body["data"]["status"] == "PENDING"

        # 通过详情端点确认
        detail = client.get(f"/api/tasks/{task_id}")
        assert detail.json()["data"]["status"] == "PENDING"

    def test_create_task_writes_status_event(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "test", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        events = client.get(f"/api/tasks/{task_id}/events").json()["data"]
        assert len(events) >= 1
        assert events[0]["to_status"] == "PENDING"
        assert events[0]["reason"] == "Web 请求创建任务"

    def test_create_task_writes_audit_log(self, client: TestClient):
        client.post("/api/tasks", json={
            "name": "test", "agent_id": "a2",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        log_data = client.get("/api/audit-logs").json()["data"]
        logs = log_data if isinstance(log_data, list) else log_data.get("items", [])
        assert any(log["event_type"] == "TASK_CREATED" for log in logs)

    def test_rejects_zero_duration(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
            "duration_sec": 0,
        })
        assert resp.status_code == 400

    def test_rejects_negative_sample_rate(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
            "sample_rate": -1,
        })
        assert resp.status_code == 400

    def test_rejects_too_long_duration(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
            "duration_sec": 121,
        })
        assert resp.status_code == 400

    def test_rejects_too_high_sample_rate(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
            "sample_rate": 1000,
        })
        assert resp.status_code == 400

    def test_rejects_unknown_agent(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad-agent", "agent_id": "missing_agent",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        assert resp.status_code == 404


class TestTaskListAndDetail:
    """任务列表与详情端点。"""

    def test_list_returns_empty_initially(self, client: TestClient):
        resp = client.get("/api/tasks")
        assert resp.json()["data"]["total"] == 0

    def test_list_returns_created_tasks(self, client: TestClient):
        client.post("/api/tasks", json={
            "name": "task1", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        client.post("/api/tasks", json={
            "name": "task2", "agent_id": "a1",
            "target_pid": 2, "collector_type": "ebpf_io",
        })
        resp = client.get("/api/tasks")
        assert resp.json()["data"]["total"] == 2

    def test_detail_returns_full_fields(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "detail-test", "agent_id": "a3",
            "target_pid": 9999, "collector_type": "pyspy",
            "sample_rate": 11, "duration_sec": 5,
        })
        task_id = resp.json()["data"]["task_id"]
        detail = client.get(f"/api/tasks/{task_id}").json()["data"]
        assert detail["name"] == "detail-test"
        assert detail["target_pid"] == 9999
        assert detail["collector_type"] == "pyspy"
        assert detail["sample_rate"] == 11
        assert detail["duration_sec"] == 5

    def test_nonexistent_task_returns_404(self, client: TestClient):
        resp = client.get("/api/tasks/nonexistent")
        assert resp.status_code == 404


class TestTaskEvents:
    """状态迁移事件端点。"""

    def test_events_are_returned_in_order(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "events-test", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]

        # 手动推进两步
        repo.transition_task(task_id, TaskStatus.RUNNING, "heartbeat", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "done collecting", Actor.AGENT)

        events = client.get(f"/api/tasks/{task_id}/events").json()["data"]
        statuses = [e["to_status"] for e in events]
        assert statuses == ["PENDING", "RUNNING", "UPLOADING"]

        metrics = client.get("/api/metrics").text
        assert 'mini_drop_task_transitions_total{from="NONE",to="PENDING"}' in metrics
        assert 'mini_drop_task_transitions_total{from="PENDING",to="RUNNING"}' in metrics
        assert 'mini_drop_task_transitions_total{from="RUNNING",to="UPLOADING"}' in metrics

    def test_events_404_for_nonexistent_task(self, client: TestClient):
        resp = client.get("/api/tasks/does-not-exist/events")
        assert resp.status_code == 404


class TestTaskArtifacts:
    """产物查询端点。"""

    def test_empty_artifacts_for_new_task(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "art-test", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        arts = client.get(f"/api/tasks/{task_id}/artifacts").json()["data"]
        assert arts == []

    def test_artifacts_after_result_report(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "art2", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{"artifact_type": "raw", "bucket": "mini-drop", "object_key": "tasks/x/perf.data"}])
        arts = client.get(f"/api/tasks/{task_id}/artifacts").json()["data"]
        assert len(arts) == 1
        assert arts[0]["artifact_type"] == "raw"

    def test_artifact_content_reads_local_json(self, client: TestClient, tmp_path, monkeypatch):
        monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
        top_path = tmp_path / "top.json"
        top_path.write_text('[{"name":"fib_hotspot","samples":10,"percent":80.0}]', encoding="utf-8")
        resp = client.post("/api/tasks", json={
            "name": "art-content", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "top_json",
            "filename": "top.json",
            "local_path": str(top_path),
            "content_type": "application/json",
        }])

        content = client.get(f"/api/tasks/{task_id}/artifacts/top_json/content")
        assert content.status_code == 200
        assert content.json()["data"][0]["name"] == "fib_hotspot"

    def test_artifact_content_rejects_path_outside_root(self, client: TestClient, tmp_path, monkeypatch):
        root = tmp_path / "artifacts"
        outside = tmp_path / "outside.json"
        root.mkdir()
        outside.write_text('{"secret": true}', encoding="utf-8")
        monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(root))
        resp = client.post("/api/tasks", json={
            "name": "art-content-forbidden", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "top_json",
            "filename": "top.json",
            "local_path": str(outside),
            "content_type": "application/json",
        }])

        content = client.get(f"/api/tasks/{task_id}/artifacts/top_json/content")
        assert content.status_code == 403

    def test_artifact_content_reads_minio_object(self, client: TestClient, monkeypatch):
        resp = client.post("/api/tasks", json={
            "name": "art-object", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "top_json",
            "bucket": "mini-drop",
            "object_key": f"tasks/{task_id}/top.json",
            "content_type": "application/json",
        }])
        monkeypatch.setattr(store, "read_object_bytes", lambda bucket, key: b'[{"name":"fib","samples":1}]')

        content = client.get(f"/api/tasks/{task_id}/artifacts/top_json/content")

        assert content.status_code == 200
        assert content.json()["data"][0]["name"] == "fib"

    def test_artifact_content_falls_back_to_minio_when_local_path_missing(
        self,
        client: TestClient,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
        resp = client.post("/api/tasks", json={
            "name": "art-object-fallback", "agent_id": "a1",
            "target_pid": 1, "collector_type": "pyspy",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "flamegraph_svg",
            "bucket": "mini-drop",
            "object_key": f"tasks/{task_id}/pyspy.svg",
            "local_path": str(tmp_path / task_id / "pyspy.svg"),
            "content_type": "image/svg+xml",
        }])
        monkeypatch.setattr(store, "read_object_bytes", lambda bucket, key: b"<svg></svg>")

        content = client.get(f"/api/tasks/{task_id}/artifacts/flamegraph_svg/content")

        assert content.status_code == 200
        assert content.json()["data"]["text"] == "<svg></svg>"

    def test_artifact_download_streams_minio_through_server(self, client: TestClient, monkeypatch):
        resp = client.post("/api/tasks", json={
            "name": "art-download", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "raw",
            "filename": "perf data.bin",
            "bucket": "mini-drop",
            "object_key": f"tasks/{task_id}/perf.data",
            "content_type": "application/octet-stream",
        }])
        monkeypatch.setattr(store, "stream_object", lambda bucket, key: iter([b"part-1", b"part-2"]))

        download = client.get(f"/api/tasks/{task_id}/artifacts/raw/download")

        assert download.status_code == 200
        assert download.content == b"part-1part-2"
        assert "perf%20data.bin" in download.headers["content-disposition"]

    def test_artifact_download_reads_local_file(self, client: TestClient, tmp_path, monkeypatch):
        monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
        artifact_path = tmp_path / "report.txt"
        artifact_path.write_bytes(b"local-report")
        resp = client.post("/api/tasks", json={
            "name": "local-download", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "raw",
            "filename": "report.txt",
            "local_path": str(artifact_path),
            "content_type": "text/plain",
        }])

        download = client.get(f"/api/tasks/{task_id}/artifacts/raw/download")

        assert download.status_code == 200
        assert download.content == b"local-report"


class TestStoragePresign:
    def test_generic_presign_endpoint_is_not_public(self, client: TestClient):
        resp = client.get(
            "/api/storage/presign",
            params={"bucket": "mini-drop", "key": "tasks/demo/flamegraph.svg"},
        )
        assert resp.status_code == 404


class TestDiagnose:
    """诊断触发端点。"""

    def test_diagnose_enqueues_report(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "diag", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        diag = client.post(f"/api/tasks/{task_id}/diagnose").json()["data"]
        assert diag["diagnosis_id"].startswith("diag_")
        assert diag["report_id"].startswith("report_")
        assert diag["task_id"] == task_id
        assert "summary" in diag
        assert "ranked_causes" in diag
        assert "model" in diag
        assert diag["generation_mode"] == "RULE_FALLBACK"
        assert diag["semantic_validated"] is False
        assert diag["validated"] is False
        assert len(diag["tool_results"]) >= 1
        assert diag["repair_plan"]["plan_id"].startswith("repair_")

        detail = client.get(f"/api/diagnoses/{diag['diagnosis_id']}").json()["data"]
        assert detail["run"]["task_id"] == task_id
        assert detail["run"]["status"] == "DONE"
        assert detail["run"]["validated"] is False
        assert len(detail["tool_results"]) >= 1
        history = client.get(f"/api/tasks/{task_id}/diagnoses").json()["data"]
        assert history[0]["id"] == diag["diagnosis_id"]

        metrics = client.get("/api/metrics").text
        assert 'mini_drop_diagnosis_total{status="' in metrics

        feedback = client.post(
            f"/api/diagnoses/{diag['diagnosis_id']}/feedback",
            json={
                "predicted_cause_id": "insufficient_data",
                "feedback_label": "partial",
                "feedback_note": "需要更多证据",
            },
        )
        assert feedback.status_code == 200
        assert feedback.json()["data"]["feedback_saved"] is True

    def test_diagnose_404_for_nonexistent(self, client: TestClient):
        resp = client.post("/api/tasks/nope/diagnose")
        assert resp.status_code == 404

    def test_diagnosis_detail_404_for_nonexistent(self, client: TestClient):
        resp = client.get("/api/diagnoses/diag_missing")
        assert resp.status_code == 404


class TestTaskCancellationApi:
    def test_cancel_task_records_terminal_status_event_and_audit(self, client: TestClient):
        created = client.post("/api/tasks", json={
            "name": "cancel-api",
            "agent_id": "a1",
            "target_pid": 1,
            "collector_type": "perf_cpu",
        })
        task_id = created.json()["data"]["task_id"]

        response = client.post(
            f"/api/tasks/{task_id}/cancel",
            json={"reason": "operator requested cancellation"},
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "CANCELLED"
        assert client.get(f"/api/tasks/{task_id}").json()["data"]["status"] == "CANCELLED"
        events = client.get(f"/api/tasks/{task_id}/events").json()["data"]
        assert events[-1]["to_status"] == "CANCELLED"
        audit = client.get("/api/audit-logs").json()["data"]["items"]
        assert any(item["event_type"] == "TASK_CANCELLED" for item in audit)

    def test_cancel_terminal_task_returns_conflict(self, client: TestClient):
        created = client.post("/api/tasks", json={
            "name": "already-failed",
            "agent_id": "a1",
            "target_pid": 1,
            "collector_type": "perf_cpu",
        })
        task_id = created.json()["data"]["task_id"]
        repo.transition_task(task_id, TaskStatus.RUNNING, "claimed", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.FAILED, "collector failed", Actor.AGENT)

        response = client.post(
            f"/api/tasks/{task_id}/cancel",
            json={"reason": "too late"},
        )

        assert response.status_code == 409

    def test_task_attempts_endpoint_exposes_execution_record(self, client: TestClient):
        created = client.post("/api/tasks", json={
            "name": "attempt-api",
            "agent_id": "a1",
            "target_pid": 1,
            "collector_type": "perf_cpu",
        })
        task_id = created.json()["data"]["task_id"]
        repo.transition_task(task_id, TaskStatus.RUNNING, "claimed", Actor.SERVER)

        response = client.get(f"/api/tasks/{task_id}/attempts")

        assert response.status_code == 200
        attempts = response.json()["data"]
        assert len(attempts) == 1
        assert attempts[0]["task_id"] == task_id
        assert attempts[0]["attempt_no"] == 1
        assert attempts[0]["status"] == "RUNNING"


class TestProductionFailClosed:
    def test_production_rejects_auth_disabled(self, monkeypatch):
        from server.app.main import _production_fail_closed_check

        monkeypatch.setenv("MINI_DROP_ENV", "production")
        monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("MINI_DROP_API_KEY", "k")
        with pytest.raises(RuntimeError, match="认证"):
            _production_fail_closed_check()

    def test_production_rejects_cors_wildcard(self, monkeypatch):
        from server.app.main import _production_fail_closed_check

        monkeypatch.setenv("MINI_DROP_ENV", "production")
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "true")
        monkeypatch.setenv("MINI_DROP_API_KEY", "k")
        monkeypatch.setenv("MINI_DROP_CORS_ORIGINS", "*")
        with pytest.raises(RuntimeError, match="CORS"):
            _production_fail_closed_check()

    def test_production_passes_with_secure_defaults(self, monkeypatch):
        from server.app.main import _production_fail_closed_check

        monkeypatch.setenv("MINI_DROP_ENV", "production")
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "true")
        monkeypatch.setenv("MINI_DROP_API_KEY", "k")
        monkeypatch.setenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN", "internal-secret")
        monkeypatch.setenv("MINI_DROP_CORS_ORIGINS", "https://minidrop.example.com")
        _production_fail_closed_check()  # no exception

    def test_production_rejects_default_gateway_token(self, monkeypatch):
        from server.app.main import _production_fail_closed_check

        monkeypatch.setenv("MINI_DROP_ENV", "production")
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "true")
        monkeypatch.setenv("MINI_DROP_CORS_ORIGINS", "https://minidrop.example.com")
        monkeypatch.setenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN", "mini-drop-internal-dev")
        with pytest.raises(RuntimeError, match="GATEWAY_TOKEN"):
            _production_fail_closed_check()
