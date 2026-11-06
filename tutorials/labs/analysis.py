"""Bounded, synthetic teaching exercises; no host sampling or external services.

These implementations explain mechanisms. Production parsers, repositories and
browser decoders remain authoritative and have their own contracts and limits.
"""

from __future__ import annotations

import codecs
import copy
import json
import re
from collections import deque

from .helpers import check


MAX_SAFE_WEIGHT = 2**53 - 1
MAX_INT64 = 2**63 - 1


def parse_folded(raw: bytes) -> dict:
    """Parse at most 32 KiB / 128 lines / depth 16 / 256 nodes of folded data."""
    if not isinstance(raw, bytes) or len(raw) > 32 * 1024:
        raise ValueError("INPUT_LIMIT")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("INVALID_UTF8") from error
    root = {"name": "all", "value": 0, "self": 0, "children": {}}
    functions: dict[str, dict] = {}
    nodes, lines = 1, 0
    for original in text.split("\n"):
        line = original.removesuffix("\r")
        if not line.strip():
            continue
        lines += 1
        if lines > 128 or len(line.encode("utf-8")) > 4096:
            raise ValueError("INPUT_LIMIT")
        path, separator, weight_text = line.rpartition(" ")
        if not separator or not re.fullmatch(r"[0-9]{1,16}", weight_text):
            raise ValueError("INVALID_WEIGHT")
        weight = int(weight_text)
        if weight <= 0 or root["value"] + weight > MAX_SAFE_WEIGHT:
            raise ValueError("INVALID_WEIGHT")
        frames = path.split(";")
        if len(frames) > 16:
            raise ValueError("DEPTH_LIMIT")
        if any(
            not frame.strip()
            or len(frame.encode("utf-8")) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in frame)
            for frame in frames
        ):
            raise ValueError("INVALID_FRAME")
        root["value"] += weight
        parent = root
        for frame in frames:
            if frame not in parent["children"]:
                nodes += 1
                if nodes > 256:
                    raise ValueError("NODE_LIMIT")
                parent["children"][frame] = {
                    "name": frame, "value": 0, "self": 0, "children": {}
                }
            parent = parent["children"][frame]
            parent["value"] += weight
        parent["self"] += weight
        for frame in set(frames):
            row = functions.setdefault(frame, {"name": frame, "self": 0, "inclusive": 0})
            row["inclusive"] += weight
        functions[frames[-1]]["self"] += weight
    if not lines:
        raise ValueError("EMPTY_INPUT")

    def freeze(node: dict) -> dict:
        return {
            **node,
            "children": [freeze(node["children"][name]) for name in sorted(node["children"])],
        }

    return {
        "tree": freeze(root),
        "functions": sorted(functions.values(), key=lambda row: (-row["self"], row["name"])),
        "total_weight": root["value"],
        "stack_count": lines,
    }


def heap_dominators(objects: dict[str, int], edges: dict[str, list[str]], roots: list[str]) -> dict:
    """Small-graph teaching algorithm using dominator-set intersection.

    At most 32 objects, 128 strong edges and 32 rounds. Weights are synthetic
    payload bytes. Production HPROF uses an indexed parser and an idom algorithm.
    """
    if not isinstance(objects, dict) or not isinstance(edges, dict) or not isinstance(roots, list):
        raise ValueError("INVALID_GRAPH")
    if not 1 <= len(objects) <= 32 or "@root" in objects:
        raise ValueError("GRAPH_LIMIT")
    if any(not isinstance(name, str) or not name or len(name) > 64 for name in objects):
        raise ValueError("INVALID_OBJECT")
    if any(type(weight) is not int or not 0 <= weight <= 1_000_000 for weight in objects.values()):
        raise ValueError("INVALID_WEIGHT")
    if not roots or len(roots) > 32 or any(not isinstance(root, str) or root not in objects for root in roots):
        raise ValueError("INVALID_ROOT")
    if any(source not in objects or not isinstance(targets, list) for source, targets in edges.items()):
        raise ValueError("INVALID_EDGE")
    if sum(len(targets) for targets in edges.values()) > 128:
        raise ValueError("GRAPH_LIMIT")
    if any(not isinstance(target, str) or target not in objects for targets in edges.values() for target in targets):
        raise ValueError("INVALID_EDGE")
    graph = {name: list(dict.fromkeys(edges.get(name, []))) for name in objects}
    graph["@root"] = list(dict.fromkeys(roots))
    reachable = {"@root"}
    pending = deque(["@root"])
    while pending:
        source = pending.popleft()
        for target in graph[source]:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    predecessors = {name: set() for name in reachable}
    for source in reachable:
        for target in graph[source]:
            predecessors[target].add(source)
    dominators = {name: set(reachable) for name in reachable}
    dominators["@root"] = {"@root"}
    for iteration in range(32):
        changed = False
        for name in sorted(reachable - {"@root"}):
            incoming = [dominators[parent] for parent in sorted(predecessors[name])]
            updated = {name} | set.intersection(*incoming)
            if updated != dominators[name]:
                dominators[name] = updated
                changed = True
        if not changed:
            break
    else:
        raise ValueError("DOMINATOR_LIMIT")
    immediate: dict[str, str] = {}
    for name in sorted(reachable - {"@root"}):
        strict = dominators[name] - {name}
        candidates = [candidate for candidate in strict if strict - {candidate} <= dominators[candidate]]
        if len(candidates) != 1:
            raise ValueError("INVALID_DOMINATOR")
        immediate[name] = candidates[0]
    retained = {
        owner: sum(objects[target] for target in objects if target in reachable and owner in dominators[target])
        for owner in sorted(reachable)
    }
    return {
        "reachable": sorted(reachable - {"@root"}),
        "unreachable": sorted(set(objects) - reachable),
        "idom": immediate,
        "retained": retained,
        "iterations": iteration + 1,
    }


