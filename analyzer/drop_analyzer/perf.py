"""Native perf script/data: explicit thread identities, module names and loss evidence."""

from collections import Counter
from pathlib import Path
import re
import struct

from .errors import AnalysisError
from .external import run_tool
from .folded import Limits
from .stacks import stack_result


FIELDS = "comm,pid,tid,time,event,ip,sym,dso"
HEADER = re.compile(
    r"^\s*(?P<comm>.+?)\s+(?P<pid>\d{1,10})(?:/(?P<tid>\d{1,10})|\s+(?P<other_tid>\d{1,10}))(?:\s+\[\d+\])?\s+(?P<seconds>\d{1,10})\.(?P<fraction>\d{1,9}):\s+(?P<event>\S+):(?:\s+(?P<frame>.*))?\s*$"
)
FRAME = re.compile(
    r"^\s*(?:0x)?(?P<ip>[a-fA-F0-9]{1,16})\s+(?P<symbol>.+?)\s+\((?P<dso>.*)\)\s*$"
)


def invalid(message):
    raise AnalysisError("INVALID_INPUT", message)


def unknown_loss():
    return {
        "lost_records": None,
        "hardware_lost_samples": None,
        "lost_samples": None,
        "loss_proof": "unknown",
    }


def scan_perf_loss(raw):
    """Scan record headers only; lost records cannot be invented as lost samples."""
    if len(raw) < 56 or raw[:8] not in {b"PERFILE2", b"2ELIFREP"}:
        return unknown_loss()
    endian = "<" if raw[:8] == b"PERFILE2" else ">"
    header_size = struct.unpack_from(endian + "Q", raw, 8)[0]
    offset, size = struct.unpack_from(endian + "QQ", raw, 40)
    if not 56 <= header_size <= offset <= len(raw) or size > len(raw) - offset:
        return unknown_loss()
    end, records, samples, lost, hardware = offset + size, 0, 0, 0, 0
    while offset < end:
        records += 1
        if records > 2_000_000 or end - offset < 8:
            return unknown_loss()
        kind, _, length = struct.unpack_from(endian + "IHH", raw, offset)
        if length < 8 or length > end - offset or kind in {71, 81}:
            return (
                unknown_loss()
            )  # AUXTRACE/COMPRESSED have payloads not covered by this header-only scan.
        if kind == 9:
            samples += 1
        elif kind in {2, 13}:
            if length < (24 if kind == 2 else 16):
                return unknown_loss()
            amount = struct.unpack_from(
                endian + "Q", raw, offset + (16 if kind == 2 else 8)
            )[0]
            if kind == 2:
                lost += amount
            else:
                hardware += amount
            if max(lost, hardware) > 2**53 - 1:
                return unknown_loss()
        offset += length
    return {
        "sample_count": samples,
        "lost_records": lost,
        "hardware_lost_samples": hardware,
        "lost_samples": hardware if lost == 0 else None,
        "loss_proof": "complete_native_perf_record_scan",
    }


