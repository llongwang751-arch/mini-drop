"""Teaching reference: validate this course's controlled-self RAW, not real profiles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

MAX_BYTES = 32 * 1024
FIELDS = {"schema_version", "source", "unit", "clock_ticks_per_second",
          "total_ticks", "iterations", "checksum"}


class InvalidInput(ValueError):
    """Only a fixed safe code is returned to callers."""


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidInput("DUPLICATE_FIELD")
        value[key] = item
    return value


def reject_constant(_value):
    raise InvalidInput("INVALID_NUMBER")


def integer(value, low, high):
    # bool subclasses int in Python; the wire contract does not accept it here.
    return type(value) is int and low <= value <= high


def analyze(raw: bytes) -> dict:
    if not 1 <= len(raw) <= MAX_BYTES:
        raise InvalidInput("RAW_SIZE_LIMIT")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                           parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise InvalidInput("INVALID_RAW_JSON") from None
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise InvalidInput("INVALID_RAW_FIELDS")
    if (not integer(value["schema_version"], 1, 1)
            or value["source"] != "controlled_self" or value["unit"] != "cpu_ticks"
            or not integer(value["iterations"], 1000, 10_000_000)
            or not integer(value["clock_ticks_per_second"], 1, 1_000_000_000)
            or not isinstance(value["checksum"], str)
            or re.fullmatch(r"[0-9a-f]{16}", value["checksum"]) is None):
        raise InvalidInput("INVALID_RAW_SCHEMA")
    ticks_per_second = value["clock_ticks_per_second"]
    if not integer(value["total_ticks"], 0, ticks_per_second * 60):
        raise InvalidInput("INVALID_RAW_TICKS")
    ticks = value["total_ticks"]
    return {
        "schema_version": 1,
        "summary": {
            "source": "controlled_self", "unit": "cpu_ticks", "total_ticks": ticks,
            "clock_ticks_per_second": ticks_per_second,
            "limitations": [
                "Measures only this collector's own controlled process CPU ticks.",
                "Contains no function stacks, request latency, or external process sampling.",
                "Clock resolution can produce zero ticks for short workloads.",
            ],
        },
        "flamegraph": {
            "name": "all", "value": ticks, "self": 0,
            "children": [{"name": "controlled_process", "value": ticks,
                          "self": ticks, "children": []}],
        },
        "input_sha256": hashlib.sha256(raw).hexdigest(),
    }


def atomic_output(path: Path, result: dict) -> None:
    payload = (json.dumps(result, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise InvalidInput("RESULT_SIZE_LIMIT")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".result-",
                                         suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()  # Only the single temporary file we created.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.raw.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        result = analyze(raw)
        atomic_output(args.output, result)
    except InvalidInput as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, OverflowError, RecursionError):
        print("ANALYZER_IO_ERROR", file=sys.stderr)
        return 3
    print('{"ok":true,"schema_version":1}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
