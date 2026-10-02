"""Preserve the failed phrase check and the equivalent scoped-text fix separately."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
DEST = ROOT / "reports/quality/planning-retrieval-v2-20261002/local/prompt-literal-compatibility"


def main():
    assert not DEST.exists()
    DEST.mkdir(parents=True)
    mapping = json.loads((STAGE / "prompt-literal-compatibility-candidates.json").read_text(encoding="utf-8"))
    copies = [(STAGE / "prompt-literal-compatibility-candidates.json", "source-candidates.json"),
              (ROOT / mapping["files"][0]["clean_candidate"], "clean/diagnosis_agent.py"),
              (STAGE / "prompt-ci-fix-pytest.xml", "pytest.xml"),
              (STAGE / "prepare_prompt_literal_compatibility.py", "prepare_prompt_literal_compatibility.py"),
              (STAGE / "archive_prompt_literal_compatibility.py", "archive_prompt_literal_compatibility.py"),
              (STAGE / "ci-runs/36995708503/python-failure/report.json", "original-ci-failure.json"),
              (STAGE / "ci-runs/36995708503/python-failure/summary.json", "original-ci-failure-summary.json")]
    for source, name in copies:
        target = DEST / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    xml = ET.parse(DEST / "pytest.xml")
    cases = xml.findall(".//testcase")
    assert len(cases) == 132 and not xml.findall(".//failure") and not xml.findall(".//error")
    command = ["python", "-m", "ruff", "check", "server/app/drop_insight/diagnosis_agent.py"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    (DEST / "ruff.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0
    rows = [{"path": str(path.relative_to(DEST)).replace("\\", "/"), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "bytes": path.stat().st_size} for path in sorted(DEST.rglob("*")) if path.is_file()]
    receipt = {"schema": "mini-drop.prompt-literal-compatibility.v1", "base_head": mapping["base_head"],
               "passed_cases": len(cases), "failures": 0, "ruff_exit_code": result.returncode, "model_invocations": 0,
               "scope": "Original CI failed only the continuous phrase assertion. Equivalent INVESTIGATE-scoped wording retains observable-domain requirement, leaves the original test and validators unchanged. The first failed CI and first prompt-boundary evidence remain immutable.", "files": rows}
    (DEST / "manifest.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(DEST), "files": len(rows), "manifest_sha256": hashlib.sha256((DEST / "manifest.json").read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