class SSEDecoder:
    """Incremental UTF-8 framing with a bounded frame and committed cursor."""

    def __init__(self, cursor: str = ""):
        if cursor and not self.valid_cursor(cursor):
            raise ValueError("INVALID_CURSOR")
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.line = ""
        self.after_cr = False
        self.data: list[str] = []
        self.event_type = "message"
        self.pending_id: str | None = None
        self.last_event_id = cursor
        self.frame_bytes = 0
        self.total_bytes = 0
        self.closed = False

    @staticmethod
    def valid_cursor(cursor: str) -> bool:
        return bool(re.fullmatch(r"[1-9][0-9]{0,18}", cursor)) and int(cursor) <= MAX_INT64

    def push(self, chunk: bytes) -> list[dict]:
        if self.closed or not isinstance(chunk, bytes):
            raise ValueError("DECODER_CLOSED")
        self.total_bytes += len(chunk)
        if self.total_bytes > 32 * 1024:
            raise ValueError("STREAM_LIMIT")
        try:
            return self._consume(self.decoder.decode(chunk, final=False))
        except UnicodeDecodeError as error:
            raise ValueError("INVALID_UTF8") from error

    def finish(self) -> list[dict]:
        if self.closed:
            raise ValueError("DECODER_CLOSED")
        self.closed = True
        try:
            events = self._consume(self.decoder.decode(b"", final=True))
        except UnicodeDecodeError as error:
            raise ValueError("INVALID_UTF8") from error
        if self.after_cr:
            self.after_cr = False
            events.extend(self._line())
        # No blank line means no completed event; do not commit pending_id.
        return events

    def _consume(self, text: str) -> list[dict]:
        events: list[dict] = []
        for char in text:
            if self.after_cr:
                self.after_cr = False
                if char == "\n":
                    self._count(1)
                    events.extend(self._line())
                    continue
                events.extend(self._line())
            self._count(len(char.encode("utf-8")))
            if char == "\r":
                self.after_cr = True
            elif char == "\n":
                events.extend(self._line())
            else:
                self.line += char
        return events

    def _count(self, size: int):
        self.frame_bytes += size
        if self.frame_bytes > 4096:
            raise ValueError("FRAME_LIMIT")

    def _line(self) -> list[dict]:
        line, self.line = self.line, ""
        if not line:
            if self.pending_id is not None:
                if not self.valid_cursor(self.pending_id):
                    raise ValueError("INVALID_CURSOR")
                self.last_event_id = self.pending_id
            events = []
            if self.data:
                events.append({"type": self.event_type or "message", "data": "\n".join(self.data), "id": self.last_event_id})
            self.data, self.event_type, self.pending_id = [], "message", None
            self.frame_bytes = 0
            return events
        if line.startswith(":"):
            return []
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            self.data.append(value)
        elif field == "event":
            self.event_type = value
        elif field == "id" and "\0" not in value:
            self.pending_id = value
        return []


def decode_lesson_json(raw: bytes) -> dict:
    """Decode at most 8 KiB, rejecting duplicate keys and non-JSON constants."""
    if not isinstance(raw, bytes) or len(raw) > 8192:
        raise ValueError("INPUT_LIMIT")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_KEY")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("INVALID_JSON")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("INVALID_JSON") from error
    if not isinstance(value, dict):
        raise ValueError("INVALID_ENVELOPE")
    return value


