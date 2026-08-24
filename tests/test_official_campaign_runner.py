from scripts.run_official_campaign import supported_tags_from_snapshots, wait_campaign


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"data": {"run_id": "campaign-1", "status": "COMPLETED"}}


class _AuthenticatedSession:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, timeout: int):
        self.urls.append(url)
        return _Response()


def test_wait_campaign_reuses_authenticated_session() -> None:
    session = _AuthenticatedSession()

    result = wait_campaign(session, "https://control.example", "campaign-1", 1)

    assert result["status"] == "COMPLETED"
    assert session.urls == [
        "https://control.example/api/v1/diagnosis-campaigns/runs/campaign-1"
    ]


def test_cpu_profile_tag_requires_observed_hot_function_and_cpu_spike() -> None:
    snapshots = {
        "baseline_snapshot": {"process_cpu_percent": 1},
        "fault_snapshot": {
            "process_cpu_percent": 92,
            "hot_function": "order_loop",
            "hot_function_samples": 80,
            "source_profile_samples": 100,
        },
        "recovery_snapshot": {"process_cpu_percent": 2},
    }

    tags = supported_tags_from_snapshots(
        "T1-CPU-001", snapshots, comparison_passed=True
    )

    assert tags == {"cpu_metric_change", "profile_hot_function"}


def test_memory_profile_tag_requires_retained_memory_and_recovery() -> None:
    snapshots = {
        "baseline_snapshot": {"process_rss_mb": 30},
        "fault_snapshot": {"process_rss_mb": 112, "retained_memory_mb": 80},
        "recovery_snapshot": {"process_rss_mb": 34},
    }

    tags = supported_tags_from_snapshots(
        "T1-MEM-001", snapshots, comparison_passed=True
    )

    assert tags == {"rss_growth", "memory_profile_growth"}


def test_noisy_neighbor_tags_include_target_refuting_measurement() -> None:
    snapshots = {
        "fault_snapshot": {
            "same_host_verified": True,
            "peer_pid": 42,
            "peer_cpu_ticks": 91,
            "process_cpu_percent": 0.4,
        }
    }

    tags = supported_tags_from_snapshots(
        "T1-NOISY-001", snapshots, comparison_passed=True
    )

    assert tags == {"peer_cpu_pressure", "target_cpu_profile"}


def test_failed_campaign_never_grants_evidence_tags() -> None:
    tags = supported_tags_from_snapshots(
        "T1-MEM-001",
        {"fault_snapshot": {"rss_mb": 112, "retained_memory_mb": 80}},
        comparison_passed=False,
    )

    assert tags == set()
