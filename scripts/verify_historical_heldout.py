"""Replay the original held-out contract in an owned historical source sandbox.

This never substitutes old source into the repository or claims that current
production code remains equivalent to the first experiment.
"""
from __future__ import annotations

import argparse
import ast
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_BASE = "08b70f20a112357390477edb6585564c5a10e2cc"
PREFIX = "benchmarks/retrieval/heldout_20261002_"
MANIFEST_SHA = "d6fde80a176a268ef9c13cd6ca5ddce2a8d33755769b0dc2a00ab6f6ce15e0c2"
ORIGINAL_REPORT_SHA = "c0940e9dd2e70ceccc313c2fd6359b701c0cc710b00fc4480237d8fe0cf3a6f9"
VERIFIER_PATH = "scripts/evaluate_heldout_diagnosis.py"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def restored_inputs(root=ROOT):
    """Accept only exact paths and bytes from the original signed manifest."""
    raw = (root / (PREFIX + "manifest.json")).read_bytes()
    if sha(raw) != MANIFEST_SHA:
        raise ValueError("original manifest changed")
    manifest = json.loads(raw)
    packet = json.loads((root / (PREFIX + "frozen_inputs.json")).read_bytes())
    pins = {**manifest["corpus_files"], **manifest["production_source_files"],
            "benchmarks/retrieval/sre_queries.json": manifest["development_set_sha256"]}
    if set(packet["files"]) != set(pins):
        raise ValueError("historical snapshot contains unregistered paths")
    result = {}
    for name, digest in pins.items():
        row = packet["files"][name]
        raw = base64.b64decode(row["base64"], validate=True)
        if row["sha256"] != digest or len(raw) != row["bytes"] or sha(raw) != digest:
            raise ValueError("historical snapshot bytes changed")
        result[name] = raw
    for suffix, key in (("public", "question_sha256"), ("private", "oracle_sha256")):
        raw = (root / (PREFIX + suffix + ".json")).read_bytes()
        if sha(raw) != manifest[key]:
            raise ValueError("historical question or oracle changed")
    return result


def historical_verifier_bytes(root=ROOT):
    """The historical verifier is Git-owned and retains the original 17 nodes."""
    return subprocess.check_output(["git", "show", HISTORICAL_BASE + ":" + VERIFIER_PATH], cwd=root)


@contextmanager
def historical_test_scope(evaluator, root=ROOT):
    """Bind only legacy tests to frozen inputs, restoring globals on exit.

    Current production imports are not replaced in sys.modules. The lexical
    implementation is loaded under a private module name from its original
    bytes; its plain BM25 route has no provider dependency.
    """
    inputs = restored_inputs(root)
    with tempfile.TemporaryDirectory(prefix="mini-drop-historical-heldout-") as directory:
        owned = Path(directory).resolve()
        for name, raw in inputs.items():
            target = (owned / name).resolve()
            if not target.is_relative_to(owned):
                raise ValueError("historical target outside owned sandbox")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        for suffix in ("public", "private", "manifest", "frozen_inputs"):
            name = PREFIX + suffix + ".json"
            target = owned / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((root / name).read_bytes())
        package_name = "_mini_drop_historical_" + owned.name.replace("-", "_")
        package = ModuleType(package_name)
        package.__path__ = [str(owned / "server/app/agent_runtime")]
        sys.modules[package_name] = package
        modules = {}
        for short in ("retrieval", "semantic_retrieval"):
            name = package_name + "." + short
            spec = importlib.util.spec_from_file_location(name, owned / "server/app/agent_runtime" / (short + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[short] = module
        tree = ast.parse(inputs["server/app/drop_insight/adaptive_planner.py"])
        prompt_nodes = [node for node in tree.body if isinstance(node, ast.Assign)
                        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "SYSTEM_PROMPT"]
        if len(prompt_nodes) != 1:
            raise ValueError("original production prompt is missing")
        constants = {"SYSTEM_PROMPT": ast.literal_eval(prompt_nodes[0].value)}
        requirements = []
        for filename, constant in (("cpu_criteria.py", "EVIDENCE_PLANNING_REQUIREMENT"),
                                   ("performance_criteria.py", "PERFORMANCE_PLANNING_REQUIREMENT")):
            name = "server.app.drop_insight._historical_" + owned.name.replace("-", "_") + "_" + filename[:-3]
            spec = importlib.util.spec_from_file_location(name, owned / "server/app/drop_insight" / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            requirements.append(getattr(module, constant))
        constants["EVIDENCE_PLANNING_REQUIREMENT"] = "".join(requirements)
        replacements = {"ROOT": owned, "retrieve_knowledge": modules["retrieval"].retrieve_knowledge,
                        "corpus": modules["semantic_retrieval"].corpus, **constants}
        previous = {name: getattr(evaluator, name) for name in replacements}
        default_functions = ("frozen_inputs", "frozen_knowledge", "frozen_retrieve_knowledge", "validate_freeze")
        previous_defaults = {}
        try:
            for name, value in replacements.items():
                setattr(evaluator, name, value)
            for name in default_functions:
                function = getattr(evaluator, name)
                # contextmanager wraps frozen_knowledge; bind its wrapped
                # function rather than touching the unchanged implementation.
                function = getattr(function, "__wrapped__", function)
                previous_defaults[name] = function.__defaults__
                function.__defaults__ = (owned,)
            yield owned
        finally:
            for name, defaults in previous_defaults.items():
                function = getattr(evaluator, name)
                getattr(function, "__wrapped__", function).__defaults__ = defaults
            for name, value in previous.items():
                setattr(evaluator, name, value)
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + "."):
                    del sys.modules[name]


def verify_historical_report(report_path, root=ROOT):
    """Verify the original report using original source, not current production."""
    report_path = Path(report_path).resolve()
    if sha(report_path.read_bytes()) != ORIGINAL_REPORT_SHA:
        raise ValueError("original historical report bytes changed")
    # Import current verifier only for its unchanged scoring logic. Its module
    # constants and BM25 source are explicitly rebound to the historical scope.
    spec = importlib.util.spec_from_file_location("mini_drop_historical_verifier", root / VERIFIER_PATH)
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    with historical_test_scope(evaluator, root):
        metrics = evaluator.verify_report(report_path)
    return {"schema": "mini-drop.historical-heldout-replay.v1", "status": "VERIFIED",
            "scope": "HISTORICAL_ORIGINAL_SOURCE_AND_CORPUS_ONLY",
            "current_production_equivalence_claimed": False,
            "original_manifest_sha256": MANIFEST_SHA,
            "original_report_sha256": ORIGINAL_REPORT_SHA,
            "restored_input_count": len(restored_inputs(root)), "provider_calls": 0,
            "actual_tasks_dispatched": 0, "metrics": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_historical_report(args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