def validate_lesson_contract(payload: dict, *, versions: tuple[int, ...] = (1, 2)) -> dict:
    """Validate a proposed teaching-only type; it is not the production catalog."""
    if not isinstance(payload, dict):
        raise ValueError("INVALID_ENVELOPE")
    version = payload.get("schema_version")
    if type(version) is not int or version not in versions:
        raise ValueError("UNSUPPORTED_VERSION")
    if len(payload) not in (4, 5):
        raise ValueError("INVALID_FIELDS")
    fields = {"schema_version", "kind", "unit", "weight"}
    if version == 2:
        fields.add("provenance")
    if set(payload) != fields or payload["kind"] != "LESSON_SELF_CPU_V2":
        raise ValueError("INVALID_FIELDS")
    if payload["unit"] != "cpu_ticks":
        raise ValueError("UNIT_MISMATCH")
    if type(payload["weight"]) is not int or not 0 <= payload["weight"] <= MAX_SAFE_WEIGHT:
        raise ValueError("INVALID_WEIGHT")
    provenance = {"source": "unknown"}
    if version == 2:
        value = payload["provenance"]
        if not isinstance(value, dict) or len(value) != 1 or set(value) != {"source"} or not isinstance(value["source"], str) or value["source"] not in {"synthetic", "import", "controlled_self"}:
            raise ValueError("INVALID_PROVENANCE")
        provenance = dict(value)
    return {**payload, "provenance": provenance}


def _rejects(callable_, expected: str) -> bool:
    try:
        callable_()
    except ValueError as error:
        return str(error) == expected
    return False


def folded_tree_case() -> dict:
    parsed = parse_folded(b"main;render;decode 6\nmain;render 3\nmain;idle 1\n")
    recursive = parse_folded(b"main;walk;walk 4\nmain;walk 6\n")
    walk = next(row for row in recursive["functions"] if row["name"] == "walk")
    branch = recursive["tree"]["children"][0]["children"][0]
    naive_inclusive = branch["value"] + branch["children"][0]["value"]
    checks = [
        check(parsed["total_weight"] == 10, "three synthetic paths have total weight 10"),
        check(parsed["functions"][0] == {"name": "decode", "self": 6, "inclusive": 6}, "decode has the largest self weight"),
        check(walk["inclusive"] == 10 and walk["self"] == 10, "recursive frames are not double-counted in function inclusive"),
        check(naive_inclusive == 14 and naive_inclusive != walk["inclusive"], "negative control exposes naive inclusive double counting"),
        check(_rejects(lambda: parse_folded(b"main 0\n"), "INVALID_WEIGHT"), "reject zero weight"),
        check(_rejects(lambda: parse_folded(b"main;;decode 1\n"), "INVALID_FRAME"), "reject empty frame"),
        check(_rejects(lambda: parse_folded(b"\xff 1\n"), "INVALID_UTF8"), "reject malformed UTF-8"),
        check(_rejects(lambda: parse_folded(b";".join([b"f"] * 17) + b" 1\n"), "DEPTH_LIMIT"), "reject excessive depth"),
    ]
    return {"summary": "Synthetic folded input becomes a bounded tree; recursive inclusive stays 10 rather than 14.", "checks": checks, "observations": [{"source": "synthetic", "total_weight": 10, "tree": parsed["tree"], "functions": parsed["functions"], "recursive_walk": walk, "naive_recursive_inclusive": naive_inclusive}]}


def heap_dominators_case() -> dict:
    objects = {"cache": 8, "other": 8, "shared": 40, "owned": 24, "cycle_a": 4, "cycle_b": 4, "orphan": 16}
    edges = {"cache": ["shared", "owned"], "other": ["shared"], "owned": ["cycle_a"], "cycle_a": ["cycle_b"], "cycle_b": ["cycle_a"]}
    result = heap_dominators(objects, edges, ["cache", "other"])
    exclusive_edges = copy.deepcopy(edges)
    exclusive_edges["other"] = []
    exclusive = heap_dominators(objects, exclusive_edges, ["cache", "other"])
    checks = [
        check(result["unreachable"] == ["orphan"], "unrooted object remains unreachable"),
        check(result["idom"]["shared"] == "@root", "shared object is not exclusively retained by cache"),
        check(result["retained"]["cache"] == 40, "cache retains own 8 plus owned 24 and cycle 8"),
        check(result["retained"]["@root"] == 88, "reachable payload bytes exclude orphan 16"),
        check(result["idom"]["cycle_b"] == "cycle_a", "cycle traversal terminates with a valid immediate dominator"),
        check(exclusive["retained"]["cache"] == 80, "removing the alternate strong edge changes retention by 40"),
        check(sum(result["retained"].values()) > result["retained"]["@root"], "negative control exposes overlapping retained sums"),
        check(_rejects(lambda: heap_dominators(objects, {"cache": ["missing"]}, ["cache"]), "INVALID_EDGE"), "reject dangling references in teaching input"),
    ]
    return {"summary": "Synthetic strong-reference graph separates reachability, shallow bytes and retained bytes.", "checks": checks, "observations": [{"source": "synthetic", "size_basis": "assigned_payload_bytes_not_VM_heap_size", "objects": objects, "edges": edges, **result, "cache_without_alternate_edge": exclusive["retained"]["cache"]}]}


