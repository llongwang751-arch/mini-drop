"""Real Campaign state machine tests with a deterministic controlled target."""

import time

from server.app.diagnosis.campaign_runs import CampaignManager


class FakeTarget:
    def __init__(self) -> None:
        self.active = True
        self.values = iter([1.0, 2.0, 96.0, 3.0, 2.0])

    def health(self):
        return {"status": "ok"}

    def stop_cpu(self):
        self.active = False
        return {"status": "stopped"}

    def start_cpu(self, duration_seconds):
        self.active = True
        return {"status": "started", "duration_seconds": duration_seconds}

    def snapshot(self):
        return {
            "process_cpu_percent": next(self.values),
            "fault_active": self.active,
            "host_pid": 4321,
            "operation_count": 100,
        }


class FakeMemoryTarget:
    def __init__(self) -> None:
        self.active = False
        self.values = iter(
            [
                (20.0, 0.0),
                (84.0, 64.0),
                (84.0, 64.0),
                (22.0, 0.0),
            ]
        )

    def health(self):
        return {"status": "ok"}

    def stop_memory(self):
        self.active = False
        return {"status": "stopped"}

    def start_memory(self, duration_seconds, megabytes):
        self.active = True
        return {
            "status": "started",
            "duration_seconds": duration_seconds,
            "megabytes": megabytes,
        }

    def snapshot(self):
        rss, retained = next(self.values)
        return {
            "process_rss_mb": rss,
            "retained_memory_mb": retained,
            "memory_fault_active": self.active,
            "host_pid": 5432,
        }


class FakeIoTarget:
    def __init__(self) -> None:
        self.active = False
        self.values = iter(
            [
                (1024, 0),
                (10 * 1024 * 1024, 8 * 1024 * 1024),
                (10 * 1024 * 1024, 8 * 1024 * 1024),
                (10 * 1024 * 1024, 8 * 1024 * 1024),
            ]
        )

    def health(self):
        return {"status": "ok"}

    def stop_io(self):
        self.active = False
        return {"status": "stopped"}

    def start_io(self, duration_seconds):
        self.active = True
        return {"status": "started", "duration_seconds": duration_seconds}

    def snapshot(self):
        write_bytes, workload_bytes = next(self.values)
        return {
            "process_write_bytes": write_bytes,
            "io_workload_bytes": workload_bytes,
            "io_fault_active": self.active,
            "host_pid": 6543,
        }


class FakeDownstreamTarget:
    def __init__(self) -> None:
        self.active = False

    def health(self):
        return {"status": "ok"}

    def stop_cpu(self):
        return {"status": "stopped"}

    def stop_memory(self):
        return {"status": "stopped"}

    def stop_io(self):
        return {"status": "stopped"}

    def stop_downstream(self):
        self.active = False
        return {"status": "stopped"}

    def start_downstream(self, duration_seconds, delay_ms):
        self.active = True
        return {"status": "started", "delay_ms": delay_ms}

    def probe_downstream(self):
        return {
            "upstream_latency_ms": 760.0 if self.active else 2.0,
            "downstream_fault_active": self.active,
            "downstream_delay_ms": 750 if self.active else 0,
            "applied_delay_ms": 750 if self.active else 0,
            "host_pid": 7654,
        }


class FakeNetworkTarget:
    def __init__(self) -> None:
        self.active = False

    def health(self):
        return {"status": "ok"}

    def stop_cpu(self):
        return {"status": "stopped"}

    def stop_memory(self):
        return {"status": "stopped"}

    def stop_io(self):
        return {"status": "stopped"}

    def stop_downstream(self):
        return {"status": "stopped"}

    def stop_network(self):
        self.active = False
        return {"status": "stopped"}

    def start_network(self, duration_seconds, delay_ms):
        self.active = True
        return {"status": "started", "delay_ms": delay_ms}

    def probe_network(self):
        return {
            "upstream_latency_ms": 660.0 if self.active else 3.0,
            "network_fault_active": self.active,
            "network_delay_ms": 650 if self.active else 0,
            "network_proxy_delay_ms": 650 if self.active else 0,
            "host_pid": 8765,
        }


