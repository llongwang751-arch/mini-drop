from __future__ import annotations

import json

import pytest

from scripts.verify_interview_demo import (
    AcceptanceError,
    _accepted_terminal_status,
    _artifact_sample_count,
    _validate_lineage_artifacts,
    _validate_java_profile_content,
    _report_execution_round_indexes,
    run_acceptance,
)


@pytest.mark.parametrize(
    ("policy", "status", "accepted"),
    [
        ("AUTO", "COMPLETED", True),
        ("AUTO", "INSUFFICIENT_EVIDENCE", False),
        ("DISABLED", "COMPLETED", True),
        ("DISABLED", "INSUFFICIENT_EVIDENCE", True),
        ("DISABLED", "FAILED", False),
    ],
)
def test_acceptance_terminal_semantics(policy, status, accepted):
    assert _accepted_terminal_status(policy, status) is accepted


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"sample_count": 17}, 17),
        ({"total_samples": 211}, 211),
        ({"event_count": 9}, 9),
        ({"samples": 5}, 5),
        ({"profile_quality": {"total_samples": 23}}, 23),
    ],
)
def test_artifact_sample_count_accepts_collector_contract_fields(metadata, expected):
    assert _artifact_sample_count({"metadata": metadata}) == expected


def test_java_profile_validator_decodes_standard_content_envelope():
    class JavaContentClient:
        @staticmethod
        def request_raw(method, path):
            assert method == "GET"
            assert path.endswith("/artifacts/java_flamegraph_html/content")
            return json.dumps(
                {"code": 0, "data": {"text": "<html>Hotspot allocation</html>"}}
            ).encode()

    result = _validate_java_profile_content(
        JavaContentClient(),
        {
            "task_id": "task-java",
            "_raw_artifacts": [
                {
                    "artifact_type": "java_flamegraph_html",
                    "size_bytes": 8192,
                    "integrity_status": "VERIFIED",
                }
            ],
        },
        expected_hot_function="Hotspot",
        require_expected_hot_function=True,
    )

    assert result["expected_hot_function_found"] is True
    assert result["flamegraph_bytes"] == len("<html>Hotspot allocation</html>".encode())


def test_java_gc_validator_requires_independent_counter_window():
    class JavaContentClient:
        @staticmethod
        def request_raw(method, path):
            return json.dumps(
                {"code": 0, "data": {"text": "<html>Hotspot allocation</html>"}}
            ).encode()

    result = _validate_java_profile_content(
        JavaContentClient(),
        {
            "task_id": "task-java-gc",
            "_raw_artifacts": [
                {
                    "artifact_type": "java_flamegraph_html",
                    "size_bytes": 8192,
                    "integrity_status": "VERIFIED",
                },
                {
                    "artifact_type": "jvm_gc_metrics",
                    "size_bytes": 1024,
                    "sha256": "a" * 64,
                    "integrity_status": "VERIFIED",
                    "metadata": {
                        "delta": {
                            "allocated_bytes": 10_000_000,
                            "gc_count": 4,
                            "gc_time_ms": 21,
                        },
                        "window_duration_ms": 10_000,
                    },
                },
            ],
        },
        expected_hot_function="Hotspot",
        require_expected_hot_function=True,
        require_gc_counters=True,
    )

    assert result["jvm_gc_counters"]["gc_count_delta"] == 4
    assert result["jvm_gc_counters"]["allocated_bytes_delta"] == 10_000_000


def test_generic_decisive_collector_requires_verified_non_empty_artifact():
    result = _validate_lineage_artifacts(
        {
            "task_id": "task-memory",
            "_raw_artifacts": [
                {
                    "artifact_type": "smaps_json",
                    "size_bytes": 4096,
                    "integrity_status": "VERIFIED",
                }
            ],
        }
    )
    assert result["artifact_contract_verified"] is True
    assert result["verified_artifact_types"] == ["smaps_json"]

    with pytest.raises(AcceptanceError, match="integrity-verified"):
        _validate_lineage_artifacts(
            {
                "task_id": "task-empty",
                "_raw_artifacts": [
                    {
                        "artifact_type": "smaps_json",
                        "size_bytes": 0,
                        "integrity_status": "VERIFIED",
                    }
                ],
            }
        )


