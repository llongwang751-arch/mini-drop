"""Native gperftools/jemalloc profiles and LLVM BOLT fdata, without text fallbacks."""

import math
import re
import struct
from collections import Counter, defaultdict

from .errors import AnalysisError
from .stacks import stack_result


def invalid(message):
    raise AnalysisError("INVALID_INPUT", message)


def native_cpu(raw):
    layout = None
    for code in ("<I", ">I", "<Q", ">Q"):
        width = struct.calcsize(code)
        if len(raw) >= width * 5:
            header = [
                struct.unpack_from(code, raw, offset * width)[0] for offset in range(5)
            ]
            if header[0:3] == [0, 3, 0] and header[3] > 0 and header[4] == 0:
                layout = (code, width, header[3])
                break
    if layout is None:
        invalid("Invalid gperftools CPU profile header")
    code, width, period = layout
    offset, stacks, ended = width * 5, [], False
    while offset + 2 * width <= len(raw):
        count, depth = (
            struct.unpack_from(code, raw, offset)[0],
            struct.unpack_from(code, raw, offset + width)[0],
        )
        offset += 2 * width
        if depth > 128 or offset + depth * width > len(raw):
            invalid("Invalid gperftools stack depth or truncated sample")
        addresses = [
            struct.unpack_from(code, raw, offset + index * width)[0]
            for index in range(depth)
        ]
        offset += depth * width
        if count == 0 and addresses == [0]:
            ended = True
            break
        stacks.append(
            (
                [
                    f"0x{address - (index > 0):x}"
                    for index, address in reversed(list(enumerate(addresses)))
                ],
                count,
            )
        )
        if len(stacks) > 500_000:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "Native profile exceeds stack limit"
            )
    if not ended:
        invalid("gperftools CPU profile is missing its end marker")
    return stack_result(
        stacks,
        metadata={
            "format": "gperftools-cpu",
            "period_us": period,
            "symbolization": "raw_program_counters",
            "memory_maps": raw[offset:].decode("utf8", errors="replace")[:32768],
        },
    )


HEAP_ROW = re.compile(r"^\s*(\d+):\s*(\d+)\s*\[\s*(\d+):\s*(\d+)\s*\]\s*@\s*(.*)$")
JE_ROW = re.compile(r"^\s*t\*:\s*(\d+):\s*(\d+)\s*\[\s*(\d+):\s*(\d+)\s*\]")


def native_heap(raw, *, kind, params=None):
    params = params or {}
    try:
        lines = raw.decode("utf8", errors="strict").splitlines()
    except UnicodeDecodeError:
        invalid("Native heap profile is not UTF-8")
    if not lines:
        invalid("Empty native heap profile")
    je = kind == "JEPROF_HEAP"
    if je:
        match = re.fullmatch(r"heap_v(2)/(\d+)", lines[0].strip())
        if not match:
            invalid("Invalid or unsupported jemalloc heap_v2 header")
        rate = int(match.group(2))
        if rate <= 0:
            invalid("jemalloc sampling rate must be positive")
    expected_totals = None
    if not je:
        header = re.fullmatch(
            r"heap profile:\s*(\d+):\s*(\d+)\s*\[\s*(\d+):\s*(\d+)\s*\]\s*@\s*\S+",
            lines[0].strip(),
        )
        if not header:
            invalid("Invalid gperftools heap profile header")
        expected_totals = list(map(int, header.groups()))
    profile_type = params.get("sample_type")
    indices = {
        "inuse_objects": 0,
        "inuse_space": 1,
        "alloc_objects": 2,
        "alloc_space": 3,
    }
    profile_type = profile_type or (
        params.get("profile_type")
        if params.get("profile_type") in indices
        else "inuse_space"
    )
    if profile_type not in indices:
        invalid("Native heap sample type must be alloc/inuse objects/space")
    stacks, current, totals = [], None, [0, 0, 0, 0]
    raw_totals = [0, 0, 0, 0]
    maps = []
    for line in lines[1:]:
        if line.startswith("MAPPED_LIBRARIES:"):
            maps = lines[lines.index(line) + 1 :]
            break
        if je and line.startswith("@"):
            if current is not None:
                invalid("Native heap stack is missing its sample counters")
            current = re.findall(r"0x[0-9a-fA-F]+", line)
            continue
        match = JE_ROW.match(line) if je else HEAP_ROW.match(line)
        if match:
            if je and current is None:
                if expected_totals is not None:
                    invalid("Duplicate jemalloc global counters")
                expected_totals = list(map(int, match.groups()[:4]))
                continue  # Global/thread totals, not a stack sample.
            values = list(map(int, match.groups()[:4]))
            raw_totals = [left + right for left, right in zip(raw_totals, values)]
            addresses = current if je else re.findall(r"0x[0-9a-fA-F]+", match.group(5))
            if not addresses or len(addresses) > 128:
                invalid("Native heap stack is empty or exceeds depth limit")
            if je:
                # Standard jeprof Poisson sampling correction, separate for
                # live and cumulative allocation counters.
                for count_index in (0, 2):
                    count, size = values[count_index : count_index + 2]
                    if count:
                        scale = -math.expm1(-(size / count) / rate)
                        if scale:
                            values[count_index] = round(count / scale)
                            values[count_index + 1] = round(size / scale)
            totals = [left + right for left, right in zip(totals, values)]
            stacks.append(
                (
                    [
                        f"0x{max(0, int(address, 16) - 1):x}"
                        for address in reversed(addresses)
                    ],
                    values[indices[profile_type]],
                )
            )
            current = None
        elif line.strip() and not (
            je and (line.lstrip().startswith("t") or line.startswith("#"))
        ):
            invalid("Malformed native heap sample row")
    if current is not None or expected_totals is None or raw_totals != expected_totals:
        invalid("Native heap counters are missing, truncated or inconsistent")
    unit = "bytes" if profile_type.endswith("space") else "count"
    parsed = stack_result(
        stacks,
        unit=unit,
        metadata={
            "format": "jemalloc-heap" if je else "gperftools-heap",
            "profile_type": profile_type,
            "symbolization": "raw_program_counters",
            "sampling_adjusted": je,
            "memory_maps": "\n".join(maps)[:32768],
        },
    )
    parsed["sections"] = {
        "heap": {
            "format": parsed["metadata"]["format"],
            "profile_type": profile_type,
            "unit": unit,
            "sample_types": [
                {
                    "type": name,
                    "unit": "bytes" if name.endswith("space") else "count",
                    "total": totals[index],
                }
                for name, index in indices.items()
            ],
            "totals": {"sample_weight": parsed["metadata"]["sample_count"]},
            "classes": [],
            "dominators": [],
            "leak_paths": [],
            "limitations": [
                "分配栈 profile 不包含完整对象引用图，无法计算精确 retained size。",
                "jemalloc 采样校正值是估计值。",
            ],
        }
    }
    return parsed


