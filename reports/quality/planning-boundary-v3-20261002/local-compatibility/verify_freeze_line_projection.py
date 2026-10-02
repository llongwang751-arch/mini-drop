"""Verify exact Git-LF inputs, while all signed suite bytes stay immutable."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts import evaluate_planning_boundary_v3 as evaluation

HEAD = "e17f287ee7ab7270ffc73534c90c14af297d3228"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    manifest, _ = evaluation.validate_freeze()
    frozen_names = [evaluation.PREFIX + suffix + ".json" for suffix in ("public", "private", "manifest")]
    originals = {name: sha((ROOT / name).read_bytes()) for name in frozen_names}
    with tempfile.TemporaryDirectory(prefix="v3-git-lf-check-") as directory:
        owned = Path(directory).resolve()
        names = [*frozen_names, *manifest["previous_question_pins"]]
        for name in names:
            raw = subprocess.check_output(["git", "show", HEAD + ":" + name], cwd=ROOT)
            target = (owned / name).resolve()
            assert target.is_relative_to(owned)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        evaluation.validate_freeze(owned)
        legacy = owned / evaluation.LEGACY_DEVELOPMENT_PATH
        raw_lf = legacy.read_bytes()
        assert sha(raw_lf) == evaluation.LEGACY_DEVELOPMENT_GIT_LF_SHA
        assert sha(raw_lf.replace(b"\n", b"\r\n")) == evaluation.LEGACY_DEVELOPMENT_RAW_SHA
        rejected = []
        for ending, raw in (("LF", raw_lf), ("CRLF", raw_lf.replace(b"\n", b"\r\n"))):
            legacy.write_bytes(raw.replace(b"query", b"QUERY", 1))
            try:
                evaluation.validate_freeze(owned)
            except ValueError:
                rejected.append(ending)
            else:
                raise AssertionError("changed legacy body accepted")
        legacy.write_bytes(raw_lf)
        strict_frozen = []
        for name in frozen_names:
            target = owned / name
            raw = target.read_bytes()
            assert b"\r" not in raw
            target.write_bytes(raw.replace(b"\n", b"\r\n"))
            try:
                evaluation.validate_freeze(owned)
            except ValueError:
                strict_frozen.append(name)
            else:
                raise AssertionError("new signed suite line bytes accepted after change")
            target.write_bytes(raw)
        assert originals == {name: sha((ROOT / name).read_bytes()) for name in frozen_names}
    receipt = {"schema": "mini-drop.v3-legacy-exact-line-projection-check.v1", "status": "VERIFIED",
               "input_git_head": HEAD, "git_lf_inputs_accepted": True,
               "single_legacy_path": evaluation.LEGACY_DEVELOPMENT_PATH,
               "legacy_git_lf_sha256": evaluation.LEGACY_DEVELOPMENT_GIT_LF_SHA,
               "legacy_frozen_crlf_sha256": evaluation.LEGACY_DEVELOPMENT_RAW_SHA,
               "legacy_body_tamper_rejected": rejected, "new_frozen_raw_line_endings_strict": strict_frozen,
               "new_suite_sha256_unchanged": originals, "provider_calls_attempted": 0,
               "generic_whitespace_normalization": False, "private_truth_rewritten": False}
    output = Path(__file__).resolve().parent / "freeze-line-ending-independent-check.json"
    with output.open("xb") as stream:
        stream.write((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
