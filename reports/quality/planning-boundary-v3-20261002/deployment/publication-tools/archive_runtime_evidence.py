"""Append-only, byte-preserving publication of explicit deployment/live receipts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
PUBLIC = ROOT / "reports/quality/planning-boundary-v3-20261002"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(relative: str) -> dict:
    return json.loads((STAGE / relative).read_text(encoding="utf-8"))


def producer(manifest: dict, status: str) -> dict:
    return {"source_head": manifest["git_head"], "release_tag": manifest["release_tag"],
            "publication_status": status,
            "manifest_sha256": sha(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()),
            "manifest_digest_kind": "CANONICAL_JSON_FOR_PRODUCER_TAG_ONLY_RAW_FILE_SHA_IN_INVENTORY"}


def input_specs(include_followups: bool) -> list[dict]:
    final_manifest = read_json("release-final/manifest.json")
    prepared_manifest = read_json("release/manifest.json")
    verification = read_json("v3-publication-verification.json")
    assert verification["source_head"] == final_manifest["git_head"]
    assert final_manifest["release_tag"] in verification["release"]
    smoke = read_json("live-smoke/summary.json")
    assert smoke["source_head"] == final_manifest["git_head"]
    final_producer = producer(final_manifest, "DEPLOYED_AND_RUNTIME_VERIFIED")
    specs = [
        {"scope": "deployment/final", "producer": final_producer,
         "entries": [(f"release-final/{name}", name) for name in (
             "manifest.json", "deploy_runtime.py", "ci-run.json", "ci-jobs.json", "web-build.log",
             "platform-deployment.json", "platform-deployment.log", "release-tag.txt", "source-head.txt")],
         "boundary": "Actual final deployment; release source trees/bundles/private files are excluded."},
        {"scope": "deployment/prepared-first-source", "producer": producer(prepared_manifest, "PREPARED_ONLY_NOT_DEPLOYED"),
         "entries": [(f"release/{name}", name) for name in (
             "manifest.json", "deploy_runtime.py", "web-build.log", "release-tag.txt", "source-head.txt")]
                    + [("v3-measured-deployment-capacity.json", "v3-measured-deployment-capacity.json")],
         "boundary": "The e17f first source was prepared only; these files never prove a running release."},
        {"scope": "deployment", "producer": final_producer,
         "entries": [(name, name) for name in (
             "v3-publication-verification.json", "v3-knowledge-verification.json",
             "v3-publication-metadata-compatibility.json", "v3-final-measured-deployment-capacity.json",
             "v3-runtime-preactivation.json", "v3-runtime-predeployment.json")],
         "boundary": "Public verification and hashed predeployment/preactivation state; nested final/prepared archives have independent inventories."},
    ]
    live_entries = []
    for path in sorted((STAGE / "live-smoke").rglob("*")):
        if path.is_file() and path.suffix in {".json", ".log"} and path.name != "browser-readback-summary.json":
            live_entries.append((path.relative_to(STAGE).as_posix(), "smoke/" + path.relative_to(STAGE / "live-smoke").as_posix()))
    live_entries += [("live-runtime-audit.json", "live-runtime-audit.json"),
                     ("live_planning_smoke.py", "original-smoke-source.py")]
    if include_followups:
        for source, destination in (
            ("live-contract-readback.json", "live-contract-readback.json"),
            ("live-smoke/browser-readback-summary.json", "smoke/browser-readback-summary.json"),
            ("live-browser-input-summary.json", "live-browser-input-summary.json"),
            ("readback_live_contract.py", "readback-live-contract-source.py"),
        ):
            if (STAGE / source).is_file():
                live_entries.append((source, destination))
    specs.append({"scope": "live", "producer": final_producer, "entries": live_entries,
                  "boundary": "Original three-case smoke status/failed rows remain immutable; readback is separate inspection of the same durable records, never a retry model score.",
                  "original_smoke_status": smoke["status"],
                  "original_smoke_cases": [{key: row.get(key) for key in (
                      "name", "expected_disposition", "actual_disposition", "passed", "failure_type",
                      "planner_invocations", "model_invocations", "actual_persisted_tasks")}
                      for row in smoke["cases"]],
                  "original_smoke_helper_sha256": "4edc628342cbdd8ff221085b9d57456dc449a619949e51e2f12eddfaa04e59e9"})
    if include_followups and (STAGE / "browser").is_dir():
        browser_entries = [(path.relative_to(STAGE).as_posix(), path.relative_to(STAGE / "browser").as_posix())
                           for path in sorted((STAGE / "browser").rglob("*"))
                           if path.is_file() and path.suffix.casefold() in {".json", ".log", ".png", ".jpg", ".jpeg", ".webp"}]
        if browser_entries:
            specs.append({"scope": "live/browser", "producer": final_producer, "entries": browser_entries,
                          "boundary": "Real browser rendering uses the explicit durable-case input summary; screenshot success cannot change original model/smoke case failures."})
        source_entries = [(name, name) for name in ("browser_planning_smoke.py", "browser_planning_smoke.mjs")
                          if (STAGE / name).is_file()]
        if source_entries:
            specs.append({"scope": "live/browser-source", "producer": final_producer, "entries": source_entries,
                          "boundary": "Original browser helpers inspect durable sessions; they do not create new diagnoses, probes or model calls."})
    specs.append({"scope": "deployment/publication-tools", "producer": final_producer,
                  "entries": [(Path(__file__).name, Path(__file__).name)],
                  "boundary": "Append-only evidence generator; no deployment or provider action."})
    return specs


def credential_review(specs: list[dict]) -> dict:
    spec = importlib.util.spec_from_file_location("local_credential_audit", STAGE / "archive_development_evidence.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    values, private_scope = helper.private_values_in_memory()
    git_spec = importlib.util.spec_from_file_location("git_identity_audit", ROOT / "output/quality/ci-validation-20260927/github_ci.py")
    git_helper = importlib.util.module_from_spec(git_spec)
    git_spec.loader.exec_module(git_helper)
    git_client = git_helper.session()
    try:
        public_username = git_client.auth[0].encode() if git_client.auth else b""
        git_password = git_client.auth[1].encode() if git_client.auth else b""
        assert not public_username or public_username != git_password
    finally:
        git_client.close()
    # Official CI receipts contain the public actor login. A credential-helper
    # username is that public identity; its password/token remains a secret.
    values = [value for value in values if value != public_username]
    private_scope["actual_sensitive_values_checked"] = len(values)
    private_scope["public_git_username_excluded_from_secret_values"] = bool(public_username)
    patterns = {
        "PRIVATE_KEY_PEM": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        "GITHUB_OR_OPENAI_TOKEN": re.compile(rb"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,})(?![A-Za-z0-9])"),
        "JWT_BEARER": re.compile(rb"Bearer\s+eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    }
    sensitive_keys = re.compile(r"^(?:authorization|password|passwd|api_key|access_token|refresh_token|secret|private_key|env|environment_variables|cookie|cookies)$", re.I)
    visited = sorted({source for spec in specs for source, _ in spec["entries"]})
    violations = []

    def visit(value, relative: str, trail: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                next_trail = f"{trail}.{key}" if trail else key
                if sensitive_keys.fullmatch(key) and item:
                    violations.append({"path": relative, "kind": "SENSITIVE_JSON_FIELD", "field": next_trail})
                visit(item, relative, next_trail)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, relative, f"{trail}[{index}]")

    for relative in visited:
        path = STAGE / relative
        if not path.is_file():
            raise ValueError(f"required public receipt is missing: {relative}")
        raw = path.read_bytes()
        if any(value in raw for value in values):
            violations.append({"path": relative, "kind": "ACTUAL_LOCAL_SENSITIVE_VALUE"})
        for kind, pattern in patterns.items():
            if pattern.search(raw):
                violations.append({"path": relative, "kind": kind})
        if path.suffix == ".json":
            visit(json.loads(raw), relative)
    if violations:
        print(json.dumps({"status": "CREDENTIAL_REVIEW_REQUIRED", "violations": violations}))
        raise ValueError("abort before copying; no credential values printed")
    return {"status": "PASSED_FOR_DECLARED_LOCAL_SCOPE", "files_scanned": len(visited),
            "actual_sensitive_value_matches": 0, "high_confidence_pattern_matches": 0,
            "sensitive_nonempty_json_fields": 0, "json_environment_is_hash_only": True,
            **private_scope,
            "production_environment_followup": "Root's aggregate publication audit checks actual production-specific secrets in memory."}


def archive_scope(spec: dict, credential_audit: dict) -> dict:
    scope_root = PUBLIC / spec["scope"]
    scope_root.mkdir(parents=True, exist_ok=True)
    files = []
    changed = []
    for relative, destination_relative in spec["entries"]:
        source = STAGE / relative
        destination = scope_root / destination_relative
        if not destination.resolve().is_relative_to(scope_root.resolve()):
            raise ValueError("destination escaped its authorized scope")
        raw = source.read_bytes()
        if destination.exists():
            if destination.read_bytes() != raw:
                raise ValueError(f"immutable archived bytes differ from source: {destination_relative}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            changed.append(destination_relative)
        assert destination.read_bytes() == raw
        files.append({"path": destination_relative, "source": source.relative_to(ROOT).as_posix(),
                      "bytes": len(raw), "source_sha256": sha(raw), "archived_sha256": sha(destination.read_bytes()),
                      "copied_byte_for_byte": True})
    files.sort(key=lambda row: row["path"])
    inventory_sha = sha(json.dumps(files, sort_keys=True, ensure_ascii=False).encode())
    manifests = sorted(scope_root.glob("archive-manifest*.json"))
    previous = None
    for path in manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        if previous is None or data["revision"] > previous[1]["revision"]:
            previous = (path, data)
    if previous and previous[1]["inventory_sha256"] == inventory_sha:
        return {"scope": spec["scope"], "status": "UNCHANGED_IMMUTABLE", "file_count": len(files),
                "manifest": previous[0].relative_to(ROOT).as_posix(), "manifest_sha256": sha(previous[0].read_bytes())}
    if previous:
        previous_inventory = {row["path"]: row for row in previous[1]["files"]}
        current_inventory = {row["path"]: row for row in files}
        if not all(current_inventory.get(path) == row for path, row in previous_inventory.items()):
            raise ValueError("append-only manifest cannot remove or rewrite existing inventory")
    revision = previous[1]["revision"] + 1 if previous else 1
    name = "archive-manifest.json" if revision == 1 else f"archive-manifest-r{revision}.json"
    destination = scope_root / name
    assert not destination.exists()
    metadata = {key: value for key, value in spec.items() if key != "entries"}
    manifest = {"schema": "mini-drop.runtime-evidence-archive.v1", "status": "VERIFIED_BYTE_IDENTICAL",
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "revision": revision,
                "previous_manifest": previous[0].relative_to(ROOT).as_posix() if previous else None,
                "previous_manifest_sha256": sha(previous[0].read_bytes()) if previous else None,
                "inventory_sha256": inventory_sha, "file_count": len(files),
                "total_bytes": sum(row["bytes"] for row in files), "files": files,
                "added_files_this_revision": sorted(changed), **metadata,
                "credential_audit": credential_audit,
                "excluded": ["tgz/zip/source bundles", "private env/key files", "database/cache/temp", "vector index data"],
                "preservation": {"existing_archive_bytes_unchanged": True, "old_manifest_revisions_unchanged": True,
                                 "raw_failures_never_relabelled": True, "stage_originals_unchanged": True},
                "scope_limits": ["No source/Git/provider/model/deployment action is performed by this generator.",
                                 "Prepared source metadata is not proof of deployment.",
                                 "Original failed smoke cases and later durable-event readback are separate observations."]}
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"scope": spec["scope"], "status": manifest["status"], "revision": revision,
            "file_count": len(files), "added_files": len(changed), "inventory_sha256": inventory_sha,
            "manifest": destination.relative_to(ROOT).as_posix(), "manifest_sha256": sha(destination.read_bytes())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-followups", action="store_true")
    args = parser.parse_args()
    specs = input_specs(args.include_followups)
    if sha((STAGE / "live_planning_smoke.py").read_bytes()) != "4edc628342cbdd8ff221085b9d57456dc449a619949e51e2f12eddfaa04e59e9":
        raise ValueError("original smoke helper must not be replaced by a corrected helper")
    credential_audit = credential_review(specs)
    scopes = [archive_scope(spec, credential_audit) for spec in specs]
    print(json.dumps({"status": "VERIFIED_APPEND_ONLY", "include_followups": args.include_followups, "scopes": scopes,
                      "credential_audit_scope": credential_audit["status"]}))


if __name__ == "__main__":
    main()