class FakeInterviewClient:
    """Contract-shaped API double for the durable interview acceptance."""

    def __init__(
        self,
        *,
        include_hot_function: bool = True,
        scenario_id: str = "source-hotspot",
        report_evidence_mode: str = "correct",
        disabled_expected_profile: bool = True,
        auto_failed_followup: bool = False,
    ) -> None:
        if report_evidence_mode not in {"correct", "missing", "wrong_collector"}:
            raise ValueError("unsupported report evidence mode")
        self.include_hot_function = include_hot_function
        self.scenario_id = scenario_id
        self.report_evidence_mode = report_evidence_mode
        self.disabled_expected_profile = disabled_expected_profile
        self.auto_failed_followup = auto_failed_followup
        self.is_go = scenario_id == "go-cpu-hotspot"
        self.process_name = "go-hotspot" if self.is_go else "python-hotspot"
        self.collector_type = "go_pprof" if self.is_go else "pyspy"
        self.tool_name = "collect_go_profile" if self.is_go else "start_pyspy_profile"
        self.hot_function = "main.goCPUHotFunction" if self.is_go else "source_hot_function"
        self.skill_id = "skill-go-runtime-v1" if self.is_go else "skill-python-runtime-v1"
        self.skill_family = (
            "repository:go-runtime-diagnosis"
            if self.is_go
            else "repository:python-runtime-diagnosis"
        )
        self.fault_active = False
        self.stop_calls = 0
        self._diagnosis_count = 0
        self._diagnoses: dict[str, dict] = {}
        self._planner_called: set[str] = set()

    def last_header(self, name: str) -> str:
        return "grpc" if name.lower() == "x-mini-drop-ai-transport" else ""

    def request(self, method: str, path: str, payload=None, **_kwargs):
        if method == "GET" and path == "/api/healthz":
            return {"status": "ok"}
        if method == "GET" and path == "/api/agents?limit=1000":
            return {"items": [{"id": "control-interview-demo-agent", "status": "ONLINE"}]}
        if method == "GET" and path.startswith("/api/top-processes?"):
            return {
                "authoritative": True,
                "fresh": True,
                "items": [{"pid": 4242, "comm": self.process_name}],
            }
        if method == "GET" and path == "/api/v2/showcases/fault-plaza":
            scenarios = [
                {
                    "scenario_id": self.scenario_id,
                    "active": self.fault_active,
                    "supports_skill_ab": True,
                    "minimum_diagnosis_rounds": 3,
                    "safety": {"allow_listed": True},
                }
            ]
            scenarios.extend(
                {
                    "scenario_id": f"safe-demo-{index}",
                    "active": False,
                    "supports_skill_ab": False,
                    "safety": {"allow_listed": True},
                }
                for index in range(14)
            )
            return {
                "status": "READY",
                "production_safe": False,
                "scenarios": scenarios,
            }
        if method == "GET" and path == "/api/v2/diagnostic-skills":
            return {
                "items": [
                    {
                        "skill_id": self.skill_id,
                        "family_key": self.skill_family,
                        "status": "ACTIVE",
                    }
                ]
            }
        if method == "POST" and path.endswith(f"/{self.scenario_id}/start"):
            assert payload == {"duration_seconds": 60}
            self.fault_active = True
            return {
                "status": "RUNNING",
                "scenario": {"scenario_id": self.scenario_id, "active": True},
                "diagnosis_request": {
                    "query": (
                        "diagnose the controlled Go CPU hotspot"
                        if self.is_go
                        else "diagnose the controlled source hotspot"
                    ),
                    "auto_scope": True,
                    "budget": {
                        "min_diagnosis_rounds": 3,
                        "max_diagnosis_rounds": 4,
                    },
                },
            }
        if method == "POST" and path.endswith(f"/{self.scenario_id}/stop"):
            # The public stop route intentionally has no JSON request body.
            assert payload is None
            self.fault_active = False
            self.stop_calls += 1
            return {"status": "STOPPED", "scenario": {"active": False}}
        if method == "POST" and path == "/api/v2/diagnoses":
            assert payload["mode"] == "AUTONOMOUS"
            assert payload["auto_scope"] is True
            assert payload["target"] == {}
            assert payload["skill_policy"] in {"DISABLED", "AUTO"}
            assert payload["budget"]["min_diagnosis_rounds"] == 3
            assert payload["budget"]["max_diagnosis_rounds"] == 4
            self._diagnosis_count += 1
            diagnosis_id = f"diagnosis-{self._diagnosis_count}"
            self._diagnoses[diagnosis_id] = {
                "status": "COMPLETED",
                "target": {
                    "agent_id": "control-interview-demo-agent",
                    "pid": 4242,
                    "process_binding": {
                        "agent_id": "control-interview-demo-agent",
                        "pid": 4242,
                        "boot_id": "boot-interview-demo",
                        "process_start_ticks": 123456,
                        "pid_namespace_inode": 4026531836,
                        "namespace_pid": 4242,
                        "executable_identity": f"sha256:{self.process_name}-demo",
                    },
                },
                "skill_policy": payload["skill_policy"],
            }
            return {"diagnosis_id": diagnosis_id}

        if path.startswith("/api/v2/diagnoses/"):
            return self._diagnosis_api(method, path, payload)
        if path.startswith("/api/tasks/task-"):
            return self._task_api(method, path)
        raise AssertionError((method, path, payload))

    def _diagnosis_api(self, method: str, path: str, payload):
        parts = path.split("/")
        diagnosis_id = parts[4]
        diagnosis = self._diagnoses[diagnosis_id]
        suffix = "/".join(parts[5:])
        task_id = f"task-{diagnosis_id}"
        if method == "GET" and not suffix:
            return diagnosis
        if method == "POST" and suffix == "planner/run":
            assert payload == {}
            self._planner_called.add(diagnosis_id)
            return {"planner_kind": "MODEL", "category": "CPU"}
        if method == "GET" and suffix == "tool-calls":
            if diagnosis_id not in self._planner_called:
                return []
            profile_call = {
                "tool_call_id": f"call-{diagnosis_id}",
                "tool_name": self.tool_name,
                "task_id": task_id,
                "status": "DONE",
                "policy_decision": "ALLOW",
            }
            if diagnosis["skill_policy"] == "AUTO":
                if self.auto_failed_followup:
                    return [
                        profile_call,
                        {
                            "tool_call_id": f"call-followup-{diagnosis_id}",
                            "tool_name": "start_perf_profile",
                            "task_id": f"task-followup-{diagnosis_id}",
                            "status": "FAILED",
                            "policy_decision": "ALLOW",
                        },
                    ]
                return [profile_call]
            baseline_call = {
                "tool_call_id": f"call-baseline-{diagnosis_id}",
                "tool_name": "collect_sys_metrics",
                "status": "DONE",
                "policy_decision": "ALLOW",
            }
            if not self.disabled_expected_profile:
                baseline_call["task_id"] = task_id
                return [baseline_call]
            return [baseline_call, profile_call]
        if method == "GET" and suffix == "evidence":
            is_disabled_baseline = (
                diagnosis["skill_policy"] == "DISABLED"
                and not self.disabled_expected_profile
            )
            evidence = [
                {
                    "evidence_id": f"evidence-{diagnosis_id}",
                    "role": "SUPPORTS",
                    "classification": {
                        "decision": "USABLE",
                        "can_support_conclusion": True,
                    },
                    "envelope": {
                        "source": {
                            "task_id": task_id,
                            "task_attempt_id": f"attempt-{diagnosis_id}",
                            "artifact_id": (
                                f"raw-{diagnosis_id}"
                                if is_disabled_baseline
                                else f"flame-{diagnosis_id}"
                            ),
                            "artifact_sha256": "a" * 64,
                            "analysis_job_id": f"analysis-{diagnosis_id}",
                        },
                        "quality": {
                            "level": "CONCLUSION_GRADE",
                            "sample_count": 150,
                            "analyzer_validated": True,
                        },
                    },
                }
            ]
            if self.report_evidence_mode == "wrong_collector":
                evidence.append(
                    {
                        "evidence_id": f"evidence-decoy-{diagnosis_id}",
                        "role": "SUPPORTS",
                        "classification": {
                            "decision": "USABLE",
                            "can_support_conclusion": True,
                        },
                        "envelope": {
                            "source": {
                                "task_id": f"task-other-{diagnosis_id}",
                                "task_attempt_id": f"attempt-other-{diagnosis_id}",
                                "artifact_id": f"flame-other-{diagnosis_id}",
                                "artifact_sha256": "c" * 64,
                                "analysis_job_id": f"analysis-other-{diagnosis_id}",
                            },
                            "quality": {
                                "level": "CONCLUSION_GRADE",
                                "sample_count": 150,
                                "analyzer_validated": True,
                            },
                        },
                    }
                )
            return evidence
        if method == "GET" and suffix == "reports":
            if self.report_evidence_mode == "missing":
                evidence_refs = []
            elif (
                self.report_evidence_mode == "wrong_collector"
                and diagnosis["skill_policy"] == "AUTO"
            ):
                evidence_refs = [f"evidence-decoy-{diagnosis_id}"]
            else:
                evidence_refs = [f"evidence-{diagnosis_id}"]
            return [
                {
                    "report_id": f"report-{diagnosis_id}-{round_index}",
                    "hypothesis_id": (
                        f"hypothesis-{diagnosis_id}-{round_index}"
                    ),
                    "confidence": 0.94,
                    "evidence_refs": evidence_refs,
                }
                for round_index in range(1, 4)
            ]
        if method == "GET" and suffix == "hypotheses":
            return [
                {
                    "hypothesis_id": f"hypothesis-{diagnosis_id}-{round_index}",
                    "round_index": round_index,
                }
                for round_index in range(1, 4)
            ]
        if method == "GET" and suffix == "events":
            return [
                {
                    "event_type": "diagnosis.created",
                    "sequence": 1,
                    "actor": "USER",
                    "payload": {},
                },
                {
                    "event_type": "lats.node_selected",
                    "sequence": 2,
                    "actor": "DIAGNOSIS_AGENT",
                    "payload": {
                        "round_index": 1,
                        "iteration": 1,
                        "node_id": f"hypothesis:hypothesis-{diagnosis_id}-1",
                    },
                },
                {
                    "event_type": "lats.node_selected",
                    "sequence": 3,
                    "actor": "DIAGNOSIS_AGENT",
                    "payload": {
                        "round_index": 2,
                        "iteration": 2,
                        "node_id": f"hypothesis:hypothesis-{diagnosis_id}-2",
                    },
                },
                {
                    "event_type": "lats.node_selected",
                    "sequence": 4,
                    "actor": "DIAGNOSIS_AGENT",
                    "payload": {
                        "round_index": 2,
                        "iteration": 3,
                        "node_id": f"hypothesis:hypothesis-{diagnosis_id}-3",
                    },
                },
            ]
        if method == "GET" and suffix == "exploration-tree":
            return {"version": 3, "nodes": [{"node_id": "root"}]}
        if method == "GET" and suffix == "diagnostic-skill-activations":
            if diagnosis["skill_policy"] == "DISABLED":
                return []
            return [
                {
                    "activation_id": f"activation-{diagnosis_id}",
                    "skill_id": self.skill_id,
                    "match_score": 0.99,
                    "selected_tool": self.tool_name,
                    "outcome": "COMPLETED",
                }
            ]
        if method == "GET" and suffix == "budget":
            return {"tool_calls_used": 1, "diagnosis_rounds_used": 3}
        raise AssertionError((method, path, payload))

    def _task_api(self, method: str, path: str):
        assert method == "GET"
        parts = path.split("/")
        task_id = parts[3]
        suffix = "/".join(parts[4:])
        is_failed_followup = task_id.startswith("task-followup-")
        diagnosis_id = task_id.removeprefix(
            "task-followup-" if is_failed_followup else "task-"
        )
        diagnosis = self._diagnoses[diagnosis_id]
        is_disabled_baseline = (
            diagnosis["skill_policy"] == "DISABLED"
            and not self.disabled_expected_profile
        )
        if not suffix:
            if is_failed_followup:
                return {
                    "id": task_id,
                    "status": "FAILED",
                    "status_reason": "analysis input rejected",
                    "error_code": "ANALYSIS_INPUT_INVALID",
                    "collector_type": "perf",
                    "collection_status": "COLLECTED",
                    "analysis_status": "FAILED",
                }
            return {
                "id": task_id,
                "status": "DONE",
                "collector_type": (
                    "sys_metrics" if is_disabled_baseline else self.collector_type
                ),
                "collection_status": "COLLECTED",
                "analysis_status": "SUCCESS",
            }
        if suffix == "events":
            return [
                {
                    "sequence": 1,
                    "to_status": "FAILED" if is_failed_followup else "DONE",
                }
            ]
        if suffix == "attempts":
            return [
                {
                    "attempt_id": f"attempt-{task_id}",
                    "status": "FAILED" if is_failed_followup else "DONE",
                }
            ]
        if suffix == "artifacts":
            if is_failed_followup:
                return [
                    {
                        "id": f"raw-followup-{diagnosis_id}",
                        "artifact_type": "perf_data",
                        "size_bytes": 4096,
                        "sha256": "d" * 64,
                        "integrity_status": "VERIFIED",
                    }
                ]
            if is_disabled_baseline:
                return [
                    {
                        "id": f"raw-{diagnosis_id}",
                        "artifact_type": "system_metrics_json",
                        "size_bytes": 1024,
                        "sha256": "0" * 64,
                        "integrity_status": "VERIFIED",
                    }
                ]
            metadata = {
                "sample_count": 150,
                "profile_quality": {"sample_count": 150},
            }
            return [
                {
                    "id": f"raw-{diagnosis_id}",
                    "artifact_type": "raw",
                    "size_bytes": 4096,
                    "sha256": "0" * 64,
                    "integrity_status": "VERIFIED",
                },
                {
                    "id": f"flame-{diagnosis_id}",
                    "artifact_type": "flamegraph_json",
                    "size_bytes": 2048,
                    "sha256": "a" * 64,
                    "integrity_status": "VERIFIED",
                    "metadata": metadata,
                },
                {
                    "id": f"top-{diagnosis_id}",
                    "artifact_type": "top_json",
                    "size_bytes": 1024,
                    "sha256": "b" * 64,
                    "integrity_status": "VERIFIED",
                    "metadata": metadata,
                },
            ]
        if suffix == "artifacts/flamegraph_json/content":
            return {
                "name": "all",
                "value": 150,
                "children": [{"name": self.hot_function, "value": 120}],
            }
        if suffix == "artifacts/top_json/content":
            name = self.hot_function if self.include_hot_function else "idle"
            return [{"name": name, "samples": 120}]
        raise AssertionError((method, path))