def analyze_perf_script(raw, *, collector_perf=None, limits=None):
    limits = limits or Limits()
    if len(raw) > limits.max_input_bytes:
        raise AnalysisError(
            "INPUT_LIMIT_EXCEEDED", "perf script exceeds input byte limit"
        )
    try:
        text = raw.decode("utf8", errors="strict")
    except UnicodeDecodeError:
        invalid("perf script must be valid UTF-8")
    stacks, threads, events = [], {}, Counter()
    self_weights, inclusive_weights = Counter(), Counter()
    current = None
    first_time, last_time = None, None
    lost_records, reported_samples, saw_loss, uncertain_loss = 0, 0, False, False

    def frame(line):
        match = FRAME.fullmatch(line)
        if match is None:
            invalid("Malformed perf script stack frame")
        symbol, dso = match["symbol"], match["dso"] or "[unknown]"
        if symbol in {"[unknown]", "[unknown]+0x0", "0x0"}:
            symbol = "0x" + match["ip"].lower().removeprefix("0x")
        else:
            symbol = re.sub(r"\+0x[0-9a-fA-F]+$", "", symbol)
        return f"{symbol} ({dso})".replace(";", ":")

    def flush():
        nonlocal current, first_time, last_time
        if current is None:
            return
        if not current["frames"]:
            invalid("perf script sample is truncated or missing its instruction")
        identity = current["pid"], current["tid"]
        if identity not in threads:
            if len(threads) >= 4096:
                raise AnalysisError(
                    "INPUT_LIMIT_EXCEEDED", "perf script exceeds thread limit"
                )
            comm = current["comm"]
            threads[identity] = {
                "pid": identity[0],
                "tid": identity[1],
                "comm": comm,
                "root_name": f"{comm.replace(';', ':')} [pid={identity[0]} tid={identity[1]}]",
                "sample_count": 0,
                "first_timestamp_ns": current["time"],
                "last_timestamp_ns": current["time"],
            }
        thread = threads[identity]
        thread["sample_count"] += 1
        thread["first_timestamp_ns"] = min(
            thread["first_timestamp_ns"], current["time"]
        )
        thread["last_timestamp_ns"] = max(thread["last_timestamp_ns"], current["time"])
        stacks.append(([thread["root_name"], *reversed(current["frames"])], 1))
        self_weights[current["frames"][0]] += 1
        inclusive_weights.update(set(current["frames"]))
        if len(stacks) > limits.max_stacks:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "perf script exceeds sample limit"
            )
        events[current["event"]] += 1
        first_time = (
            current["time"] if first_time is None else min(first_time, current["time"])
        )
        last_time = (
            current["time"] if last_time is None else max(last_time, current["time"])
        )
        current = None

    for line in text.splitlines():
        if len(line.encode("utf8")) > limits.max_line_bytes:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "perf script line exceeds byte limit"
            )
        if not line.strip():
            flush()
            continue
        sample = HEADER.fullmatch(line)
        if sample:
            flush()
            pid, tid = int(sample["pid"]), int(sample["tid"] or sample["other_tid"])
            timestamp = int(sample["seconds"]) * 10**9 + int(
                sample["fraction"].ljust(9, "0")
            )
            comm = sample["comm"].strip()
            if (
                max(pid, tid) > 2**31 - 1
                or timestamp > 2**63 - 1
                or not comm
                or len(comm.encode()) > 256
                or len(sample["event"].encode()) > 256
            ):
                invalid("Invalid perf script process identity or timestamp")
            current = {
                "pid": pid,
                "tid": tid,
                "comm": comm,
                "time": timestamp,
                "event": sample["event"],
                "frames": [],
                "inline_frame": tuple(sample["frame"].split())
                if sample["frame"]
                else None,
            }
            if sample["frame"] and sample["frame"].strip():
                current["frames"].append(frame(sample["frame"]))
            continue
        loss = re.search(
            r"\b(PERF_RECORD_LOST_SAMPLES|PERF_RECORD_LOST)\s*:?\s+(\d{1,16})\s*$", line
        )
        if loss:
            flush()
            saw_loss = True
            if loss[1] == "PERF_RECORD_LOST_SAMPLES":
                reported_samples += int(loss[2])
            else:
                lost_records += int(loss[2])
            if max(reported_samples, lost_records) > 2**53 - 1:
                invalid("perf script loss counter exceeds JSON integer range")
            continue
        if "PERF_RECORD_LOST" in line or re.match(
            r"^\s*(?:#\s*)?(?:lost|lost events|events lost)\b", line, re.IGNORECASE
        ):
            flush()
            uncertain_loss = True
            continue
        if line.lstrip().startswith("#"):
            continue
        if current is None:
            invalid("perf script frame has no sample header")
        if current["inline_frame"] is not None:
            duplicate = current["inline_frame"] == tuple(line.split())
            current["inline_frame"] = None
            if duplicate:
                continue
        current["frames"].append(frame(line))
        if len(current["frames"]) >= limits.max_depth:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "perf callchain exceeds stack depth limit"
            )
    flush()
    if not stacks:
        invalid("perf script contains no samples")
    loss = unknown_loss()
    if (
        isinstance(collector_perf, dict)
        and collector_perf.get("loss_proof") == "complete_native_perf_record_scan"
    ):
        values = [
            collector_perf.get(name)
            for name in ("lost_records", "hardware_lost_samples")
        ]
        if all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= 2**53 - 1
            for value in values
        ):
            loss = {
                "lost_records": values[0],
                "hardware_lost_samples": values[1],
                "lost_samples": values[1] if values[0] == 0 else None,
                "loss_proof": "complete_native_perf_record_scan",
            }
    if loss["loss_proof"] == "unknown":
        loss.update(lost_records=lost_records if saw_loss else None)
        status = (
            "partial_script_loss_records"
            if saw_loss or uncertain_loss
            else "not_reported"
        )
    else:
        status = loss["loss_proof"]
        if (
            uncertain_loss
            or lost_records > loss["lost_records"]
            or reported_samples > loss["hardware_lost_samples"]
        ):
            loss.update(
                lost_records=lost_records if saw_loss else None, lost_samples=None
            )
            status = "conflicting_loss_evidence"
    time_range = {
        "start": str(first_time),
        "end": str(last_time),
        "duration": str(last_time - first_time),
    }
    parsed = stack_result(
        stacks,
        limits=limits,
        metadata={
            "format": "perf-script/v1",
            "input_format": "perf_script",
            "thread_information": "explicit_pid_tid",
            "time_range": time_range,
            "duration_ns": last_time - first_time,
            "lost_samples": loss["lost_samples"],
            "lost_samples_status": status,
            "symbolization": "recorded_symbols_or_raw_addresses_without_network_fetch",
        },
    )
    labels = {row["root_name"]: row for row in threads.values()}
    for node in parsed["flamegraph"]["children"]:
        thread = labels[node["name"]]
        node.update(
            node_type="thread",
            pid=thread["pid"],
            tid=thread["tid"],
            comm=thread["comm"],
        )
    function_rows = [
        {
            "name": name,
            "self": self_weights[name],
            "inclusive": weight,
            "self_ratio": self_weights[name] / len(stacks),
            "inclusive_ratio": weight / len(stacks),
        }
        for name, weight in inclusive_weights.items()
    ]
    parsed["top"]["self"] = sorted(
        function_rows, key=lambda row: (-row["self"], row["name"])
    )[:100]
    parsed["top"]["inclusive"] = sorted(
        function_rows, key=lambda row: (-row["inclusive"], row["name"])
    )[:100]
    rows = []
    for _, thread in sorted(threads.items()):
        rows.append(
            {
                **thread,
                "first_timestamp_ns": str(thread["first_timestamp_ns"]),
                "last_timestamp_ns": str(thread["last_timestamp_ns"]),
            }
        )
    parsed["sections"] = {
        "perf": {
            "threads": rows,
            "event_counts": dict(sorted(events.items())),
            "clock": collector_perf.get("clock")
            if isinstance(collector_perf, dict)
            else None,
            "timestamp_unit": "nanoseconds",
            "time_range_ns": time_range,
            "lost_samples": loss["lost_samples"],
            "reported_lost_samples": reported_samples if saw_loss else None,
            "lost_records": loss["lost_records"],
            "loss_status": status,
            "filters": collector_perf.get("filters", {})
            if isinstance(collector_perf, dict)
            else {},
            "limitations": [
                "PERF_RECORD_LOST reports missing records; these cannot all be labeled missing samples.",
                "Absence of a textual loss line does not prove zero loss.",
                "Nanosecond timestamps are decimal strings to retain 64-bit precision in browsers.",
                "Weights count observations across the listed events; they are not measured cycles, instructions or elapsed time.",
            ],
        }
    }
    return parsed


