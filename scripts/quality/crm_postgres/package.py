"""导出只用于可销毁CRM数据库验收的私有源码/原始SQL快照。

不读.env/真实数据，不发布CRM源码；精确复制白名单并记录SHA，输出必须新建且与原仓库分离。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "business"))
from case_dsl import load_suite
from contract import canonical, digest
from worker import source_manifest


def export(source, suite_path, output):
    source, output = source.resolve(), output.resolve()
    if (
        not source.is_dir()
        or output.exists()
        or output.is_relative_to(source)
        or source.is_relative_to(output)
        or any(path.is_symlink() for path in (source, output, *output.parents))
    ):
        raise ValueError("CRM_EXPORT_PATH_REJECTED")
    suite = load_suite(suite_path)
    before = source_manifest(source)
    selected = [source / name for name in before] + [
        source / "supabase/baseline.sql",
        source / "tests/supabase-prelude.sql",
    ]
    if any(not path.is_file() or path.is_symlink() for path in selected):
        raise ValueError("CRM_REQUIRED_ORIGINAL_SQL_MISSING")
    output.mkdir(parents=True)
    manifest = {}
    total = 0
    for path in selected:
        maximum = 8_388_608 if path.suffix == ".sql" else 4_194_304
        with path.open("rb") as original:
            raw = original.read(maximum + 1)
        total += len(raw)
        if len(raw) > maximum or total > 67_108_864:
            raise ValueError("CRM_EXPORT_SIZE_BOUND")
        if re.search(
            rb"\bsk-[A-Za-z0-9]{24,}\b|-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
            raw,
        ):
            raise ValueError("CRM_EXPORT_CREDENTIAL_PATTERN_REJECTED")
        relative = path.relative_to(source).as_posix()
        target = output / "crm-source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        manifest[relative] = hashlib.sha256(raw).hexdigest()
    toolkit = Path(__file__).resolve().parent.parent
    tool_manifest = {}
    for folder in ("business", "crm_postgres"):
        for path in sorted((toolkit / folder).glob("*.py")):
            raw = path.read_bytes()
            relative = folder + "/" + path.name
            target = output / "tool" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            tool_manifest[relative] = hashlib.sha256(raw).hexdigest()
    (output / "generated-suite.json").write_bytes(canonical(suite))
    after = source_manifest(source)
    if before != after:
        raise ValueError("CRM_CHANGED_DURING_EXPORT")
    value = {
        "schema_version": 2,
        "private_only": True,
        "original_source_manifest_sha256": digest(before),
        "original_source_file_count": len(before),
        "snapshot_files": manifest,
        "snapshot_manifest_sha256": digest(manifest),
        "tool_files": tool_manifest,
        "tool_manifest_sha256": digest(tool_manifest),
        "generated_suite_sha256": digest(suite),
        "original_source_unchanged": True,
        "env_credentials_real_data_included": False,
        "sql_origin": "exact original CRM consolidated baseline plus its explicit Supabase compatibility prelude fixture",
    }
    (output / "manifest.private.json").write_bytes(canonical(value))
    return {
        key: item
        for key, item in value.items()
        if key not in {"snapshot_files", "tool_files"}
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crm-source", required=True, type=Path)
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(export(args.crm_source, args.suite, args.output), ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