def test_acceptance_proves_two_fresh_arms_and_redacts_request_text() -> None:
    client = FakeInterviewClient()

    report = run_acceptance(
        client,
        fault_duration_seconds=60,
        timeout_seconds=2,
        warmup_seconds=0,
        poll_seconds=0.01,
    )

    assert report["passed"] is True
    assert report["fresh_diagnosis_ids"] == ["diagnosis-1", "diagnosis-2"]
    assert report["arms"]["disabled"]["skill_activations"] == []
    assert report["arms"]["auto"]["skill_activations"][0]["skill_id"] == "skill-python-runtime-v1"
    assert report["arms"]["auto"]["profile"]["expected_hot_function_found"] is True
    assert report["arms"]["disabled"]["tool_route"] == [
        "collect_sys_metrics",
        "start_pyspy_profile",
    ]
    assert report["arms"]["auto"]["tool_route"] == ["start_pyspy_profile"]
    assert report["skill_ab_comparison"]["auto_expected_profile_hit"] is True
    assert report["cleanup"] == {"fault_active": False, "verified": True}
    assert client.stop_calls == 2
    serialized = json.dumps(report)
    assert "diagnose the controlled source hotspot" not in serialized
    assert "X-API-Key_FROM_ENV_REDACTED" in serialized


def test_disabled_expected_profile_miss_is_recorded_for_skill_comparison() -> None:
    client = FakeInterviewClient(disabled_expected_profile=False)

    report = run_acceptance(
        client,
        fault_duration_seconds=60,
        timeout_seconds=2,
        warmup_seconds=0,
        poll_seconds=0.01,
    )

    disabled = report["arms"]["disabled"]
    auto = report["arms"]["auto"]
    assert report["passed"] is True
    assert disabled["tool_route"] == ["collect_sys_metrics"]
    assert disabled["profile"]["match_status"] == "EXPECTED_COLLECTOR_NOT_EXECUTED"
    assert disabled["expected_profile_match"] == {
        "expected_collector": "pyspy",
        "expected_collector_found": False,
        "expected_collector_done": False,
        "expected_hot_function": "source_hot_function",
        "expected_hot_function_found": False,
        "supporting_evidence_found": False,
        "report_cites_supporting_evidence": False,
    }
    assert auto["tool_route"] == ["start_pyspy_profile"]
    assert auto["expected_profile_match"]["expected_hot_function_found"] is True
    assert report["skill_ab_comparison"] == {
        "expected_collector": "pyspy",
        "expected_hot_function": "source_hot_function",
        "disabled_expected_profile_hit": False,
        "auto_expected_profile_hit": True,
        "disabled_tool_route": ["collect_sys_metrics"],
        "auto_tool_route": ["start_pyspy_profile"],
        "first_tool_diverged": True,
        "complete_route_diverged": True,
        "route_treatment_observed": True,
        "disabled_terminal_status": "COMPLETED",
        "auto_terminal_status": "COMPLETED",
        "disabled_supporting_evidence_count": 1,
        "auto_supporting_evidence_count": 1,
        "supporting_evidence_delta": 0,
    }
    assert client.fault_active is False
    assert client.stop_calls == 2


