"""Robust, deterministic anomaly detection for continuous profiling windows.

This module deliberately does not call an LLM.  It turns window-level profiler
facts into a small, auditable trigger signal.  The AI diagnosis workflow is
started only after this gate fires, which prevents a language model from
inventing incidents from ordinary sampling noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any


DETECTOR_VERSION = "continuous-profile-mad-v1"


@dataclass(frozen=True)
class WindowProfile:
    window_index: int
    sample_total: int
    top1_percent: float
    top_function: str


def summarize_top(window_index: int, rows: list[dict[str, Any]]) -> WindowProfile:
    samples = [max(0, int(item.get("samples", 0) or 0)) for item in rows]
    total = sum(samples)
    first = rows[0] if rows else {}
    top1_percent = float(first.get("percent", 0.0) or 0.0)
    return WindowProfile(
        window_index=window_index,
        sample_total=total,
        top1_percent=round(top1_percent, 2),
        top_function=str(first.get("name", "unknown"))[:256],
    )


def detect_profile_anomaly(
    windows: list[WindowProfile],
    *,
    min_baseline_windows: int = 2,
) -> dict[str, Any]:
    """Compare the newest window with robust medians of earlier windows."""

    if len(windows) <= min_baseline_windows:
        return {
            "triggered": False,
            "detector_version": DETECTOR_VERSION,
            "reason": "insufficient_baseline_windows",
            "required_windows": min_baseline_windows + 1,
            "observed_windows": len(windows),
        }

    baseline = windows[:-1]
    current = windows[-1]
    baseline_samples = float(median(item.sample_total for item in baseline))
    baseline_top1 = float(median(item.top1_percent for item in baseline))

    sample_ratio = (
        current.sample_total / baseline_samples if baseline_samples > 0 else 0.0
    )
    top1_delta = current.top1_percent - baseline_top1
    sample_surge = (
        baseline_samples >= 20
        and current.sample_total >= 100
        and sample_ratio >= 1.8
    )
    hotspot_shift = (
        current.sample_total >= 20
        and current.top1_percent >= 35.0
        and top1_delta >= 15.0
    )
    triggered = sample_surge or hotspot_shift
    reasons = []
    if sample_surge:
        reasons.append("cpu_sample_surge")
    if hotspot_shift:
        reasons.append("hotspot_concentration_shift")

    return {
        "triggered": triggered,
        "detector_version": DETECTOR_VERSION,
        "reason": ",".join(reasons) if reasons else "within_baseline",
        "window_index": current.window_index,
        "current": {
            "sample_total": current.sample_total,
            "top1_percent": current.top1_percent,
            "top_function": current.top_function,
        },
        "baseline": {
            "sample_total_median": round(baseline_samples, 2),
            "top1_percent_median": round(baseline_top1, 2),
            "window_count": len(baseline),
        },
        "deltas": {
            "sample_ratio": round(sample_ratio, 3),
            "top1_percent_points": round(top1_delta, 2),
        },
    }
