from server.app.diagnosis.route_memory import build_contextual_route_memory


def test_route_memory_prefers_successful_matching_context():
    sessions = [
        {
            "diagnosis_id": "cpu-python-success",
            "status": "COMPLETED",
            "normalized_intent": {"symptom": "cpu_saturation"},
            "target_scope": {"instances": [{"runtime": "python"}]},
            "conclusion_versions": [{"coverage": {"evidence_count": 4}}],
        },
        {
            "diagnosis_id": "io-go-failed",
            "status": "FAILED",
            "normalized_intent": {"symptom": "io_degradation"},
            "target_scope": {"instances": [{"runtime": "go"}]},
            "conclusion_versions": [],
        },
    ]
    probes = {
        "cpu-python-success": [{"probe_id": "python_cpu_profile", "status": "COMPLETED"}],
        "io-go-failed": [{"probe_id": "process_io_latency", "status": "FAILED"}],
    }

    result = build_contextual_route_memory(
        sessions,
        lambda diagnosis_id: probes[diagnosis_id],
        symptom="cpu_saturation",
        runtime="python",
    )

    assert result["ranked_routes"][0]["probe_id"] == "python_cpu_profile"
    assert result["priors"]["python_cpu_profile"] > result["priors"]["process_io_latency"]
    assert "强制执行" in result["safety_boundary"]


def test_route_memory_excludes_current_session():
    sessions = [{
        "diagnosis_id": "current",
        "status": "COMPLETED",
        "normalized_intent": {"symptom": "cpu_saturation"},
        "target_scope": {"instances": [{"runtime": "python"}]},
        "conclusion_versions": [{"coverage": {"evidence_count": 5}}],
    }]

    result = build_contextual_route_memory(
        sessions,
        lambda _: [{"probe_id": "python_cpu_profile", "status": "COMPLETED"}],
        symptom="cpu_saturation",
        runtime="python",
        exclude_diagnosis_id="current",
    )

    assert result["priors"] == {}
    assert result["ranked_routes"] == []
