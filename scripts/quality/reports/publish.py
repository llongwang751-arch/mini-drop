"""把审阅后的有界摘要原子发布为只读记录；不接受原始profile、日志或任意报告字段。

此工具不证明实验确实发生，不代替独立人审。执行者须把真实收据投影为共享Schema，
核对来源SHA后发布；已有ID的不同字节拒绝覆盖，原失败不得改成成功。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[3]
MAX_BYTES = 32768
MAX_RECORDS = 128
PRIVATE = re.compile(
    r"sk-[a-z0-9]{20,}|-----BEGIN|[a-z][a-z0-9+.-]{1,20}://|x-amz-|authorization:|bearer |password=|cookie:",
    re.IGNORECASE,
)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_RECORD_KEY")
        result[key] = value
    return result


def validate(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("RECORD_TOO_LARGE")
    row = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=unique_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_RECORD")),
    )
    schema = json.loads(
        (ROOT / "contracts/quality-run.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    ).validate(row)

    def check_text(item):
        if isinstance(item, str):
            if PRIVATE.search(item) or any(
                unicodedata.category(c) == "Cc" for c in item
            ):
                raise ValueError("PRIVATE_OR_CONTROL_RECORD_TEXT")
        elif isinstance(item, dict):
            for value in item.values():
                check_text(value)
        elif isinstance(item, list):
            for value in item:
                check_text(value)

    check_text(row)
    for key in ["title", "fixture_scope"]:
        if not row[key].strip():
            raise ValueError("EMPTY_RECORD_TEXT")
    c = row["counts"]
    if type(row["schema_version"]) is not int or any(
        type(v) is not int for v in c.values()
    ):
        raise ValueError("RECORD_INTEGER_REQUIRED")
    if c["passed"] + c["failed"] + c["skipped"] != c["total"]:
        raise ValueError("RECORD_COUNT_MISMATCH")
    status = row["status"]
    if (
        status == "passed"
        and (c["failed"] or c["skipped"])
        or status == "failed"
        and not c["failed"]
        or status == "incomplete"
        and (not c["skipped"] or c["failed"])
    ):
        raise ValueError("RECORD_STATUS_MISMATCH")
    start, finish = (
        datetime.fromisoformat(row[k].replace("Z", "+00:00"))
        for k in ["started_at", "finished_at"]
    )
    if (
        start.utcoffset() is None
        or finish.utcoffset() is None
        or finish < start
        or finish - start > timedelta(days=7)
        or finish > datetime.now(timezone.utc) + timedelta(hours=1)
    ):
        raise ValueError("RECORD_TIME_MISMATCH")
    for key in ["metrics", "checks"]:
        ids = [x["id"] for x in row[key]]
        if len(set(ids)) != len(ids):
            raise ValueError("DUPLICATE_RECORD_ITEM")
        for item in row[key]:
            if not item["label"].strip():
                raise ValueError("EMPTY_RECORD_TEXT")
    if row["status"] == "passed" and any(
        x["status"] != "passed" for x in row["checks"]
    ):
        raise ValueError("RECORD_CHECK_STATUS_MISMATCH")
    if any(not x.strip() for x in row["limits"]):
        raise ValueError("EMPTY_RECORD_TEXT")
    for m in row["metrics"]:
        v = m["value"]
        if v is not None and (
            not math.isfinite(v)
            or m["unit"] == "ratio"
            and v > 1
            or m["unit"] == "percent"
            and v > 100
        ):
            raise ValueError("RECORD_METRIC_RANGE")
    return row


def read_regular(path: Path) -> bytes:
    """先无阻塞打开并校验描述符，再限长读取；同ID重放也不能绕过大小边界。"""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("REGULAR_BOUNDED_RECORD_REQUIRED")
        data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("RECORD_TOO_LARGE")
    return data


def publish(data: bytes, directory: Path, expected_sha: str) -> str:
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError("RECORD_SOURCE_SHA_MISMATCH")
    row = validate(data)
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("RECORD_DIRECTORY_INVALID")
    entries = list(islice(directory.iterdir(), MAX_RECORDS + 1))
    if len(entries) > MAX_RECORDS:
        raise ValueError("RECORD_CATALOG_FULL")
    if any(x.is_symlink() or not x.is_file() or x.suffix != ".json" for x in entries):
        raise ValueError("RECORD_DIRECTORY_INVALID")
    target = directory / (row["id"] + ".json")
    if target.exists():
        if read_regular(target) == data:
            return expected_sha
        raise ValueError("IMMUTABLE_RECORD_EXISTS")
    if len(entries) >= MAX_RECORDS:
        raise ValueError("RECORD_CATALOG_FULL")
    # 临时文件放在父目录，API扫描记录目录时不会把半成品当成完整记录。
    fd, name = tempfile.mkstemp(prefix=".quality-publish-", dir=directory.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        # Windows的只读属性会同时作用于两个硬链接，导致临时名称无法清理；
        # 生产Linux使用0440及API只读挂载，Windows开发目录由当前用户权限保护。
        if os.name == "posix":
            os.chmod(name, 0o440)
        os.link(name, target)  # 同文件系统内原子建新名称；并发同ID不能覆盖。
        if os.name == "posix":
            parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        Path(name).unlink(missing_ok=True)
    return expected_sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    try:
        data = read_regular(args.record)
        digest = publish(data, args.directory, args.expected_sha256)
    except (OSError, ValueError, jsonschema.ValidationError):
        print(json.dumps({"passed": False, "error": "QUALITY_RECORD_PUBLISH_REJECTED"}))
        return 1
    print(json.dumps({"passed": True, "record_sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