def test_noncritical_failed_followup_is_recorded_without_failing_auto_arm() -> None:
    client = FakeInterviewClient(
        scenario_id="go-cpu-hotspot",
        auto_failed_followup=True,
    )

    report = run_acceptance(
        client,
        scenario_id="go-cpu-hotspot",
        fault_duration_seconds=60,
        timeout_seconds=2,
        warmup_seconds=0,
        poll_seconds=0.01,
    )

    auto = report["arms"]["auto"]
    assert report["passed"] is True
    assert auto["tool_route"] == ["collect_go_profile", "start_perf_profile"]
    assert auto["expected_profile_match"] == {
        "expected_collector": "go_pprof",
        "expected_collector_found": True,
        "expected_collector_done": True,
        "expected_hot_function": "goCPUHotFunction",
        "expected_hot_function_found": True,
        "supporting_evidence_found": True,
        "report_cites_supporting_evidence": True,
    }
    failed_task = next(item for item in auto["tasks"] if item["status"] == "FAILED")
    assert failed_task["collector_type"] == "perf"
    assert failed_task["collection_status"] == "COLLECTED"
    assert failed_task["analysis_status"] == "FAILED"
    assert failed_task["error_code"] == "ANALYSIS_INPUT_INVALID"
    assert failed_task["artifacts"][0]["sha256"] == "d" * 64
    failed_route = auto["tool_route_observations"][1]
    assert failed_route == {
        "tool_call_id": "call-followup-diagnosis-2",
        "tool_name": "start_perf_profile",
        "task_id": "task-followup-diagnosis-2",
        "tool_call_status": "FAILED",
        "task_status": "FAILED",
        "collection_status": "COLLECTED",
        "analysis_status": "FAILED",
        "error_code": "ANALYSIS_INPUT_INVALID",
    }
    assert report["cleanup"] == {"fault_active": False, "verified": True}
    assert client.stop_calls == 2


