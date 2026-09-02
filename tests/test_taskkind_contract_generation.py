import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def test_taskkind_contract_is_valid_and_generated_bindings_are_fresh():
    payload = json.loads((ROOT / "contracts" / "taskkinds.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "docs" / "contracts" / "taskkind.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for item in payload["task_kinds"]:
        validator.validate(item)
        assert "manifest.json" in item["artifact_filenames"]
        parameter_schema = json.loads(
            (ROOT / item["parameter_schema"]).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(parameter_schema)
        parameter_validator = Draft202012Validator(parameter_schema)
        valid = {
            "target_pid": 1,
            "duration_sec": item["default_duration_seconds"],
            "sample_rate": item["default_sample_rate"],
            "options": {},
        }
        parameter_validator.validate(valid)
        invalid = dict(valid, duration_sec=item["max_duration_seconds"] + 1)
        assert list(parameter_validator.iter_errors(invalid))
    result = subprocess.run(
        [sys.executable, "scripts/generate_taskkind_contracts.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dual_status_contract_is_closed_and_generated_bindings_are_fresh():
    payload = json.loads(
        (ROOT / "contracts" / "task-statuses.json").read_text(encoding="utf-8")
    )
    assert [item["name"] for item in payload["collection"]] == [
        "CREATED", "QUEUED", "DELIVERED", "RUNNING", "UPLOADING",
        "COLLECTED", "FAILED", "CANCELED",
    ]
    assert [item["name"] for item in payload["analysis"]] == [
        "PENDING", "RUNNING", "RETRY", "SUCCESS", "FAILED", "CANCELED",
    ]
    for dimension in ("collection", "analysis"):
        known = {item["name"] for item in payload[dimension]}
        for item in payload[dimension]:
            assert set(item["next"]) <= known
            assert item["terminal"] == (not item["next"])
    result = subprocess.run(
        [sys.executable, "scripts/generate_status_contracts.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_error_code_contract_is_unique_and_generated_bindings_are_fresh():
    payload = json.loads(
        (ROOT / "contracts" / "error-codes.json").read_text(encoding="utf-8")
    )
    codes = payload["codes"]
    assert codes[0]["id"] == 0
    assert len({item["id"] for item in codes}) == len(codes)
    assert len({item["name"] for item in codes}) == len(codes)
    assert {item["retryable"] for item in codes} == {True, False}
    result = subprocess.run(
        [sys.executable, "scripts/generate_error_code_contracts.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