def analyze_bolt(raw):
    try:
        lines = raw.decode("utf8", errors="strict").splitlines()
    except UnicodeDecodeError:
        invalid("BOLT fdata must be UTF-8")
    no_lbr, branches, functions, memory = False, [], defaultdict(Counter), []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line == "boltedcollection":
            continue
        if line.startswith("no_lbr"):
            no_lbr = True
            continue
        fields = line.split()
        try:
            if no_lbr:
                if len(fields) != 4 or fields[0] not in {"0", "1", "2"}:
                    invalid("Malformed BOLT basic sample row")
                int(fields[2], 16)  # Validate the basic sample offset.
                count = int(fields[3])
                if count < 0:
                    invalid("Negative BOLT sample count")
                functions[fields[1]]["samples"] += count
            elif (
                len(fields) == 8
                and fields[0] in {"0", "1", "2"}
                and fields[3] in {"0", "1", "2"}
            ):
                left, right = int(fields[2], 16), int(fields[5], 16)
                mispredictions, count = int(fields[6]), int(fields[7])
                if not 0 <= mispredictions <= count <= 2**53 - 1:
                    invalid("Invalid BOLT branch counters")
                branches.append(
                    {
                        "from_function": fields[1],
                        "from_offset": hex(left),
                        "to_function": fields[4],
                        "to_offset": hex(right),
                        "count": count,
                        "mispredictions": mispredictions,
                    }
                )
                functions[fields[1]].update(
                    branches=count, mispredictions=mispredictions
                )
                functions[fields[4]]["incoming_branches"] += count
            elif (
                len(fields) == 7
                and fields[0] in {"3", "4", "5"}
                and fields[3] in {"3", "4", "5"}
            ):
                memory.append(
                    {
                        "function": fields[1],
                        "offset": hex(int(fields[2], 16)),
                        "address": fields[4],
                        "address_offset": hex(int(fields[5], 16)),
                        "count": int(fields[6]),
                    }
                )
            else:
                invalid("Unsupported or malformed BOLT fdata record")
        except (ValueError, OverflowError):
            invalid("Invalid numeric BOLT profile field")
        if len(branches) + len(memory) > 200_000:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "BOLT record count exceeds limit"
            )
    if not functions and not memory:
        invalid("BOLT profile has no samples or branches")
    stacks = [
        ([name], value["samples"] if no_lbr else value["branches"])
        for name, value in functions.items()
    ]
    parsed = stack_result(
        stacks,
        unit="samples" if no_lbr else "branches",
        metadata={"format": "bolt-fdata", "lbr": not no_lbr},
    )
    parsed["sections"] = {
        "binary": {
            "format": "bolt_fdata",
            "mode": "basic_samples" if no_lbr else "branch",
            "functions": [
                {"name": name, **value}
                for name, value in sorted(
                    functions.items(),
                    key=lambda item: (-sum(item[1].values()), item[0]),
                )[:200]
            ],
            "branches": sorted(branches, key=lambda row: -row["count"])[:200],
            "memory_accesses": memory[:200],
            "totals": {
                "branches": sum(row["count"] for row in branches),
                "mispredictions": sum(row["mispredictions"] for row in branches),
            },
            "limitations": [
                "分支计数不是函数调用次数；优化后的二进制需要单独回归验证。"
            ],
        }
    }
    return parsed