class FakeGcTarget:
    def __init__(self) -> None:
        self.active = False
        self.snapshots = iter(
            [
                (10, 20, 0, 0.0),
                (14, 36, 4, 4.0),
                (14, 36, 4, 4.0),
                (14, 36, 4, 4.0),
            ]
        )

    def health(self):
        return {"status": "ok"}

    def stop_gc(self):
        self.active = False
        return {"status": "stopped"}

    def start_gc(self, duration_seconds):
        self.active = True
        return {"status": "started", "duration_seconds": duration_seconds}

    def snapshot(self):
        count, total_ms, injected, pause_ms = next(self.snapshots)
        return {
            "gc_collection_count": count,
            "gc_collection_time_ms": total_ms,
            "injected_gc_cycles": injected,
            "last_gc_pause_ms": pause_ms,
            "heap_used_mb": 42.0,
            "gc_fault_active": self.active,
            "host_pid": 9876,
        }


class FakeSourceTarget:
    def __init__(self) -> None:
        self.active = False

    def health(self):
        return {"status": "ok"}

    def stop_cpu(self):
        return {"status": "stopped"}

    def stop_source(self):
        self.active = False
        return {"status": "stopped"}

    def start_source(self, duration_seconds):
        self.active = True
        return {"status": "started", "duration_seconds": duration_seconds}

    def snapshot(self):
        return {
            "source_fault_active": self.active,
            "source_profile_samples": 12 if self.active else 0,
            "hot_function": "source_hot_function" if self.active else "",
            "source_file": "app.py" if self.active else "",
            "source_line": 97 if self.active else 0,
            "hot_function_samples": 12 if self.active else 0,
            "host_pid": 10987,
        }


class FakeRateTarget:
    def __init__(self) -> None:
        self.kind = None

    def health(self):
        return {"status": "ok"}

    def stop_noisy(self): self.kind = None; return {"status": "stopped"}
    def start_noisy(self, duration_seconds): self.kind = "noisy"; return {"status": "started"}
    def stop_load(self): self.kind = None; return {"status": "stopped"}
    def start_load(self, duration_seconds): self.kind = "load"; return {"status": "started"}
    def stop_queue(self): self.kind = None; return {"status": "stopped"}
    def start_queue(self, duration_seconds): self.kind = "queue"; return {"status": "started"}

    def snapshot(self):
        base = {"host_pid": 12001, "process_cpu_percent": 2.0}
        if self.kind == "noisy":
            return {**base, "noisy_neighbor_active": True, "same_host_verified": True, "peer_pid": 12002, "peer_cpu_ticks": 200}
        if self.kind == "load":
            return {**base, "load_fault_active": True, "load_offered_rps": 400.0, "load_completed_rps": 120.0, "load_rejected_requests": 40, "load_queue_depth": 48, "load_latency_ms": 90.0}
        if self.kind == "queue":
            return {**base, "queue_fault_active": True, "producer_rate": 240.0, "consumer_rate": 35.0, "queue_lag": 64, "queue_queue_depth": 64}
        return {**base, "noisy_neighbor_active": False, "peer_cpu_ticks": 0, "load_fault_active": False, "load_queue_depth": 0, "load_rejected_requests": 0, "queue_fault_active": False, "queue_lag": 0}

