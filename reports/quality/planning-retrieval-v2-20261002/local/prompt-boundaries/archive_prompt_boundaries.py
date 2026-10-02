"""Persist reproducible offline prompt-boundary checks and immutable clean delta."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
DEST = ROOT / "reports/quality/planning-retrieval-v2-20261002/local/prompt-boundaries"


def main():
    assert not DEST.exists()
    DEST.mkdir(parents=True)
    mapping = json.loads((STAGE / "prompt-source-candidates.json").read_text(encoding="utf-8"))
    shutil.copyfile(STAGE / "prompt-source-candidates.json", DEST / "source-candidates.json")
    for row in mapping["files"]:
        source = ROOT / row["clean_candidate"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == row["sha256"]
        target = DEST / "clean" / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    test_command = ["python", "-m", "pytest", "tests/test_disposition_first_prompts.py", "tests/test_checkpoint_thread_isolation.py",
                    "tests/test_planning_output_v2.py", "tests/test_cpu_plan_contract.py", "-q",
                    "--basetemp=output/acceptance/planning-retrieval-v2-20261002/pytest-prompts-v7-r2",
                    "--junitxml=" + str(DEST / "pytest.xml")]
    ruff_command = ["python", "-m", "ruff", "check"] + [row["path"] for row in mapping["files"] if row["path"].endswith(".py")]
    checks = []
    for name, command in [("pytest", test_command), ("ruff", ruff_command)]:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        (DEST / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        checks.append({"name": name, "command": command, "exit_code": result.returncode})
        assert result.returncode == 0
    shutil.copyfile(STAGE / "prepare_four_state_prompts.py", DEST / "prepare_four_state_prompts.py")
    shutil.copyfile(STAGE / "archive_prompt_boundaries.py", DEST / "archive_prompt_boundaries.py")
    rows = [{"path": str(path.relative_to(DEST)).replace("\\", "/"), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "bytes": path.stat().st_size} for path in sorted(DEST.rglob("*")) if path.is_file()]
    result = {"schema": "mini-drop.prompt-boundaries-local.v1", "base_head": mapping["base_head"],
              "agent_version": "diagnosis-agent-v7-four-state-prompts", "created_at": datetime.now(timezone.utc).isoformat(),
              "checks": checks, "model_invocations": 0, "scope": "Offline production completion/correction/timeout boundaries and true StateGraph memory isolation. No live model result is inferred.", "files": rows}
    (DEST / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(DEST), "files": len(rows), "manifest_sha256": hashlib.sha256((DEST / "manifest.json").read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