def sse_frames_case() -> dict:
    wire = 'id: 9007199254740993\r\nevent: task_status\r\ndata: {"stage":"采集完成"}\r\n\r\n'.encode("utf-8")
    cut = wire.index("采".encode("utf-8")) + 1
    decoder = SSEDecoder()
    first = decoder.push(wire[:cut])
    cursor_before = decoder.last_event_id
    events = decoder.push(wire[cut:-1]) + decoder.push(wire[-1:]) + decoder.finish()
    truncated = SSEDecoder("9007199254740993")
    half_events = truncated.push(b"id: 9007199254740994\ndata: unfinished") + truncated.finish()
    left, right = 9007199254740992, 9007199254740993
    bad = SSEDecoder()
    invalid = SSEDecoder()
    checks = [
        check(first == [] and cursor_before == "", "split multibyte text cannot prematurely commit a frame"),
        check(len(events) == 1 and json.loads(events[0]["data"])["stage"] == "采集完成", "incremental decoder reconstructs the Chinese event"),
        check(decoder.last_event_id == "9007199254740993", "cursor preserves the exact int64 decimal string"),
        check(half_events == [] and truncated.last_event_id == "9007199254740993", "truncated event does not advance the reconnect cursor"),
        check(left != right and float(left) == float(right), "negative control demonstrates float cursor collapse"),
        check(_rejects(lambda: bad.push(b"data: \xff\n\n"), "INVALID_UTF8"), "reject malformed UTF-8"),
        check(_rejects(lambda: invalid.push(b"id: 9223372036854775808\ndata: x\n\n"), "INVALID_CURSOR"), "reject cursors beyond signed int64"),
        check(_rejects(lambda: SSEDecoder().push(b":" + b"x" * 4096), "FRAME_LIMIT"), "heartbeat comments count toward frame bounds"),
    ]
    return {"summary": "Synthetic split SSE frames retain exact cursors and discard incomplete events.", "checks": checks, "observations": [{"source": "synthetic", "events": events, "committed_cursor": decoder.last_event_id, "truncated_cursor": truncated.last_event_id, "float_collision": [left, right]}]}


def contract_evolution_case() -> dict:
    legacy = {"schema_version": 1, "kind": "LESSON_SELF_CPU_V2", "unit": "cpu_ticks", "weight": 0}
    current = {**legacy, "schema_version": 2, "weight": 37, "provenance": {"source": "synthetic"}}
    normalized = validate_lesson_contract(legacy)
    accepted = validate_lesson_contract(current)
    checks = [
        check(normalized["provenance"]["source"] == "unknown", "new reader does not invent provenance for legacy input"),
        check(accepted["provenance"]["source"] == "synthetic" and accepted["weight"] == 37, "new fixture retains its synthetic source and explicit unit"),
        check(_rejects(lambda: validate_lesson_contract(current, versions=(1,)), "UNSUPPORTED_VERSION"), "old reader explicitly rejects new wire version"),
        check(_rejects(lambda: validate_lesson_contract({**current, "unit": "samples"}), "UNIT_MISMATCH"), "reject compatible-looking tree with wrong measurement unit"),
        check(_rejects(lambda: validate_lesson_contract({**current, "weight": True}), "INVALID_WEIGHT"), "reject bool masquerading as integer"),
        check(_rejects(lambda: validate_lesson_contract({**current, "command": "anything"}), "INVALID_FIELDS"), "reject unknown executable command field"),
        check(_rejects(lambda: validate_lesson_contract({**current, "provenance": {"source": "guessed"}}), "INVALID_PROVENANCE"), "reject invented provenance category"),
        check(_rejects(lambda: decode_lesson_json(b'{"weight":1,"weight":2}'), "DUPLICATE_KEY"), "reject duplicate JSON keys before dictionary normalization"),
        check(_rejects(lambda: decode_lesson_json(b'{"weight":NaN}'), "INVALID_JSON"), "reject non-JSON numeric constants"),
        check(legacy["weight"] == 0 and normalized["weight"] == 0, "zero measured counter is representable and not fabricated into a sample"),
    ]
    return {"summary": "Teaching-only LESSON_SELF_CPU_V2 demonstrates explicit versioning, unknown provenance and rejected negative fixtures.", "checks": checks, "observations": [{"source": "synthetic", "production_catalog_changed": False, "legacy_normalized": normalized, "current_accepted": accepted, "note": "37 is an assigned fixture weight, not a measured host value."}]}


CASES = {
    "folded-tree": folded_tree_case,
    "heap-dominators": heap_dominators_case,
    "sse-frames": sse_frames_case,
    "contract-evolution": contract_evolution_case,
}
