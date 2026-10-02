"""Replay v2 tests/reports against archived df0 source and public corpus.

Current production source, its knowledge corpus and its frozen-v2 rejection
remain unchanged. Only explicitly historical test consumers enter this scope.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "reports/quality/planning-retrieval-v2-20261002/evaluation"
ARCHIVE_MANIFEST_SHA = "3126374b0008a19e73cbd0d4a9d359df52a787cd73683a17a0786a0a61d76c75"
SOURCE_HEAD = "df0d3ef00a945e7d909f73868819799b2b7cc7f7"
FROZEN_MANIFEST_SHA = "ae57be9a369db8df7c10131810f4deaa68bf2ea91104911233a6ad67c8f58d4e"
VERIFIER = "scripts/evaluate_planning_retrieval_v2.py"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def restored_inputs(root=ROOT, *, archive_path=None):
    archive = Path(archive_path) if archive_path is not None else root / ARCHIVE
    if not archive.resolve().is_relative_to(root.resolve()):
        raise ValueError("historical archive outside specified root")
    raw = (archive / "archive-manifest.json").read_bytes()
    if sha(raw) != ARCHIVE_MANIFEST_SHA:
        raise ValueError("historical v2 archive manifest changed")
    manifest = json.loads(raw)
    if manifest["source_head"] != SOURCE_HEAD or manifest["frozen_manifest_sha256"] != FROZEN_MANIFEST_SHA:
        raise ValueError("historical v2 source identity changed")
    inputs = {}
    for name, receipt in manifest["files"].items():
        if not name.startswith("original-source/"):
            continue
        relative = name.removeprefix("original-source/")
        source = (archive / name).resolve()
        if not source.is_relative_to(archive.resolve()) or not relative or ".." in Path(relative).parts:
            raise ValueError("historical source path escapes archive")
        raw = source.read_bytes()
        if len(raw) != receipt["bytes"] or sha(raw) != receipt["sha256"]:
            raise ValueError("historical v2 source bytes changed")
        inputs[relative] = raw
    if len(inputs) != manifest["original_exact_git_blob_count"] or len(inputs) != 254:
        raise ValueError("historical v2 source scope changed")
    return inputs


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prompt_literal(node, constants):
    """Evaluate only original string concatenations and approved constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return prompt_literal(node.left, constants) + prompt_literal(node.right, constants)
    raise ValueError("historical prompt contains unsupported expression")


@contextmanager
def historical_planning_test_scope(evaluator, root=ROOT):
    """Explicitly bind v2 tests to unchanged archived behavior and restore it.

    Private module names avoid substituting old production modules globally.
    Numeric comparisons, original grading nodes and all test assertions remain
    strict. No provider, LangGraph, Task, index or database operation is run.
    """
    inputs = restored_inputs(root)
    with tempfile.TemporaryDirectory(prefix="mini-drop-historical-planning-v2-") as directory:
        owned = Path(directory).resolve()
        for name, raw in inputs.items():
            destination = (owned / name).resolve()
            if not destination.is_relative_to(owned):
                raise ValueError("historical target outside owned sandbox")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        prefix = "_mini_drop_historical_planning_v2_" + owned.name.replace("-", "_")
        package = ModuleType(prefix)
        package.__path__ = [str(owned / "server/app/agent_runtime")]
        sys.modules[prefix] = package
        original_names = set(sys.modules)
        previous = {}
        previous_defaults = {}
        try:
            retrieval = load_module(prefix + ".retrieval", owned / "server/app/agent_runtime/retrieval.py")
            parser = load_module(prefix + ".planning_output", owned / "server/app/agent_runtime/planning_output.py")
            # These two original modules only supply immutable requirement
            # constants to the evaluator. Their diagnostic predicates are not
            # invoked; production evidence imports are not substituted.
            cpu = load_module("server.app.drop_insight." + prefix + "_cpu", owned / "server/app/drop_insight/cpu_criteria.py")
            performance = load_module("server.app.drop_insight." + prefix + "_performance", owned / "server/app/drop_insight/performance_criteria.py")
            tree = ast.parse(inputs["server/app/drop_insight/adaptive_planner.py"])
            prompt = [node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "SYSTEM_PROMPT" for target in node.targets)]
            if len(prompt) != 1:
                raise ValueError("historical production prompt missing")
            replacements = {
                "ROOT": owned, "__file__": str(owned / VERIFIER),
                "retrieve_knowledge": retrieval.retrieve_knowledge,
                "planning_output_schema": parser.planning_output_schema,
                "validate_planning_output": parser.validate_planning_output,
                "SYSTEM_PROMPT": prompt_literal(prompt[0].value, {
                    "PLANNING_OUTPUT_REQUIREMENT": parser.PLANNING_OUTPUT_REQUIREMENT,
                }),
                "EVIDENCE_PLANNING_REQUIREMENT": cpu.EVIDENCE_PLANNING_REQUIREMENT + performance.PERFORMANCE_PLANNING_REQUIREMENT,
            }
            previous = {name: getattr(evaluator, name) for name in replacements}
            for name, value in replacements.items():
                setattr(evaluator, name, value)
            for name in ("validate_freeze", "source_receipt", "frozen_corpus"):
                function = getattr(evaluator, name)
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
                if name not in original_names and prefix in name:
                    del sys.modules[name]
            sys.modules.pop(prefix, None)


def verify_historical_planning_report(path, root=ROOT):
    from scripts import evaluate_planning_retrieval_v2 as evaluator
    with historical_planning_test_scope(evaluator, root):
        metrics = evaluator.verify_report(Path(path))
    return {"schema": "mini-drop.historical-planning-replay.v2", "status": "VERIFIED",
            "scope": "HISTORICAL_ORIGINAL_SOURCE_AND_CORPUS_ONLY",
            "current_production_equivalence_claimed": False, "source_head": SOURCE_HEAD,
            "original_manifest_sha256": FROZEN_MANIFEST_SHA, "metrics": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_historical_planning_report(args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