def test_collected_failed_followup_still_requires_artifact_sha256() -> None:
    class InvalidArtifactClient(FakeInterviewClient):
        def _task_api(self, method: str, path: str):
            response = super()._task_api(method, path)
            if "task-followup-" in path and path.endswith("/artifacts"):
                response[0]["sha256"] = "invalid"
            return response

    client = InvalidArtifactClient(
        scenario_id="go-cpu-hotspot",
        auto_failed_followup=True,
    )

    with pytest.raises(AcceptanceError, match="lacks a SHA-256"):
        run_acceptance(
            client,
            scenario_id="go-cpu-hotspot",
            fault_duration_seconds=60,
            timeout_seconds=2,
            warmup_seconds=0,
            poll_seconds=0.01,
        )

    assert client.stop_calls == 2
    assert client.fault_active is False


def test_acceptance_stops_fault_when_auto_profile_proof_fails() -> None:
    client = FakeInterviewClient(include_hot_function=False)

    with pytest.raises(AcceptanceError, match="source_hot_function"):
        run_acceptance(
            client,
            fault_duration_seconds=60,
            timeout_seconds=2,
            warmup_seconds=0,
            poll_seconds=0.01,
        )

    assert client.fault_active is False
    # DISABLED records its miss; the AUTO arm remains a hard failure and is
    # independently cleaned up.
    assert client.stop_calls == 2


