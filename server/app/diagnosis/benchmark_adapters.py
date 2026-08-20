"""Fault-source adapters for the unified diagnosis benchmark.

Adapters do not diagnose a case. They describe and validate how the fault
fixture is activated so every AI strategy observes the same experiment.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from server.app.diagnosis.benchmark_cases import load_benchmark_case
from server.app.diagnosis.eval_harness import (
    DEFAULT_SCENARIO_ROOT,
    evaluate_scenario,
    load_scenarios,
)


OTEL_FEATURE_URL = "http://localhost:8080/feature"
OTEL_RUNNER_SCHEMA_VERSION = "otel-live-subset.v1"
OTEL_FLAGD_PATH = Path("src/flagd/demo.flagd.json")
OTEL_COMPOSE_PATHS = (Path("compose.yaml"), Path("compose.full.yaml"))
OTEL_SUPPORTED_FLAGS = {
    "adServiceHighCpu",
    "emailMemoryLeak",
    "adServiceManualGc",
    "imageSlowLoad",
    "kafkaQueueProblems",
    "paymentServiceUnreachable",
    "loadgeneratorFloodHomepage",
}

# Public documentation uses scenario names while the pinned 3.0.0 fixture uses
# shorter flagd keys. Keeping this explicit prevents toggling a non-existent
# flag after the upstream demo is upgraded.
OTEL_PINNED_FLAG_KEYS = {
    "adServiceHighCpu": [("adHighCpu", "on")],
    "emailMemoryLeak": [("emailMemoryLeak", "100x")],
    "adServiceManualGc": [("adManualGc", "on")],
    "imageSlowLoad": [("imageSlowLoad", "5sec")],
    "kafkaQueueProblems": [("kafkaQueueProblems", "on")],
    "paymentServiceUnreachable": [
        ("paymentUnreachable", "on"),
        ("paymentFailure", "100%"),
    ],
    "loadgeneratorFloodHomepage": [
        ("loadGeneratorTraffic", "on"),
        ("loadGeneratorVUs", "50"),
    ],
}
OTEL_PINNED_BASELINE_VALUES = {
    "adServiceHighCpu": [("adHighCpu", "off")],
    "emailMemoryLeak": [("emailMemoryLeak", "off")],
    "adServiceManualGc": [("adManualGc", "off")],
    "imageSlowLoad": [("imageSlowLoad", "off")],
    "kafkaQueueProblems": [("kafkaQueueProblems", "off")],
    "paymentServiceUnreachable": [
        ("paymentUnreachable", "off"),
        ("paymentFailure", "off"),
    ],
    "loadgeneratorFloodHomepage": [
        ("loadGeneratorTraffic", "on"),
        ("loadGeneratorVUs", "5"),
    ],
}

BENCHMARK_RUNNERS: dict[str, str | None] = {
    "T1-CODE-001": "local_golden",
    "T1-IO-001": "local_golden",
    "T1-NOISY-001": "local_golden",
    "T1-CPU-001": "otel_ad_cpu_experiment",
    "T1-GC-001": "otel_ad_cpu_experiment",
    "T1-MEM-001": "otel_grpc_fault_experiment",
    "T1-DOWNSTREAM-001": "otel_grpc_fault_experiment",
    "T1-NET-001": None,
    "T1-QUEUE-001": None,
    "T1-LOAD-001": None,
}
BENCHMARK_REQUIRED_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "T1-CODE-001": (),
    "T1-IO-001": (),
    "T1-NOISY-001": (),
    "T1-CPU-001": ("sys_metrics", "java_async"),
    "T1-GC-001": ("sys_metrics", "java_async"),
    "T1-MEM-001": ("sys_metrics", "memory_smaps"),
    "T1-DOWNSTREAM-001": ("sys_metrics",),
    "T1-NET-001": ("sys_metrics",),
    "T1-QUEUE-001": ("sys_metrics",),
    "T1-LOAD-001": ("sys_metrics",),
}
OTEL_REQUIRED_SERVICES: dict[str, tuple[str, ...]] = {
    "adServiceHighCpu": ("ad", "flagd", "otel-collector"),
    "adServiceManualGc": ("ad", "flagd", "otel-collector"),
    "emailMemoryLeak": ("email", "flagd", "otel-collector"),
    "imageSlowLoad": ("image-provider", "frontend", "flagd", "otel-collector"),
    "kafkaQueueProblems": (
        "kafka",
        "checkout",
        "accounting",
        "fraud-detection",
        "flagd",
        "otel-collector",
    ),
    "paymentServiceUnreachable": (
        "payment",
        "checkout",
        "frontend",
        "flagd",
        "otel-collector",
    ),
    "loadgeneratorFloodHomepage": (
        "load-generator",
        "frontend",
        "flagd",
        "otel-collector",
    ),
}


def _pinned_otel_revision() -> str | None:
    manifest_path = Path(__file__).resolve().parents[3] / "benchmarks" / "unified_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for source in manifest.get("sources", []):
        if source.get("source_id") == "otel-demo":
            revision = source.get("revision")
            return str(revision) if revision else None
    return None


def _git_revision(root: Path) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"cannot read OTel Git revision: {exc}"
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        return None, f"cannot read OTel Git revision: {detail}"
    revision = result.stdout.strip()
    if not revision:
        return None, "cannot read OTel Git revision: git returned an empty revision"
    return revision, None


def _load_flag_definitions(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = root / OTEL_FLAGD_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read pinned flag configuration: {exc}"
    flags = payload.get("flags") if isinstance(payload, dict) else None
    if not isinstance(flags, dict):
        return None, "pinned flag configuration has no flags object"
    return flags, None


def _validate_flag_values(
    definitions: dict[str, Any],
    enabled_values: list[tuple[str, str]],
    baseline_values: list[tuple[str, str]],
) -> dict[str, Any]:
    all_keys = {key for key, _ in enabled_values + baseline_values}
    missing_keys = sorted(key for key in all_keys if not isinstance(definitions.get(key), dict))

    def missing_variants(values: list[tuple[str, str]]) -> list[dict[str, str]]:
        missing: list[dict[str, str]] = []
        for key, variant in values:
            definition = definitions.get(key)
            variants = definition.get("variants") if isinstance(definition, dict) else None
            if isinstance(definition, dict) and (
                not isinstance(variants, dict) or variant not in variants
            ):
                missing.append({"key": key, "variant": variant})
        return missing

    missing_enabled = missing_variants(enabled_values)
    missing_baseline = missing_variants(baseline_values)
    return {
        "flag_keys_ready": not missing_keys,
        "flag_variants_ready": not missing_enabled and not missing_baseline,
        "missing_flag_keys": missing_keys,
        "missing_enabled_variants": missing_enabled,
        "missing_baseline_variants": missing_baseline,
    }


def _compose_services(path: Path) -> tuple[set[str], str | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return set(), f"cannot read OTel Compose file {path.name}: {exc}"
    services_line = next(
        (index for index, line in enumerate(lines) if line.strip() == "services:" and not line.startswith((" ", "\t"))),
        None,
    )
    if services_line is None:
        return set(), f"OTel Compose file {path.name} has no top-level services object"
    services: set[str] = set()
    service_pattern = re.compile(r"^  ([A-Za-z0-9_.-]+):(?:\s*(?:#.*)?)$")
    for line in lines[services_line + 1 :]:
        if line and not line.startswith((" ", "\t", "#")):
            break
        match = service_pattern.match(line)
        if match:
            services.add(match.group(1))
    if not services:
        return set(), f"OTel Compose file {path.name} has no service definitions"
    return services, None


def _load_compose_services(root: Path) -> tuple[set[str], list[str]]:
    available: set[str] = set()
    errors: list[str] = []
    for index, relative_path in enumerate(OTEL_COMPOSE_PATHS):
        path = root / relative_path
        if not path.exists() and index > 0:
            continue
        services, error = _compose_services(path)
        available.update(services)
        if error:
            errors.append(error)
    return available, errors


def _docker_readiness() -> tuple[bool, bool]:
    docker_available = shutil.which("docker") is not None
    if not docker_available:
        return False, False
    try:
        compose_available = subprocess.run(
            ["docker", "compose", "version"],
            check=False,
            capture_output=True,
            timeout=10,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        compose_available = False
    return True, compose_available


def _base_check(case_id: str, adapter: str, action: str) -> dict[str, Any]:
    runner = BENCHMARK_RUNNERS.get(case_id)
    required = list(BENCHMARK_REQUIRED_CAPABILITIES.get(case_id, ()))
    return {
        "case_id": case_id,
        "adapter": adapter,
        "action": action,
        "runner": runner,
        "runner_supported": runner is not None,
        "runner_schema_version": OTEL_RUNNER_SCHEMA_VERSION,
        "required_capabilities": required,
        "required_collectors": required,
    }


def adapter_preflight(
    case_id: str,
    *,
    otel_root: Path | None = None,
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case = load_benchmark_case(case_id)
    adapter = case["trigger"]["adapter"]
    action = case["trigger"]["action"]
    common = _base_check(case_id, adapter, action)

    if adapter == "mini_drop_golden":
        scenario_ids = {item["scenario_id"] for item in load_scenarios(DEFAULT_SCENARIO_ROOT)}
        adapter_supported = action in scenario_ids
        reasons = [] if adapter_supported else [f"Golden scenario not found: {action}"]
        return {
            **common,
            "adapter_supported": adapter_supported,
            "fixture_ready": adapter_supported,
            "diagnosis_ready": adapter_supported and common["runner_supported"],
            "ready": adapter_supported,
            "mode": "deterministic_replay",
            "scenario_id": action,
            "capabilities_ready": True,
            "provided_agent_capabilities": None,
            "missing_capabilities": [],
            "fixture_reasons": reasons,
            "diagnosis_reasons": reasons,
            "readiness_errors": reasons,
        }

    if adapter == "otel_feature_flag":
        enabled_values = OTEL_PINNED_FLAG_KEYS.get(action, [])
        baseline_values = OTEL_PINNED_BASELINE_VALUES.get(action, [])
        required_services = list(OTEL_REQUIRED_SERVICES.get(action, ()))
        adapter_supported = (
            action in OTEL_SUPPORTED_FLAGS
            and bool(enabled_values)
            and bool(baseline_values)
            and bool(required_services)
        )
        docker_available, compose_available = _docker_readiness()
        expected_revision = _pinned_otel_revision()
        actual_revision: str | None = None
        revision_ready = False
        flag_fixture_path: str | None = None
        flag_status = {
            "flag_keys_ready": False,
            "flag_variants_ready": False,
            "missing_flag_keys": sorted({key for key, _ in enabled_values + baseline_values}),
            "missing_enabled_variants": [],
            "missing_baseline_variants": [],
        }
        available_services: set[str] = set()
        compose_errors: list[str] = []
        fixture_reasons: list[str] = []

        if not adapter_supported:
            fixture_reasons.append(f"no complete pinned adapter mapping for {action}")
        if otel_root is None:
            fixture_reasons.append("explicit otel_root is required")
        else:
            root = otel_root.resolve()
            flag_fixture_path = str(root / OTEL_FLAGD_PATH)
            actual_revision, revision_error = _git_revision(root)
            revision_ready = bool(expected_revision and actual_revision == expected_revision)
            if revision_error:
                fixture_reasons.append(revision_error)
            elif not expected_revision:
                fixture_reasons.append("pinned OTel revision is missing from unified manifest")
            elif not revision_ready:
                fixture_reasons.append(
                    f"OTel revision mismatch: expected {expected_revision}, got {actual_revision}"
                )

            definitions, flag_error = _load_flag_definitions(root)
            if flag_error:
                fixture_reasons.append(flag_error)
            elif definitions is not None:
                flag_status = _validate_flag_values(
                    definitions, enabled_values, baseline_values
                )

            available_services, compose_errors = _load_compose_services(root)
            fixture_reasons.extend(compose_errors)

        missing_services = sorted(set(required_services) - available_services)
        services_ready = not compose_errors and not missing_services
        if flag_status["missing_flag_keys"]:
            fixture_reasons.append(
                "missing flag keys: " + ", ".join(flag_status["missing_flag_keys"])
            )
        for item in flag_status["missing_enabled_variants"]:
            fixture_reasons.append(
                f"missing fault flag variant: {item['key']}={item['variant']}"
            )
        for item in flag_status["missing_baseline_variants"]:
            fixture_reasons.append(
                f"missing baseline flag variant: {item['key']}={item['variant']}"
            )
        if missing_services:
            fixture_reasons.append(
                "OTel Compose is missing required services: " + ", ".join(missing_services)
            )
        if not docker_available:
            fixture_reasons.append("docker executable is unavailable")
        elif not compose_available:
            fixture_reasons.append("docker compose is unavailable")

        fixture_ready = (
            adapter_supported
            and otel_root is not None
            and docker_available
            and compose_available
            and revision_ready
            and flag_status["flag_keys_ready"]
            and flag_status["flag_variants_ready"]
            and services_ready
        )
        required_capabilities = set(common["required_capabilities"])
        if agent is None:
            agent_id = None
            agent_status = None
            provided_capabilities: list[str] | None = None
            missing_capabilities = sorted(required_capabilities)
            capabilities_ready = not required_capabilities
        else:
            agent_id = agent.get("id")
            agent_status = str(agent.get("status") or "").upper()
            provided_capabilities = sorted(str(item) for item in (agent.get("capabilities") or []))
            missing_capabilities = sorted(required_capabilities - set(provided_capabilities))
            capabilities_ready = agent_status == "ONLINE" and not missing_capabilities

        diagnosis_reasons: list[str] = []
        if not fixture_ready:
            diagnosis_reasons.append("fixture is not ready")
        if not common["runner_supported"]:
            diagnosis_reasons.append(
                f"bounded runner {OTEL_RUNNER_SCHEMA_VERSION} does not support {case_id}"
            )
        if agent is None and required_capabilities:
            diagnosis_reasons.append("explicit target Agent readiness is required")
        elif agent is not None:
            if agent_status != "ONLINE":
                diagnosis_reasons.append("target Agent is not ONLINE")
            if missing_capabilities:
                diagnosis_reasons.append(
                    "target Agent lacks collectors: " + ", ".join(missing_capabilities)
                )
        diagnosis_ready = (
            fixture_ready and common["runner_supported"] and capabilities_ready
        )
        return {
            **common,
            "adapter_supported": adapter_supported,
            "fixture_ready": fixture_ready,
            "diagnosis_ready": diagnosis_ready,
            "ready": fixture_ready,
            "mode": "live_fault_injection",
            "feature_flag": action,
            "pinned_flag_values": [
                {"key": key, "variant": variant} for key, variant in enabled_values
            ],
            "pinned_baseline_values": [
                {"key": key, "variant": variant} for key, variant in baseline_values
            ],
            "feature_flag_url": OTEL_FEATURE_URL,
            "explicit_otel_root": otel_root is not None,
            "otel_root": str(otel_root.resolve()) if otel_root else None,
            "expected_revision": expected_revision,
            "actual_revision": actual_revision,
            "revision_ready": revision_ready,
            "flag_fixture_path": flag_fixture_path,
            **flag_status,
            "available_services": sorted(available_services),
            "required_services": required_services,
            "missing_services": missing_services,
            "services_ready": services_ready,
            "docker_available": docker_available,
            "compose_available": compose_available,
            "checkout_ready": fixture_ready,
            "agent_id": agent_id,
            "agent_status": agent_status,
            "agent_capabilities": provided_capabilities or [],
            "provided_agent_capabilities": provided_capabilities,
            "missing_capabilities": missing_capabilities,
            "capabilities_ready": capabilities_ready,
            "fixture_reasons": fixture_reasons,
            "diagnosis_reasons": diagnosis_reasons,
            "readiness_errors": fixture_reasons + diagnosis_reasons,
            "instructions": [
                "Start the pinned OpenTelemetry Demo checkout.",
                f"Open {OTEL_FEATURE_URL}.",
                "Apply pinned_flag_values and save.",
                "Run the Mini-Drop diagnosis, then restore pinned_baseline_values.",
            ],
        }

    reasons = ["external dataset adapter is catalogued but not locally materialized"]
    return {
        **common,
        "adapter_supported": False,
        "fixture_ready": False,
        "diagnosis_ready": False,
        "ready": False,
        "mode": "external_dataset",
        "capabilities_ready": False,
        "provided_agent_capabilities": None,
        "missing_capabilities": common["required_capabilities"],
        "fixture_reasons": reasons,
        "diagnosis_reasons": ["fixture is not ready"],
        "readiness_errors": reasons + ["fixture is not ready"],
    }


def replay_local_golden(case_id: str) -> dict[str, Any]:
    case = load_benchmark_case(case_id)
    if case["trigger"]["adapter"] != "mini_drop_golden":
        raise ValueError(f"{case_id} is not a local Golden replay case")
    scenario_id = case["trigger"]["action"]
    scenarios = {item["scenario_id"]: item for item in load_scenarios(DEFAULT_SCENARIO_ROOT)}
    if scenario_id not in scenarios:
        raise KeyError(f"Golden scenario not found: {scenario_id}")
    return evaluate_scenario(scenarios[scenario_id])


def set_otel_feature_flag(
    case_id: str,
    *,
    otel_root: Path,
    enabled: bool,
) -> dict[str, Any]:
    """Atomically toggle a pinned OTel Demo case in demo.flagd.json."""

    case = load_benchmark_case(case_id)
    if case["trigger"]["adapter"] != "otel_feature_flag":
        raise ValueError(f"{case_id} is not an OpenTelemetry feature-flag case")
    scenario = case["trigger"]["action"]
    values = (
        OTEL_PINNED_FLAG_KEYS if enabled else OTEL_PINNED_BASELINE_VALUES
    ).get(scenario)
    if not values:
        raise KeyError(f"no pinned flag mapping for {scenario}")
    config_path = otel_root / OTEL_FLAGD_PATH
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    flags = payload.get("flags", {})
    changes = []
    for key, variant in values:
        if key not in flags:
            raise KeyError(f"flagd key not found in pinned fixture: {key}")
        if variant not in flags[key].get("variants", {}):
            raise ValueError(f"flagd variant {key}={variant} does not exist")
        before = flags[key].get("defaultVariant")
        flags[key]["defaultVariant"] = variant
        changes.append({"key": key, "before": before, "after": variant})

    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=config_path.parent,
        suffix=".tmp",
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    temporary.replace(config_path)
    return {
        "case_id": case_id,
        "scenario": scenario,
        "enabled": enabled,
        "config_path": str(config_path),
        "changes": changes,
    }


def preflight_all(
    *,
    otel_root: Path | None = None,
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from server.app.diagnosis.benchmark_cases import load_benchmark_cases

    checks = [
        adapter_preflight(case["case_id"], otel_root=otel_root, agent=agent)
        for case in load_benchmark_cases()
    ]
    fixture_ready_count = sum(bool(item["fixture_ready"]) for item in checks)
    return {
        "case_count": len(checks),
        "supported_count": sum(bool(item["adapter_supported"]) for item in checks),
        "fixture_ready_count": fixture_ready_count,
        "diagnosis_ready_count": sum(bool(item["diagnosis_ready"]) for item in checks),
        "ready_count": fixture_ready_count,
        "checks": checks,
    }