def test_cpu_campaign_exposes_real_stages_oracle_and_cleanup(monkeypatch):
    monkeypatch.setenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0")
    manager = CampaignManager(target=FakeTarget())
    created = manager.create("LIVE-CPU-001")
    deadline = time.time() + 3
    current = created
    while current["status"] == "RUNNING" and time.time() < deadline:
        time.sleep(0.01)
        current = manager.get(created["run_id"])

    assert current is not None
    assert current["status"] == "COMPLETED"
    assert set(current["snapshots"]) == {
        "baseline_snapshot",
        "fault_snapshot",
        "recovery_snapshot",
    }
    assert current["diagnosis"]["root_cause"] == "SELF_CODE_CPU_HOTSPOT"
    assert current["comparison"]["root_cause_match"] is True
    assert current["comparison"]["passed"] is True
    assert current["cleanup"]["attempted"] is True
    assert current["cleanup"]["succeeded"] is True
    stages = {event["stage"] for event in current["events"]}
    assert {
        "PRECHECK_PASSED",
        "BASELINE_CAPTURED",
        "FAULT_INJECTED",
        "FAULT_CONFIRMED",
        "TASK_LINKED",
        "DIAGNOSIS_COMPLETED",
        "ORACLE_COMPARED",
        "RECOVERY_STARTED",
        "RECOVERY_VERIFIED",
    }.issubset(stages)


def test_memory_campaign_tracks_growth_oracle_and_cleanup(monkeypatch):
    monkeypatch.setenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0")
    manager = CampaignManager(target=FakeMemoryTarget())
    created = manager.create("LIVE-MEM-001")
    deadline = time.time() + 3
    current = created
    while current["status"] == "RUNNING" and time.time() < deadline:
        time.sleep(0.01)
        current = manager.get(created["run_id"])

    assert current is not None
    assert current["status"] == "COMPLETED"
    assert current["snapshots"]["baseline_snapshot"]["process_rss_mb"] == 20.0
    assert current["snapshots"]["fault_snapshot"]["process_rss_mb"] == 84.0
    assert current["diagnosis"]["root_cause"] == "SELF_CODE_RETAINED_MEMORY"
    assert current["comparison"]["benchmark_case_id"] == "T1-MEM-001"
    assert current["comparison"]["passed"] is True
    assert current["cleanup"]["succeeded"] is True


def test_io_campaign_tracks_write_growth_oracle_and_cleanup(monkeypatch):
    monkeypatch.setenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0")
    manager = CampaignManager(target=FakeIoTarget())
    created = manager.create("LIVE-IO-001")
    deadline = time.time() + 3
    current = created
    while current["status"] == "RUNNING" and time.time() < deadline:
        time.sleep(0.01)
        current = manager.get(created["run_id"])

    assert current is not None
    assert current["status"] == "COMPLETED"
    assert current["diagnosis"]["root_cause"] == "SELF_CODE_SYNC_IO"
    assert current["comparison"]["benchmark_case_id"] == "T1-IO-001"
    assert current["comparison"]["passed"] is True
    assert current["cleanup"]["succeeded"] is True


def test_downstream_campaign_localizes_dependency_edge_and_recovers(monkeypatch):
    monkeypatch.setenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0")
    manager = CampaignManager(target=FakeDownstreamTarget())
    created = manager.create("LIVE-DOWNSTREAM-001")
    deadline = time.time() + 3
    current = created
    while current["status"] == "RUNNING" and time.time() < deadline:
        time.sleep(0.01)
        current = manager.get(created["run_id"])

    assert current is not None
    assert current["status"] == "COMPLETED"
    assert current["diagnosis"]["root_cause"] == "DOWNSTREAM_SERVICE_LATENCY"
    assert current["comparison"]["benchmark_case_id"] == "T1-DOWNSTREAM-001"
    assert current["snapshots"]["fault_snapshot"]["upstream_latency_ms"] == 760.0
    assert current["snapshots"]["recovery_snapshot"]["downstream_fault_active"] is False
    assert current["comparison"]["passed"] is True
    assert current["cleanup"]["succeeded"] is True


