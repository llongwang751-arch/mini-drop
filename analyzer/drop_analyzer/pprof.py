"""Bounded pprof profile.proto decoder, including inline frames and labels.

Wire field numbers follow github.com/google/pprof/proto/profile.proto. No
application protobuf schema or generated service code is coupled to this parser.
"""

import gzip
import io
import zlib

from .errors import AnalysisError
from .stacks import stack_result

MAX_BYTES = 64 * 1024 * 1024
MAX_ROWS = 500_000


def invalid(message):
    raise AnalysisError("INVALID_INPUT", message)


def varint(data, offset):
    result = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            invalid("Truncated protobuf varint")
        value = data[offset]
        offset += 1
        if shift == 63 and value > 1:
            invalid("Protobuf varint overflow")
        result |= (value & 127) << shift
        if not value & 128:
            return result, offset
    invalid("Protobuf varint exceeds 64 bits")


def fields(data):
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not number:
            invalid("Invalid protobuf field number")
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire in (1, 5):
            end = offset + (8 if wire == 1 else 4)
            if end > len(data):
                invalid("Truncated fixed protobuf field")
            value, offset = data[offset:end], end
        elif wire == 2:
            size, offset = varint(data, offset)
            end = offset + size
            if end > len(data):
                invalid("Truncated protobuf message")
            value, offset = data[offset:end], end
        else:
            invalid("Unsupported protobuf wire encoding")
        yield number, wire, value


def message(data):
    result = {}
    for number, _, value in fields(data):
        result.setdefault(number, []).append(value)
    return result


def one(row, field, default=0):
    return row.get(field, [default])[-1]


def repeated(row, field):
    result = []
    for value in row.get(field, []):
        if isinstance(value, int):
            result.append(value)
        else:
            offset = 0
            while offset < len(value):
                item, offset = varint(value, offset)
                result.append(item)
    return result


def signed(value):
    return value if value < 2**63 else value - 2**64


