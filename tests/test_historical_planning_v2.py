"""Historical isolation remains explicit and rejects even repinned tampering."""
from __future__ import annotations

import json
import shutil

import pytest

from scripts import evaluate_planning_retrieval_v2 as evaluation
from scripts.verify_historical_planning_v2 import (
    ARCHIVE, ARCHIVE_MANIFEST_SHA, ROOT, historical_planning_test_scope,
    restored_inputs, sha, verify_historical_planning_report,
)


def test_original_v2_report_replays_without_current_production_claim():
    result = verify_historical_planning_report(ROOT / ARCHIVE / "first-run/report.json")
    assert result["status"] == "VERIFIED"
    assert result["current_production_equivalence_claimed"] is False
    assert result["metrics"]["case_count"] == 24
    assert result["metrics"]["structure_valid_count"] == 21
    assert result["metrics"]["timeouts"] == 3


def test_historical_scope_restores_all_current_sources_even_after_error():
    original_root = evaluation.ROOT
    original_parser = evaluation.validate_planning_output
    original_retriever = evaluation.retrieve_knowledge
    original_prompt = evaluation.SYSTEM_PROMPT
    original_file = evaluation.__file__
    original_defaults = evaluation.validate_freeze.__defaults__
    with pytest.raises(RuntimeError, match="deliberate"):
        with historical_planning_test_scope(evaluation) as owned:
            assert owned != original_root
            assert evaluation.ROOT == owned
            assert evaluation.validate_planning_output is not original_parser
            assert evaluation.retrieve_knowledge is not original_retriever
            assert evaluation.__file__ == str(owned / "scripts/evaluate_planning_retrieval_v2.py")
            raise RuntimeError("deliberate scope exit")
    assert evaluation.ROOT == original_root
    assert evaluation.validate_planning_output is original_parser
    assert evaluation.retrieve_knowledge is original_retriever
    assert evaluation.SYSTEM_PROMPT == original_prompt
    assert evaluation.__file__ == original_file
    assert evaluation.validate_freeze.__defaults__ == original_defaults
    assert not owned.exists()


@pytest.mark.parametrize("change", ["source", "manifest"])
def test_changed_historical_archive_is_rejected(tmp_path, change):
    archive = tmp_path / "a"
    shutil.copytree(ROOT / ARCHIVE / "original-source", archive / "original-source")
    shutil.copyfile(ROOT / ARCHIVE / "archive-manifest.json", archive / "archive-manifest.json")
    target = archive / ("archive-manifest.json" if change == "manifest" else "original-source/knowledge/linux_cpu.md")
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        restored_inputs(tmp_path, archive_path=archive)


def test_repinning_source_cannot_rewrite_signed_archive_manifest(tmp_path):
    archive = tmp_path / "a"
    shutil.copytree(ROOT / ARCHIVE / "original-source", archive / "original-source")
    shutil.copyfile(ROOT / ARCHIVE / "archive-manifest.json", archive / "archive-manifest.json")
    target = archive / "original-source/knowledge/linux_cpu.md"
    target.write_bytes(target.read_bytes() + b"\nforged coverage\n")
    manifest_path = archive / "archive-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["original-source/knowledge/linux_cpu.md"] = {
        "sha256": sha(target.read_bytes()), "bytes": target.stat().st_size,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert sha(manifest_path.read_bytes()) != ARCHIVE_MANIFEST_SHA
    with pytest.raises(ValueError, match="manifest changed"):
        restored_inputs(tmp_path, archive_path=archive)


def test_current_v2_freeze_rejects_changed_corpus_outside_historical_scope(tmp_path):
    with historical_planning_test_scope(evaluation) as owned:
        manifest, _ = evaluation.validate_freeze()
        for name in [evaluation.PREFIX + suffix + ".json" for suffix in ("public", "private", "manifest")] + list(manifest["corpus_files"]):
            destination = tmp_path / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(owned / name, destination)
    target = tmp_path / "knowledge/linux_cpu.md"
    target.write_bytes(target.read_bytes() + b"\nnew public capability\n")
    with pytest.raises(ValueError, match="corpus changed"):
        evaluation.validate_freeze(tmp_path)
