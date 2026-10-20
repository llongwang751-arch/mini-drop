"""Common stack aggregation; adapters retain their actual units and provenance."""

from .errors import AnalysisError
from .folded import Limits, analyze_folded


def stack_result(stacks, *, unit="samples", metadata=None, limits=None):
    limits = limits or Limits()
    rows = []
    for frames, weight in stacks:
        if not isinstance(weight, int) or weight < 0:
            raise AnalysisError(
                "INVALID_INPUT",
                "Negative or non-integral profile weights are unsupported",
            )
        if not weight:
            continue
        frames = [
            str(frame).replace(";", ":").replace("\n", " ").replace("\r", " ")
            for frame in frames
        ]
        rows.append((";".join(frames or ["[unknown]"]) + " " + str(weight)).encode())
        if len(rows) > limits.max_stacks:
            raise AnalysisError("INPUT_LIMIT_EXCEEDED", "Profile exceeds stack limit")
    if rows:
        parsed = analyze_folded(b"\n".join(rows), limits=limits)
    else:
        parsed = {
            "flamegraph": {"name": "all", "value": 0, "self": 0, "children": []},
            "top": {
                "schema_version": "1.0",
                "total_samples": 0,
                "unit": unit,
                "self": [],
                "inclusive": [],
            },
            "metadata": {
                "sample_count": 0,
                "stack_count": 0,
                "unique_stack_count": 0,
                "node_count": 1,
                "max_depth": 0,
            },
        }
    parsed["top"]["unit"] = unit
    parsed["metadata"].update({"unit": unit, **(metadata or {})})
    return parsed