def test_acceptance_supports_a_real_go_pprof_contract() -> None:
    client = FakeInterviewClient(scenario_id="go-cpu-hotspot")

    report = run_acceptance(
        client,
        scenario_id="go-cpu-hotspot",
        fault_duration_seconds=60,
        timeout_seconds=2,
        warmup_seconds=0,
        poll_seconds=0.01,
    )

    assert report["passed"] is True
    assert report["preflight"]["expected_collector"] == "go_pprof"
    assert report["preflight"]["expected_hot_function"] == "goCPUHotFunction"
    assert report["arms"]["disabled"]["profile"]["matched_hot_function"] == (
        "main.goCPUHotFunction"
    )
    assert report["arms"]["auto"]["skill_activations"][0]["skill_id"] == (
        "skill-go-runtime-v1"
    )


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        (
            "missing",
            r"diagnosis diagnosis-1 Report has no Evidence references",
        ),
        (
            "wrong_collector",
            r"diagnosis diagnosis-2 Report does not cite supporting go_pprof Evidence",
        ),
    ],
)
def test_go_acceptance_rejects_missing_or_wrong_report_evidence_refs(
    mode: str,
    message: str,
) -> None:
    client = FakeInterviewClient(
        scenario_id="go-cpu-hotspot",
        report_evidence_mode=mode,
    )

    with pytest.raises(AcceptanceError, match=message):
        run_acceptance(
            client,
            scenario_id="go-cpu-hotspot",
            fault_duration_seconds=60,
            timeout_seconds=2,
            warmup_seconds=0,
            poll_seconds=0.01,
        )

    assert client.stop_calls == (2 if mode == "wrong_collector" else 1)
    assert client.fault_active is False


