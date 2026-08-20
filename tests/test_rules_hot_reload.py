from __future__ import annotations

import json
import os
import time

import pytest

from server.app.rca import candidates


@pytest.fixture(autouse=True)
def _clear_cache():
    candidates._cached_load_rules.cache_clear()
    candidates._rules_mtime_cache.clear()
    yield
    candidates._cached_load_rules.cache_clear()
    candidates._rules_mtime_cache.clear()


def _rule(cid: str) -> dict:
    return {
        "candidate_id": cid,
        "description": cid,
        "match_type": "top_function_keyword",
        "rule_score": 0.5,
        "params": {"min_percent": 40, "keywords": ["hotspot"]},
    }


def test_rules_reload_when_file_mtime_changes(tmp_path):
    rule_file = tmp_path / "rules.json"
    rule_file.write_text(json.dumps([_rule("r1")]))
    first = candidates.load_rules(str(rule_file))
    assert [r["candidate_id"] for r in first] == ["r1"]

    # Rewrite the file with a strictly newer mtime and load again.
    rule_file.write_text(json.dumps([_rule("r2")]))
    future = time.time() + 5
    os.utime(rule_file, (future, future))
    second = candidates.load_rules(str(rule_file))
    assert [r["candidate_id"] for r in second] == ["r2"]


def test_rules_are_cached_without_mtime_change(tmp_path):
    rule_file = tmp_path / "rules.json"
    rule_file.write_text(json.dumps([_rule("r1")]))
    first = candidates.load_rules(str(rule_file))
    second = candidates.load_rules(str(rule_file))
    assert first is second  # cached, no reload
