"""Byte-preserving archive of an explicit public/local evidence allowlist."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
DEST = ROOT / "reports/quality/planning-boundary-v3-20261002/development"
SOURCE_HEAD = "e17f287ee7ab7270ffc73534c90c14af297d3228"
BASE_HEAD = "13dc0ed03f23eb5b7049d04f154e256d0ebdb486"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def select() -> list[Path]:
    paths = [STAGE / name for name in (
        "knowledge-development-lexical.json", "knowledge-tests-first.xml", "knowledge-tests-second.xml",
        "knowledge-tests-final.xml", "knowledge-local-test-summary.json", "knowledge-official-sources.json",
        "knowledge-independent-source-audit.json", "classification-independent-review.json",
        "service-metadata-first.xml", "evaluation-v3-tests.xml", "evaluation-v3-tests.log",
        "source-scoped-index-verification.json", "source-commit.json",
        "local-python-final/report.json", "local-python-final/report.html",
        "local-python-final/python-all/command-1.log", "local-python-final/python-all/junit.xml",
        "local-python-final/python-all/coverage.json",
    )]
    paths += sorted((STAGE / "knowledge-exposed-v2-bm25").rglob("*"))
    paths += sorted(STAGE.glob("classification*.xml"))
    paths += sorted(STAGE.glob("historical-v2-*.xml"))
    paths += sorted(STAGE.glob("historical-v2-*.log"))
    paths += sorted((STAGE / "local-python-final/contracts").glob("command-*.log"))
    paths.append(Path(__file__).resolve())
    paths = sorted(set(path for path in paths if path.is_file()))
    if any(any(part.casefold() in {"private", ".pytest_cache", "__pycache__"} or part.startswith("pytest-")
               for part in path.relative_to(STAGE).parts) for path in paths):
        raise ValueError("allowlist reached private or pytest temporary data")
    for path in paths:
        if path.suffix not in {".json", ".xml", ".log", ".html", ".py"}:
            raise ValueError("unexpected evidence suffix")
    for required in ("knowledge-tests-first.xml", "knowledge-tests-second.xml", "knowledge-tests-final.xml",
                     "classification-final-r4-tests.xml", "historical-v2-first-failure.xml",
                     "historical-v2-final.xml", "local-python-final/python-all/junit.xml"):
        if STAGE / required not in paths:
            raise ValueError("required historical/final evidence missing")
    return paths


def private_values_in_memory() -> tuple[list[bytes], dict]:
    values = []
    sensitive = re.compile(r"(?:API_KEY|TOKEN|PASSWORD|SECRET|PRIVATE_KEY)", re.I)
    for key, value in os.environ.items():
        if sensitive.search(key) and len(value) >= 12:
            values.append(value.encode())
    env_files = []
    for parent in (ROOT, ROOT / "deploy/env"):
        if parent.is_dir():
            for path in parent.iterdir():
                name = path.name.casefold()
                if (path.is_file() and (name.startswith(".env") or name.endswith(".env"))
                        and "example" not in name and "sample" not in name):
                    env_files.append(path)
                    for line in path.read_text(encoding="utf-8").splitlines():
                        key, sep, value = line.partition("=")
                        value = value.strip().strip("\"'")
                        if sep and sensitive.search(key) and len(value) >= 12:
                            values.append(value.encode())
    helper = ROOT / "output/quality/ci-validation-20260927/github_ci.py"
    spec = importlib.util.spec_from_file_location("private_git_credential_helper", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module.session()  # Local Git credential read, never an HTTP request.
    try:
        if client.auth:
            for value in client.auth:
                if isinstance(value, str) and len(value) >= 12:
                    values.append(value.encode())
        authorization = client.headers.get("Authorization", "")
        if authorization:
            token = authorization.split(" ", 1)[-1]
            if len(token) >= 12:
                values.append(token.encode())
    finally:
        client.close()
    values = sorted(set(values))
    return values, {"actual_sensitive_values_checked": len(values), "actual_values_not_written": True,
                    "sources": ["current process sensitive environment values", "local non-example env sensitive values",
                                "local Git credential helper"],
                    "env_files_examined": len(env_files), "remote_production_environment_values_examined": False}


def counts(path: Path) -> dict:
    root = ET.fromstring(path.read_bytes())
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    result = {key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    result["passed"] = result["tests"] - result["failures"] - result["errors"] - result["skipped"]
    result["interpretation"] = ("EMPTY_SUITE_NOT_A_PASS" if result["tests"] == 0 else
                                "FAILED_RETAINED" if result["failures"] or result["errors"] else
                                "PASSED_WITH_REGISTERED_SKIPS" if result["skipped"] else "PASSED")
    return result


def main() -> None:
    selected = select()
    if DEST.exists():
        raise ValueError("new archive directory required; prior evidence must not be replaced")
    values, credential_scope = private_values_in_memory()
    patterns = {
        "PRIVATE_KEY_PEM": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        "GITHUB_OR_OPENAI_TOKEN": re.compile(rb"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,})(?![A-Za-z0-9])"),
        "JWT_BEARER": re.compile(rb"Bearer\s+eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    }
    violations = []
    for path in selected:
        raw = path.read_bytes()
        if any(value in raw for value in values):
            violations.append({"path": path.relative_to(STAGE).as_posix(), "kind": "ACTUAL_SENSITIVE_VALUE"})
        for kind, pattern in patterns.items():
            if pattern.search(raw):
                violations.append({"path": path.relative_to(STAGE).as_posix(), "kind": kind})
    if violations:
        print(json.dumps({"status": "CREDENTIAL_REVIEW_REQUIRED", "violations": violations}))
        raise ValueError("archive aborted before copying; actual values are never printed")
    source_index = json.loads((STAGE / "source-scoped-index-verification.json").read_text(encoding="utf-8"))
    assert source_index["staged_files"] == 33 and source_index["base_head"] == BASE_HEAD
    source_commit = json.loads((STAGE / "source-commit.json").read_text(encoding="utf-8"))
    assert source_commit["source_head"] == SOURCE_HEAD and source_commit["files"] == 33
    code_equivalences = []
    for row in source_index["files"]:
        relative = row["path"]
        if not relative.endswith(".py"):
            continue  # Never inspect new evaluation question or oracle contents.
        committed = subprocess.check_output(["git", "show", f"{SOURCE_HEAD}:{relative}"], cwd=ROOT)
        working = (ROOT / relative).read_bytes()
        equal = ast.dump(ast.parse(committed.decode()), include_attributes=False) == ast.dump(ast.parse(working.decode()), include_attributes=False)
        assert equal, relative
        code_equivalences.append({"path": relative, "committed_sha256": sha(committed),
                                  "working_bytes_sha256": sha(working), "python_ast_equal": True})
    full_report = json.loads((STAGE / "local-python-final/report.json").read_text(encoding="utf-8"))
    assert full_report["environment"]["git_head"] == BASE_HEAD and full_report["environment"]["dirty"] is True
    DEST.mkdir(parents=True)
    entries = []
    for source in selected:
        relative = source.relative_to(STAGE)
        destination = DEST / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        original = source.read_bytes()
        shutil.copyfile(source, destination)
        copied = destination.read_bytes()
        assert original == copied
        entries.append({"path": relative.as_posix(), "source": source.relative_to(ROOT).as_posix(),
                        "bytes": len(original), "source_sha256": sha(original), "archived_sha256": sha(copied),
                        "copied_byte_for_byte": True})
    tests = [{"path": path.relative_to(STAGE).as_posix(), "counts": counts(path)}
             for path in selected if path.suffix == ".xml"]
    manifest = {
        "schema": "mini-drop.local-development-evidence-archive.v1", "status": "VERIFIED_BYTE_IDENTICAL",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "file_count": len(entries),
        "total_bytes": sum(row["bytes"] for row in entries), "files": entries,
        "generator": "archive_development_evidence.py", "test_runs": tests,
        "test_count_boundary": "Overlapping focused and full suites are separate receipts; do not sum them. Registered skips and empty suites are not passes.",
        "local_full_suite_provenance": {
            "producer_git_head": BASE_HEAD, "dirty_worktree": True, "clean_git_test_run_claimed": False,
            "scoped_candidate_files": 33, "equivalent_committed_source_head": SOURCE_HEAD,
            "python_candidate_ast_equivalence": code_equivalences,
            "scope": "Root's local full run happened on the 13dc0ed0 dirty worktree with focused candidates; e17f287e is its subsequently committed scoped source, not the Git checkout used to run the tests.",
            "environment_receipt": full_report["environment"],
        },
        "credential_audit": {"status": "PASSED_FOR_DECLARED_LOCAL_SCOPE", "files_scanned": len(selected),
                              "actual_sensitive_value_matches": 0, "high_confidence_pattern_matches": 0,
                              "patterns": list(patterns), **credential_scope,
                              "production_environment_followup": "Root performs any final actual production-env-value check in the aggregate publication audit."},
        "excluded": ["pytest temporary directories", "pytest/cache data", "databases", ".coverage binary database",
                     "private env files", "source bundles and complete release checkout", "new v3 private evaluation questions"],
        "scope_limits": ["Development lexical metrics and already exposed v2 lexical regression are not new blind evaluation.",
                         "Classification independent review is a public pure-function code review, not a real model or deployment test.",
                         "All original stage artifacts, including failures, remain untouched.",
                         "No canonical docs, source code, Git state, provider calls, cloud index or deployment were changed by archiving."],
    }
    path = DEST / "archive-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "path": str(DEST), "raw_files": len(entries),
                      "total_files_including_manifest": len(entries) + 1, "bytes": manifest["total_bytes"],
                      "archive_manifest_sha256": sha(path.read_bytes()), "python_equivalences": len(code_equivalences)}))


if __name__ == "__main__":
    main()