def analyze_perf_data(raw, *, path, work_dir):
    if len(raw) < 56 or raw[:8] not in {b"PERFILE2", b"2ELIFREP"}:
        invalid("Invalid native perf.data header or unsupported format")
    endian = "<" if raw[:8] == b"PERFILE2" else ">"
    header_size = struct.unpack_from(endian + "Q", raw, 8)[0]
    offset, length = struct.unpack_from(endian + "QQ", raw, 40)
    if not 56 <= header_size <= offset <= len(raw) or length > len(raw) - offset:
        invalid("Truncated native perf.data header or data section")
    root = Path(work_dir)
    symbols = root / "isolated-symbols"
    symbols.mkdir(mode=0o700, exist_ok=True)
    script = run_tool(
        [
            "perf",
            "script",
            "--ns",
            "--show-lost-events",
            "--symfs",
            symbols,
            "-F",
            FIELDS,
            "-i",
            path,
        ],
        cwd=root,
        output_name="native-perf.script",
        max_bytes=Limits().max_input_bytes,
        environment={
            "DEBUGINFOD_URLS": "",
            "PERF_CONFIG_NOSYSTEM": "1",
            "PERF_CONFIG_NOGLOBAL": "1",
        },
    )
    parsed = analyze_perf_script(script, collector_perf=scan_perf_loss(raw))
    parsed["metadata"]["input_format"] = "perf_data"
    return parsed
