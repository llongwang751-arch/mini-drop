"""Keep the existing domain-switch phrase without removing disposition scoping."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
BASE = "f2cdbe7d711a58e2042bf4ef2cc01a0fcb051cf0"
NAME = "server/app/drop_insight/diagnosis_agent.py"
OLD = '"才扩展未尝试候选并切换可观察的证据域，其他未知原因最多一个兜底候选。"'
NEW = '"才扩展未尝试候选并切换证据域；新证据域必须可观察，其他未知原因最多一个兜底候选。"'


def main():
    clean = subprocess.run(["git", "show", BASE + ":" + NAME], cwd=ROOT, check=True, capture_output=True).stdout.decode("utf-8")
    dirty = (ROOT / NAME).read_text(encoding="utf-8")
    assert clean.count(OLD) == dirty.count(OLD) == 1
    clean, dirty = clean.replace(OLD, NEW, 1), dirty.replace(OLD, NEW, 1)
    assert ast.dump(ast.parse(clean)) == ast.dump(ast.parse(dirty))
    candidate = STAGE / "prompt-ci-clean" / NAME
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(clean, encoding="utf-8", newline="")
    (ROOT / NAME).write_text(dirty, encoding="utf-8", newline="")
    result = {"base_head": BASE, "files": [{"path": NAME,
              "clean_candidate": str(candidate.relative_to(ROOT)).replace("\\", "/"),
              "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(), "dirty_annotations_preserved": True}],
              "scope": "Text-only equivalent INVESTIGATE restriction and observable-domain requirement; existing assertion and all validators unchanged."}
    (STAGE / "prompt-literal-compatibility-candidates.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": 1, "base_head": BASE}))


if __name__ == "__main__":
    main()