def test_network_campaign_distinguishes_edge_latency_and_recovers(monkeypatch):
    monkeypatch.setenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0")
    manager = CampaignManager(target=FakeNetworkTarget())
    created = manager.create("LIVE-NET-001")
    deadline = time.time() + 3
    current = created
    while current["status"] == "RUNNING" and time.time() < deadline:
        time.sleep(0.01)
        current = manager.get(created["run_id"])

    assert current is not None
    assert current["status"] == "COMPLETED"
    assert current["diagnosis"]["root_cause"] == "DEPENDENCY_EDGE_NETWORK_LATENCY"
    assert current["comparison"]["benchmark_case_id"] == "T1-NET-001"
    assert current["snapshots"]["fault_snapshot"]["network_delay_ms"] == 650
    assert current["snapshots"]["recovery_snapshot"]["network_fault_active"] is False
    assert current["comparison"]["passed"] is True
    assert current["cleanup"]["succeeded"] is True


def test_gc_campaign_tracks_real_gc_pause_and_recovers(monkeypatch):
    monkeypatch.setenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0")
    target = FakeGcTarget()
    manager = CampaignManager(target=target, gc_target=target)
    created = manager.create("LIVE-GC-001")
    deadline = time.time() + 3
    current = created
    while current["status"] == "RUNNING" and time.time() < deadline:
        time.sleep(0.01)
        current = manager.get(created["run_id"])

    assert current is not None
    assert current["status"] == "COMPLETED"
    assert current["diagnosis"]["root_cause"] == "MANUAL_FULL_GC_PRESSURE"
    assert current["comparison"]["benchmark_case_id"] == "T1-GC-001"
    assert current["snapshots"]["fault_snapshot"]["injected_gc_cycles"] == 4
    assert current["snapshots"]["recovery_snapshot"]["gc_fault_active"] is False
    assert current["comparison"]["passed"] is True
    assert current["cleanup"]["succeeded"] is True


def test_source_campaign_localizes_function_file_and_line(monkeypatch):
    monkeypatch.setenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0")
    manager = CampaignManager(target=FakeSourceTarget())
    created = manager.create("LIVE-CODE-001")
    deadline = time.time() + 3
    current = created
    while current["status"] == "RUNNING" and time.time() < deadline:
        time.sleep(0.01)
        current = manager.get(created["run_id"])

    assert current is not None
    assert current["status"] == "COMPLETED"
    fault = current["snapshots"]["fault_snapshot"]
    assert fault["hot_function"] == "source_hot_function"
    assert fault["source_file"] == "app.py"
    assert fault["source_line"] == 97
    assert current["diagnosis"]["root_cause"] == "DOMINANT_SOURCE_HOT_FUNCTION"
    assert current["comparison"]["benchmark_case_id"] == "T1-CODE-001"
    assert current["comparison"]["passed"] is True
    assert current["cleanup"]["succeeded"] is True


def test_remaining_campaigns_cover_noisy_load_and_queue(monkeypatch):
    monkeypatch.setenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0")
    expected = {
        "LIVE-NOISY-001": ("SAME_HOST_NOISY_NEIGHBOR", "T1-NOISY-001"),
        "LIVE-LOAD-001": ("HOMEPAGE_TRAFFIC_SATURATION", "T1-LOAD-001"),
        "LIVE-QUEUE-001": ("PRODUCER_CONSUMER_IMBALANCE", "T1-QUEUE-001"),
    }
    for scenario_id, (root_cause, case_id) in expected.items():
        manager = CampaignManager(target=FakeRateTarget())
        created = manager.create(scenario_id)
        deadline = time.time() + 3
        current = created
        while current["status"] == "RUNNING" and time.time() < deadline:
            time.sleep(0.01)
            current = manager.get(created["run_id"])

        assert current is not None
        assert current["status"] == "COMPLETED"
        assert current["diagnosis"]["root_cause"] == root_cause
        assert current["comparison"]["benchmark_case_id"] == case_id
        assert current["comparison"]["passed"] is True
        assert current["cleanup"]["succeeded"] is True
