import json
from pathlib import Path
from scripts.score_multitrack_delivery import _run_validity


def _write_output(root: Path, validation: dict) -> None:
    output = root / "runs-native" / "agent" / "sha" / "case-01" / "repeat-1"
    output.mkdir(parents=True)
    (output / "raw-agent-output.txt").write_text(
        json.dumps({"validation": validation}), encoding="utf-8"
    )


def test_rule_fallback_campaign_is_never_comparable(tmp_path):
    (tmp_path / "campaign-metadata.json").write_text(
        json.dumps({"rule_fallback_allowed": True}), encoding="utf-8"
    )
    _write_output(
        tmp_path,
        {
            "model_invoked": True,
            "schema_validated": True,
            "fallback_reason": None,
        },
    )

    validity, reason = _run_validity(tmp_path, 1)

    assert validity == "INVALID_PROVIDER_CONFIGURATION"
    assert "rule fallback" in reason


def test_provider_error_is_not_counted_as_model_backed_output(tmp_path):
    _write_output(
        tmp_path,
        {
            "model_invoked": True,
            "schema_validated": False,
            "fallback_reason": "provider returned HTTP 401",
        },
    )

    validity, reason = _run_validity(tmp_path, 1)

    assert validity == "INVALID_PROVIDER_CONFIGURATION"
    assert "schema-valid" in reason


def test_partial_provider_failure_invalidates_complete_cohort(tmp_path):
    first = tmp_path / "runs-native" / "agent" / "sha" / "case-01" / "repeat-1"
    second = tmp_path / "runs-native" / "agent" / "sha" / "case-01" / "repeat-2"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "raw-agent-output.txt").write_text(
        json.dumps(
            {
                "validation": {
                    "model_invoked": True,
                    "schema_validated": True,
                    "fallback_reason": None,
                }
            }
        ),
        encoding="utf-8",
    )
    (second / "raw-agent-output.txt").write_text(
        json.dumps(
            {
                "validation": {
                    "model_invoked": True,
                    "schema_validated": False,
                    "fallback_reason": "timeout",
                }
            }
        ),
        encoding="utf-8",
    )

    validity, reason = _run_validity(tmp_path, 2)

    assert validity == "INVALID_PARTIAL_PROVIDER_FAILURE"
    assert "1/2" in reason