def analyze_pprof(raw, *, kind, params=None):
    params = params or {}
    if len(raw) > MAX_BYTES:
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "pprof input exceeds byte limit")
    if raw[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(MAX_BYTES + 1)
        except (OSError, EOFError, zlib.error):
            invalid("Invalid pprof gzip stream")
        if len(raw) > MAX_BYTES:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "Decoded pprof exceeds byte limit"
            )
    profile = message(raw)
    strings = []
    try:
        strings = [
            value.decode("utf-8", errors="strict") for value in profile.get(6, [])
        ]
    except (UnicodeDecodeError, AttributeError):
        invalid("pprof string table must be UTF-8")
    if not strings or strings[0] != "":
        invalid("pprof string table must start with empty string")

    def string(index):
        if not isinstance(index, int) or not 0 <= index < len(strings):
            invalid("pprof string table reference is invalid")
        return strings[index]

    types = []
    for data in profile.get(1, []):
        row = message(data)
        types.append({"type": string(one(row, 1)), "unit": string(one(row, 2))})
    if not types:
        invalid("pprof profile has no sample types")
    requested = params.get("sample_type")
    if not requested and params.get("profile_type") in {
        value["type"] for value in types
    }:
        requested = params["profile_type"]
    default = string(one(profile, 14))
    preferred = (
        [requested]
        if requested
        else (
            ["cpu", "samples"]
            if kind == "GO_CPU"
            else [
                default,
                "inuse_space",
                "alloc_space",
                "inuse_objects",
                "alloc_objects",
            ]
        )
    )
    selected = next(
        (
            i
            for name in preferred
            if name
            for i, row in enumerate(types)
            if row["type"] == name
        ),
        None,
    )
    if selected is None:
        if requested:
            invalid("Requested pprof sample type does not exist")
        selected = len(types) - 1
    allowed_types = (
        {"cpu", "samples"}
        if kind == "GO_CPU"
        else {
            "alloc_objects",
            "alloc_space",
            "inuse_objects",
            "inuse_space",
            "objects",
            "space",
        }
    )
    if types[selected]["type"] not in allowed_types:
        invalid("Selected pprof sample type is incompatible with this task kind")
    functions = {}
    for data in profile.get(5, []):
        row = message(data)
        identifier = one(row, 1)
        if not identifier or identifier in functions:
            invalid("Duplicate or zero pprof function ID")
        functions[identifier] = {
            "name": string(one(row, 2)) or string(one(row, 3)) or "[unknown]",
            "filename": string(one(row, 4)),
            "start_line": signed(one(row, 5)),
        }
    locations = {}
    for data in profile.get(4, []):
        row = message(data)
        identifier = one(row, 1)
        if not identifier or identifier in locations:
            invalid("Duplicate or zero pprof location ID")
        inline = []
        for line in row.get(4, []):
            function_id = one(message(line), 1)
            if function_id not in functions:
                invalid("pprof location references missing function")
            inline.append(functions[function_id]["name"])
        locations[identifier] = inline or [f"0x{one(row, 3):x}"]
    if any(len(profile.get(field, [])) > MAX_ROWS for field in (2, 4, 5, 6)):
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "pprof table exceeds row limit")
    label_filter = params.get("label_filter") or params.get("labels") or {}
    if not isinstance(label_filter, dict) or len(label_filter) > 32:
        invalid("pprof label filter must be an object with at most 32 entries")
    include = params.get("include")
    exclude = params.get("exclude")
    if any(
        value and (not isinstance(value, str) or len(value) > 256)
        for value in (include, exclude)
    ):
        invalid("pprof frame filter exceeds limit")
    stacks, labels_seen, filtered, vector_totals = [], {}, 0, [0] * len(types)
    for data in profile.get(2, []):
        row = message(data)
        values = [signed(value) for value in repeated(row, 2)]
        if len(values) != len(types):
            invalid("pprof sample value vector length mismatches sample types")
        labels = {}
        for data_label in row.get(3, []):
            label = message(data_label)
            key = string(one(label, 1))
            value = string(one(label, 2)) if 2 in label else str(signed(one(label, 3)))
            labels.setdefault(key, []).append(value)
            if len(labels_seen) < 100:
                labels_seen.setdefault(key, set()).add(value)
        frames = []
        for identifier in reversed(repeated(row, 1)):
            if identifier not in locations:
                invalid("pprof sample references missing location")
            frames.extend(reversed(locations[identifier]))
        if (
            any(
                str(value) not in labels.get(str(key), [])
                for key, value in label_filter.items()
            )
            or (include and not any(include in frame for frame in frames))
            or (exclude and any(exclude in frame for frame in frames))
        ):
            filtered += 1
            continue
        if values[selected] < 0:
            invalid(
                "Differential pprof with negative selected weights requires a differential renderer"
            )
        for index, value in enumerate(values):
            vector_totals[index] += value
        stacks.append((frames, values[selected]))
    parsed = stack_result(
        stacks,
        unit=types[selected]["unit"],
        metadata={
            "format": "pprof/protobuf",
            "profile_type": types[selected]["type"],
            "sample_types": types,
            "duration_ns": one(profile, 10),
            "time_ns": one(profile, 9),
            "period": one(profile, 12),
            "filtered_samples": filtered,
            "filters": {"labels": label_filter, "include": include, "exclude": exclude},
            "labels": {
                key: sorted(values)[:100] for key, values in labels_seen.items()
            },
        },
    )
    observation_index = next(
        (
            index
            for index, value in enumerate(types)
            if value["type"] == "samples" and value["unit"] in {"count", "samples"}
        ),
        None,
    )
    if observation_index is not None:
        parsed["metadata"]["observation_count"] = vector_totals[observation_index]
        parsed["top"]["observation_count"] = vector_totals[observation_index]
    if kind == "GO_HEAP":
        parsed["sections"] = {
            "heap": {
                "format": "pprof",
                "profile_type": types[selected]["type"],
                "unit": types[selected]["unit"],
                "sample_types": [
                    {**value, "total": vector_totals[index]}
                    for index, value in enumerate(types)
                ],
                "classes": [],
                "dominators": [],
                "leak_paths": [],
                "totals": {"sample_weight": parsed["metadata"]["sample_count"]},
                "limitations": [
                    "pprof 是分配栈采样，不能计算对象引用图、dominator 或精确 retained size。"
                ],
            }
        }
    return parsed
