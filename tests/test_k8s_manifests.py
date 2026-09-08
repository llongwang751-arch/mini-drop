from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "deploy" / "k8s" / "base"


def _resources() -> list[dict]:
    kustomization = yaml.safe_load((BASE / "kustomization.yaml").read_text(encoding="utf-8"))
    resources: list[dict] = []
    for relative in kustomization["resources"]:
        resources.extend(
            document
            for document in yaml.safe_load_all((BASE / relative).read_text(encoding="utf-8"))
            if document
        )
    return resources


def test_k8s_base_has_unique_resources_and_does_not_embed_example_secret():
    resources = _resources()
    identities = [(item["kind"], item["metadata"]["name"]) for item in resources]

    assert len(resources) == 21
    assert len(identities) == len(set(identities))
    assert not any(item["kind"] == "Secret" for item in resources)
    assert "secret.example.yaml" not in yaml.safe_load(
        (BASE / "kustomization.yaml").read_text(encoding="utf-8")
    )["resources"]


def test_k8s_control_services_are_replicated_and_disruption_bounded():
    resources = _resources()
    deployments = {
        item["metadata"]["name"]: item
        for item in resources
        if item["kind"] == "Deployment"
    }
    expected = {"control-plane", "diagnosis-worker", "analyzer", "apiserver", "web"}

    assert set(deployments) == expected
    assert all(deployment["spec"]["replicas"] >= 2 for deployment in deployments.values())
    assert all(
        deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
        for deployment in deployments.values()
    )

    budgets = {
        item["metadata"]["name"]
        for item in resources
        if item["kind"] == "PodDisruptionBudget"
    }
    assert budgets == expected


def test_k8s_agent_is_one_host_pid_collector_per_node():
    resources = _resources()
    agents = [item for item in resources if item["kind"] == "DaemonSet"]

    assert len(agents) == 1
    pod_spec = agents[0]["spec"]["template"]["spec"]
    assert pod_spec["hostPID"] is True
    assert any(volume.get("hostPath", {}).get("path") == "/sys/kernel/tracing" for volume in pod_spec["volumes"])
    assert any(volume.get("hostPath", {}).get("path") == "/sys/kernel/debug" for volume in pod_spec["volumes"])
    capabilities = pod_spec["containers"][0]["securityContext"]["capabilities"]
    assert set(capabilities["add"]) == {"SYS_PTRACE", "PERFMON", "BPF", "SYS_RESOURCE"}
    assert capabilities["drop"] == ["ALL"]