def test_report_rounds_use_selection_iteration_for_an_old_sibling() -> None:
    reports = [
        {"hypothesis_id": "round-1"},
        {"hypothesis_id": "round-2"},
        {"hypothesis_id": "old-sibling"},
    ]
    hypotheses = [
        {"hypothesis_id": "round-1", "round_index": 1},
        {"hypothesis_id": "round-2", "round_index": 2},
        {"hypothesis_id": "old-sibling", "round_index": 2},
    ]
    events = [
        {
            "event_type": "lats.node_selected",
            "payload": {"node_id": "hypothesis:round-1", "iteration": 1},
        },
        {
            "event_type": "lats.node_selected",
            "payload": {"node_id": "hypothesis:round-2", "iteration": 2},
        },
        {
            "event_type": "lats.node_selected",
            "payload": {"node_id": "hypothesis:old-sibling", "iteration": 3},
        },
    ]

    assert _report_execution_round_indexes(reports, hypotheses, events) == [1, 2, 3]


def test_report_rounds_fall_back_to_hypothesis_birth_round() -> None:
    reports = [
        {"hypothesis_id": "selected"},
        {"hypothesis_id": "legacy"},
    ]
    hypotheses = [
        {"hypothesis_id": "selected", "round_index": 1},
        {"hypothesis_id": "legacy", "round_index": 2},
    ]
    events = [
        {
            "event_type": "lats.node_selected",
            "payload": {"node_id": "hypothesis:selected", "iteration": 1},
        }
    ]

    assert _report_execution_round_indexes(reports, hypotheses, events) == [1, 2]
