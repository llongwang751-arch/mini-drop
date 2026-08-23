"""Counterfactual root-cause validation for Drop Insight.

The LLM may propose an experiment, but this module owns the safety contract and
the deterministic verdict.  It deliberately does not execute arbitrary shell
commands and never reads the dataset oracle while judging a live experiment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).resolve().parents[3] / "benchmarks" / "causal_replay" / "cases.json"
SUPPORTED_CASES = {"CR-CPU-001", "CR-MEM-001", "CR-DOWNSTREAM-001"}


def resource_policy(design_mode: str, duration_seconds: int) -> dict[str, Any]:
    """Describe conservative admission thresholds for small lab hosts.

    This policy does not pretend to inspect a remote host. The executor must
    verify these thresholds before starting a live experiment.
    """

    if design_mode == "DUAL_NODE_CONTROL":
        return {
            "recommended": duration_seconds <= 120,
            "minimum_online_agents": 2,
            "minimum_available_memory_mb_per_worker": 768,
            "maximum_recommended_window_seconds": 120,
            "execution_mode": "parallel treatment/control",
            "admission_note": "仅在两个独立 Worker 空闲、采集能力一致且负载可对齐时启用",
        }
    return {
        "recommended": duration_seconds <= 180,
        "minimum_online_agents": 1,
        "minimum_available_memory_mb_per_worker": 512,
        "maximum_recommended_window_seconds": 180,
        "execution_mode": "sequential crossover",
        "admission_note": "适合 2 核 4GB 节点；四个窗口串行执行，避免常驻第二套负载",
    }


def load_catalog() -> dict[str, Any]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    payload["cases"] = [
        {**item, "implementation_status": "AVAILABLE" if item["case_id"] in SUPPORTED_CASES else "PLANNED"}
        for item in payload.get("cases", [])
    ]
    return payload


def get_case(case_id: str) -> dict[str, Any]:
    for item in load_catalog().get("cases", []):
        if item.get("case_id") == case_id:
            return item
    raise ValueError(f"causal replay case not found: {case_id}")


def build_plan(case: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if case["case_id"] not in SUPPORTED_CASES:
        raise ValueError(f"causal replay case is not implemented yet: {case['case_id']}")
    treatment = payload["treatment_target"]
    control = payload.get("control_target")
    design_mode = payload.get("design_mode", "SINGLE_NODE_CROSSOVER")
    if design_mode == "DUAL_NODE_CONTROL":
        if control is None:
            raise ValueError("dual-node design requires a control target")
        if treatment["agent_id"] == control["agent_id"]:
            raise ValueError("treatment and control must use different agents")
    elif control is not None:
        raise ValueError("single-node design must not provide a control target")
    duration_seconds = payload.get("duration_seconds", 60)
    return {
        "case_id": case["case_id"],
        "title": case["title"],
        "hypothesis_id": payload["hypothesis_id"],
        "hypothesis": case["hypothesis"],
        "treatment": case["treatment"],
        "control": case["control"],
        "treatment_target": treatment,
        "control_target": control,
        "design_mode": design_mode,
        "primary_metric": case["primary_metric"],
        "duration_seconds": duration_seconds,
        "resource_policy": resource_policy(design_mode, duration_seconds),
        "risk_level": case["risk_level"],
        "approval_required": True,
        "single_primary_intervention": True,
        "cleanup": case["cleanup"],
        "windows": ["BASELINE", "INCIDENT", "INTERVENTION", "RECOVERY"],
        "support_condition": case["support_condition"],
        "refutation_condition": case["refutation_condition"],
    }


def evaluate_plan(plan: dict[str, Any], measurements: dict[str, Any]) -> dict[str, Any]:
    """Apply a transparent difference-in-differences style decision rule.

    Metrics in the current MVP are lower-is-better (latency, CPU pressure).  A
    verdict is withheld when control values or provenance references are absent.
    """

    windows = ("baseline", "incident", "intervention", "recovery")
    missing_refs = [
        name for name in windows
        if not measurements[name].get("task_ids") and not measurements[name].get("evidence_refs")
    ]
    design_mode = plan.get("design_mode", "DUAL_NODE_CONTROL")
    missing_control = (
        [name for name in windows if measurements[name].get("control") is None]
        if design_mode == "DUAL_NODE_CONTROL"
        else []
    )
    if missing_refs or missing_control:
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "confidence": 0.0,
            "reason": (
                "双节点模式的四窗口必须同时包含对照组数值和可追溯的任务或证据引用"
                if design_mode == "DUAL_NODE_CONTROL"
                else "单节点模式的四窗口必须包含可追溯的任务或证据引用"
            ),
            "missing_windows": sorted(set(missing_refs + missing_control)),
            "metrics": {},
        }

    def change(before: float, after: float) -> float:
        return (before - after) / max(abs(before), 1e-9)

    baseline = measurements["baseline"]
    incident = measurements["incident"]
    intervention = measurements["intervention"]
    recovery = measurements["recovery"]
    incident_degradation = (incident["treatment"] - baseline["treatment"]) / max(abs(baseline["treatment"]), 1e-9)
    treatment_improvement = change(incident["treatment"], intervention["treatment"])
    recovery_error = abs(recovery["treatment"] - baseline["treatment"]) / max(abs(baseline["treatment"]), 1e-9)

    if design_mode == "SINGLE_NODE_CROSSOVER":
        supported = incident_degradation >= 0.20 and treatment_improvement >= 0.30 and recovery_error <= 0.15
        refuted = treatment_improvement < 0.10
        if supported:
            verdict = "SUPPORTED_SINGLE_NODE"
            reason = "同一节点在故障、单变量干预和清理恢复窗口呈现可逆变化；缺少并行对照，结论仅为时间序列支持"
        elif refuted:
            verdict = "CAUSALLY_REFUTED"
            reason = "单变量干预后指标没有明显改善，当前假设被交替实验推翻"
        else:
            verdict = "INCONCLUSIVE"
            reason = "观察到变化，但效应量或恢复窗口未达到单节点支持门槛"
        confidence = min(0.75, max(0.0, treatment_improvement)) if verdict == "SUPPORTED_SINGLE_NODE" else 0.0
        return {
            "verdict": verdict,
            "confidence": round(confidence, 4),
            "reason": reason,
            "missing_windows": [],
            "metrics": {
                "incident_degradation": round(incident_degradation, 4),
                "treatment_improvement": round(treatment_improvement, 4),
                "recovery_error": round(recovery_error, 4),
                "difference_in_differences": None,
            },
        }

    control_improvement = change(incident["control"], intervention["control"])
    net_effect = treatment_improvement - control_improvement

    supported = (
        incident_degradation >= 0.20
        and treatment_improvement >= 0.30
        and abs(control_improvement) < 0.10
        and net_effect >= 0.25
        and recovery_error <= 0.15
    )
    refuted = treatment_improvement < 0.10 or net_effect < 0.05
    if supported:
        verdict = "CAUSALLY_VERIFIED"
        reason = "处理组在单变量干预后显著恢复，对照组未同步变化，清理后指标回到基线"
    elif refuted:
        verdict = "CAUSALLY_REFUTED"
        reason = "干预没有产生足够的独立改善，当前假设被反事实实验推翻"
    else:
        verdict = "INCONCLUSIVE"
        reason = "观察到变化，但效应量或恢复窗口未达到因果确认门槛"
    confidence = min(0.95, max(0.0, net_effect)) if verdict == "CAUSALLY_VERIFIED" else 0.0
    return {
        "verdict": verdict,
        "confidence": round(confidence, 4),
        "reason": reason,
        "missing_windows": [],
        "metrics": {
            "incident_degradation": round(incident_degradation, 4),
            "treatment_improvement": round(treatment_improvement, 4),
            "control_improvement": round(control_improvement, 4),
            "difference_in_differences": round(net_effect, 4),
            "recovery_error": round(recovery_error, 4),
        },
    }
