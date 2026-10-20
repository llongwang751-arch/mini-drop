"""将 HotSpot HPROF 转为对象图，使用事务化、可恢复的 SQLite 索引。

保留大小依据序列化字段和数组载荷计算；HPROF 不包含 VM 压缩引用、对象头
及对齐信息，因此结果明确说明计量口径，不伪造精确的 VM 对象占用。
"""

import hashlib
import json
from pathlib import Path
import sqlite3
from collections import Counter, defaultdict, deque

from .errors import AnalysisError
from .stacks import stack_result

TYPES = {2: 0, 4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
PRIMITIVES = {
    4: "boolean[]",
    5: "char[]",
    6: "float[]",
    7: "double[]",
    8: "byte[]",
    9: "short[]",
    10: "int[]",
    11: "long[]",
}
ROOTS = {
    255: ("unknown", 0),
    1: ("jni_global", "id"),
    2: ("jni_local", 8),
    3: ("java_frame", 8),
    4: ("native_stack", 4),
    5: ("sticky_class", 0),
    6: ("thread_block", 4),
    7: ("monitor", 0),
    8: ("thread_object", 8),
    0x89: ("interned_string", 0),
    0x8A: ("finalizing", 0),
    0x8B: ("debugger", 0),
    0x8C: ("reference_cleanup", 0),
    0x8D: ("vm_internal", 0),
    0x8E: ("jni_monitor", 8),
    0x90: ("unreachable", 0),
}

INDEX_SCHEMA_VERSION = "1"
INDEX_SCHEMA = """
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS strings(id TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS names(id TEXT PRIMARY KEY,name_id TEXT);
    CREATE TABLE IF NOT EXISTS classes(id TEXT PRIMARY KEY,super TEXT,fields TEXT,size INTEGER);
    CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,class TEXT,size INTEGER,payload BLOB,kind TEXT);
    CREATE TABLE IF NOT EXISTS edges(src TEXT,dst TEXT);
    CREATE TABLE IF NOT EXISTS roots(id TEXT,kind TEXT);
"""


def open_index(index_path):
    def connect():
        connection = sqlite3.connect(index_path, timeout=5)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA cache_size=-8192")
            connection.execute("PRAGMA max_page_count=65536")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Index consistency check failed")
            connection.executescript(INDEX_SCHEMA)
            return connection
        except sqlite3.DatabaseError:
            connection.close()
            raise

    if index_path != ":memory:" and Path(index_path).is_symlink():
        raise AnalysisError("INVALID_INDEX", "HPROF cache must not be a symbolic link")
    try:
        return connect(), False
    except sqlite3.DatabaseError:
        if index_path == ":memory:":
            raise
        database = Path(index_path)
        for suffix in ("", "-wal", "-shm"):
            candidate = database.with_name(database.name + suffix)
            if candidate.is_symlink():
                raise AnalysisError(
                    "INVALID_INDEX", "HPROF cache sidecar must not be a symbolic link"
                )
            candidate.unlink(missing_ok=True)
        return connect(), True


def invalid(message):
    raise AnalysisError("INVALID_HPROF", message)


class Cursor:
    def __init__(self, raw, offset=0, end=None, id_size=8):
        self.raw, self.offset, self.end, self.id_size = (
            raw,
            offset,
            len(raw) if end is None else end,
            id_size,
        )

    def take(self, size):
        end = self.offset + size
        if size < 0 or end > self.end:
            invalid("Truncated HPROF record")
        value, self.offset = self.raw[self.offset : end], end
        return value

    def number(self, size):
        return int.from_bytes(self.take(size), "big")

    def id(self):
        return self.number(self.id_size)

    def value(self, kind):
        if kind not in TYPES:
            invalid("Unknown HPROF field type")
        return self.number(self.id_size if kind == 2 else TYPES[kind])


def analyze_hprof(raw, *, work_dir=None, limits=None, on_progress=None):
    notify = on_progress or (lambda stage: None)
    notify("parse")
    limits = {
        "max_input_bytes": 32 * 1024 * 1024,
        "max_objects": 100_000,
        "max_edges": 1_000_000,
        "max_strings": 200_000,
        **(limits or {}),
    }
    if len(raw) > limits["max_input_bytes"]:
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "HPROF input exceeds byte limit")
    terminator = raw.find(b"\0", 0, 40)
    if terminator < 0 or raw[:terminator] not in {
        b"JAVA PROFILE 1.0.1",
        b"JAVA PROFILE 1.0.2",
        b"JAVA PROFILE 1.0.3",
    }:
        invalid("Invalid HPROF header")
    header = Cursor(raw, terminator + 1)
    identifier_size = header.number(4)
    if identifier_size not in {4, 8}:
        invalid("HPROF identifier width must be 4 or 8")
    timestamp_ms = header.number(8)
    digest = hashlib.sha256(raw).hexdigest()
    index_path = ":memory:"
    if work_dir:
        directory = Path(work_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        index_path = str(directory / f"hprof-{digest}.sqlite3")
    notify("index")
    connection, index_rebuilt = open_index(index_path)
    try:
        progress = dict(connection.execute("SELECT key,value FROM meta"))
        if progress.get("digest", digest) != digest:
            invalid("HPROF index input digest mismatch")
        if progress.get(
            "index_schema_version", INDEX_SCHEMA_VERSION
        ) != INDEX_SCHEMA_VERSION or not header.offset <= int(
            progress.get("offset", header.offset)
        ) <= len(raw):
            for table in (
                "meta",
                "strings",
                "names",
                "classes",
                "objects",
                "edges",
                "roots",
            ):
                connection.execute("DELETE FROM " + table)
            connection.commit()
            progress, index_rebuilt = {}, True
        cursor = Cursor(
            raw, int(progress.get("offset", header.offset)), id_size=identifier_size
        )
        segment_end = int(progress.get("segment_end", 0))
        count = connection.execute("SELECT count(*) FROM objects").fetchone()[0]
        string_count = connection.execute("SELECT count(*) FROM strings").fetchone()[0]
        resumed = cursor.offset > header.offset
        segmented = progress.get("segmented") == "1"
        heap_end_seen = progress.get("heap_end_seen") == "1"

        def checkpoint():
            connection.executemany(
                "INSERT OR REPLACE INTO meta VALUES(?,?)",
                [
                    ("digest", digest),
                    ("index_schema_version", INDEX_SCHEMA_VERSION),
                    ("offset", str(cursor.offset)),
                    ("segment_end", str(segment_end)),
                    ("segmented", "1" if segmented else "0"),
                    ("heap_end_seen", "1" if heap_end_seen else "0"),
                ],
            )
            connection.commit()

        def object_row(identifier, cls, size, payload, kind):
            nonlocal count
            count += 1
            if count > limits["max_objects"]:
                raise AnalysisError(
                    "INPUT_LIMIT_EXCEEDED", "HPROF object count exceeds limit"
                )
            try:
                connection.execute(
                    "INSERT INTO objects VALUES(?,?,?,?,?)",
                    (str(identifier), str(cls), size, payload, kind),
                )
            except sqlite3.IntegrityError:
                invalid("Duplicate HPROF object identifier")

        while cursor.offset < len(raw):
            if cursor.offset >= segment_end:
                tag = cursor.number(1)
                cursor.take(4)
                length = cursor.number(4)
                record_end = cursor.offset + length
                if record_end > len(raw):
                    invalid("HPROF record length exceeds input")
                if tag == 1:
                    identifier = cursor.id()
                    text = cursor.take(record_end - cursor.offset).decode(
                        "utf8", errors="replace"
                    )
                    if len(text) > 16384:
                        raise AnalysisError(
                            "INPUT_LIMIT_EXCEEDED", "HPROF string exceeds limit"
                        )
                    connection.execute(
                        "INSERT OR REPLACE INTO strings VALUES(?,?)",
                        (str(identifier), text),
                    )
                    string_count += 1
                    if string_count > limits["max_strings"]:
                        raise AnalysisError(
                            "INPUT_LIMIT_EXCEEDED", "HPROF string table exceeds limit"
                        )
                elif tag == 2:
                    cursor.take(4)
                    cls = cursor.id()
                    cursor.take(4)
                    name = cursor.id()
                    connection.execute(
                        "INSERT OR REPLACE INTO names VALUES(?,?)",
                        (str(cls), str(name)),
                    )
                elif tag in {0x0C, 0x1C}:
                    if tag == 0x1C:
                        segmented = True
                    segment_end = record_end
                    continue
                elif tag == 0x2C:
                    if length != 0:
                        invalid("HPROF heap end record must be empty")
                    heap_end_seen = True
                else:
                    cursor.take(length)
                if cursor.offset != record_end:
                    invalid("Unexpected HPROF record length")
                if string_count % 1000 == 0:
                    checkpoint()
                continue
            # 堆子记录可能位于大 segment 内；只在精确的子记录边界提交检查点，
            # SIGKILL 后才能在 segment 内安全继续，而不把半条记录当完整数据。
            tag = cursor.number(1)
            previous_end, cursor.end = cursor.end, segment_end
            if tag in ROOTS:
                identifier = cursor.id()
                kind, extra = ROOTS[tag]
                cursor.take(identifier_size if extra == "id" else extra)
                if kind != "unreachable":
                    connection.execute(
                        "INSERT INTO roots VALUES(?,?)", (str(identifier), kind)
                    )
            elif tag == 0x20:
                identifier = cursor.id()
                cursor.take(4)
                superclass = cursor.id()
                references = [cursor.id() for _ in range(5)]
                size = cursor.number(4)
                for _ in range(cursor.number(2)):
                    cursor.take(2)
                    kind = cursor.number(1)
                    value = cursor.value(kind)
                    if kind == 2:
                        references.append(value)
                for _ in range(cursor.number(2)):
                    cursor.id()
                    kind = cursor.number(1)
                    value = cursor.value(kind)
                    if kind == 2:
                        references.append(value)
                fields = [
                    (cursor.id(), cursor.number(1)) for _ in range(cursor.number(2))
                ]
                if any(kind not in TYPES for _, kind in fields):
                    invalid("Invalid HPROF instance field descriptor")
                connection.execute(
                    "INSERT INTO classes VALUES(?,?,?,?)",
                    (str(identifier), str(superclass), json.dumps(fields), size),
                )
                object_row(identifier, 0, 0, b"", "class")
                references.append(superclass)
                connection.executemany(
                    "INSERT INTO edges VALUES(?,?)",
                    [(str(identifier), str(value)) for value in references if value],
                )
            elif tag == 0x21:
                identifier = cursor.id()
                cursor.take(4)
                cls = cursor.id()
                size = cursor.number(4)
                object_row(identifier, cls, size, cursor.take(size), "instance")
            elif tag == 0x22:
                identifier = cursor.id()
                cursor.take(4)
                length, cls = cursor.number(4), cursor.id()
                payload = cursor.take(length * identifier_size)
                object_row(identifier, cls, len(payload), payload, "array")
            elif tag == 0x23:
                identifier = cursor.id()
                cursor.take(4)
                length, kind = cursor.number(4), cursor.number(1)
                if kind not in PRIMITIVES:
                    invalid("Invalid primitive array type")
                payload_size = length * TYPES[kind]
                cursor.take(payload_size)
                object_row(identifier, -kind, payload_size, b"", "primitive")
            elif tag == 0xFE:  # Android heap dump info 记录不定义对象引用。
                cursor.take(4 + identifier_size)
            else:
                invalid(f"Unsupported HPROF heap sub-record 0x{tag:02x}")
            cursor.end = previous_end
            if count % 1000 == 0:
                checkpoint()
        if not count or (segmented and not heap_end_seen):
            invalid("HPROF contains no objects or its segmented dump is incomplete")
        checkpoint()
        names = {
            identifier: name.replace("/", ".")
            for identifier, name in connection.execute(
                "SELECT n.id,s.value FROM names n JOIN strings s ON s.id=n.name_id"
            )
        }
        classes = {
            identifier: (superclass, json.loads(fields), size)
            for identifier, superclass, fields, size in connection.execute(
                "SELECT * FROM classes"
            )
        }
        field_names = dict(connection.execute("SELECT id,value FROM strings"))
        layouts = {}

        def layout(identifier):
            if identifier in layouts:
                return layouts[identifier]
            result, seen = [], set()
            while identifier != "0":
                if identifier in seen or identifier not in classes:
                    invalid("HPROF class hierarchy has a cycle or missing superclass")
                seen.add(identifier)
                superclass, fields, _ = classes[identifier]
                result.extend(
                    (field, kind, names.get(identifier, "")) for field, kind in fields
                )
                identifier = superclass
            return result

        if progress.get("references_complete") != "1":
            connection.execute(
                "DELETE FROM edges WHERE src IN (SELECT id FROM objects WHERE kind IN ('array','instance'))"
            )
            edge_count = connection.execute("SELECT count(*) FROM edges").fetchone()[0]
            for identifier, cls, payload, kind in connection.execute(
                "SELECT id,class,payload,kind FROM objects WHERE kind IN ('array','instance')"
            ):
                data = Cursor(payload, id_size=identifier_size)
                refs = [int(cls)] if cls != "0" else []
                if kind == "array":
                    refs.extend(
                        data.id() for _ in range(len(payload) // identifier_size)
                    )
                else:
                    if cls not in layouts:
                        layouts[cls] = layout(cls)
                    for field, field_kind, declaring in layouts[cls]:
                        value = data.value(field_kind)
                        # Reference.referent 属于弱、软或虚引用，不作为强保留边；
                        # 实例的其他字段仍按强引用处理。
                        weak = (
                            declaring == "java.lang.ref.Reference"
                            and field_names.get(str(field)) == "referent"
                        )
                        if field_kind == 2 and not weak:
                            refs.append(value)
                    if data.offset != len(payload):
                        invalid("HPROF instance payload mismatches class layout")
                edges = [(identifier, str(value)) for value in refs if value]
                edge_count += len(edges)
                if edge_count > limits["max_edges"]:
                    raise AnalysisError(
                        "INPUT_LIMIT_EXCEEDED",
                        "HPROF reference edge count exceeds limit",
                    )
                connection.executemany("INSERT INTO edges VALUES(?,?)", edges)
            connection.execute(
                "INSERT OR REPLACE INTO meta VALUES('references_complete','1')"
            )
            connection.commit()
        notify("graph")
        objects = {
            identifier: (cls, size, kind)
            for identifier, cls, size, kind in connection.execute(
                "SELECT id,class,size,kind FROM objects"
            )
        }
        graph, predecessors = defaultdict(list), defaultdict(list)
        for source, target in connection.execute("SELECT src,dst FROM edges"):
            if target in objects:
                graph[source].append(target)
                predecessors[target].append(source)
        root_kinds = {}
        for identifier, kind in connection.execute("SELECT * FROM roots"):
            if identifier in objects:
                root_kinds[identifier] = kind
        graph["0"] = list(root_kinds)
        for identifier in graph["0"]:
            predecessors[identifier].append("0")
        notify("dominator")
        # 采用迭代 DFS 与 Cooper/Harvey/Kennedy 直接支配节点算法，
        # 避免递归遍历栈溢出及平方级支配集合存储。
        seen, postorder = {"0"}, []
        traversal = [("0", iter(graph["0"]))]
        while traversal:
            node, iterator = traversal[-1]
            child = next(iterator, None)
            if child is None:
                postorder.append(node)
                traversal.pop()
            elif child not in seen:
                seen.add(child)
                traversal.append((child, iter(graph[child])))
        order = list(reversed(postorder))
        rank, dominator = {node: index for index, node in enumerate(order)}, {"0": "0"}
        steps = 0
        for iteration in range(256):
            changed = False
            for node in order[1:]:
                incoming = [value for value in predecessors[node] if value in dominator]
                if not incoming:
                    continue
                candidate = incoming[0]
                for other in incoming[1:]:
                    while candidate != other:
                        steps += 1
                        if steps > 20_000_000:
                            raise AnalysisError(
                                "INPUT_LIMIT_EXCEEDED",
                                "HPROF dominator work exceeds limit",
                            )
                        if rank[candidate] > rank[other]:
                            candidate = dominator[candidate]
                        else:
                            other = dominator[other]
                if dominator.get(node) != candidate:
                    dominator[node], changed = candidate, True
            if not changed:
                break
        else:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "HPROF dominator convergence exceeds limit"
            )
        retained = {identifier: value[1] for identifier, value in objects.items()}
        retained["0"] = 0
        for node in reversed(order[1:]):
            retained[dominator[node]] += retained[node]

        def class_name(identifier):
            cls, _, kind = objects[identifier]
            if kind == "class":
                return "java.lang.Class<" + names.get(identifier, "[unknown]") + ">"
            if kind == "primitive":
                return PRIMITIVES.get(-int(cls), "[primitive]")
            return names.get(cls, "[unknown-class]")

        histogram = defaultdict(Counter)
        for identifier, (_, size, _) in objects.items():
            histogram[class_name(identifier)].update(instances=1, shallow_bytes=size)
        # 按类聚合的 retained 大小不能跨类相加；同类嵌套支配对象只计算一次。
        for identifier in order[1:]:
            cls, ancestor = class_name(identifier), dominator[identifier]
            while ancestor != "0" and class_name(ancestor) != cls:
                steps += 1
                if steps > 20_000_000:
                    raise AnalysisError(
                        "INPUT_LIMIT_EXCEEDED",
                        "HPROF class retention aggregation exceeds work limit",
                    )
                ancestor = dominator[ancestor]
            if ancestor == "0":
                histogram[cls]["retained_bytes"] += retained[identifier]
        top = sorted(
            (
                identifier
                for identifier in order[1:]
                if objects[identifier][2] != "class"
            ),
            key=lambda identifier: (-retained[identifier], identifier),
        )[:100]

        def node_row(identifier):
            return {
                "object_id": hex(int(identifier)),
                "class_name": class_name(identifier),
                "shallow_bytes": objects[identifier][1],
                "retained_bytes": retained[identifier],
            }

        # 最短 GC root 路径单独用 BFS 求解；DFS 前驱虽然也是有效路径，
        # 却可能掩盖更近的根节点。
        targets = set(top[:20])
        shortest, queue = {"0": None}, deque(["0"])
        while queue and targets:
            current = queue.popleft()
            targets.discard(current)
            for child in graph[current]:
                if child not in shortest:
                    shortest[child] = current
                    queue.append(child)
        paths = []
        for identifier in top[:20]:
            path, cursor_id = [], identifier
            while cursor_id not in {None, "0"} and len(path) < 128:
                path.append(
                    {
                        "object_id": hex(int(cursor_id)),
                        "class_name": class_name(cursor_id),
                        **(
                            {"root_kind": root_kinds[cursor_id]}
                            if cursor_id in root_kinds
                            else {}
                        ),
                    }
                )
                cursor_id = shortest.get(cursor_id)
            paths.append(
                {
                    "object_id": hex(int(identifier)),
                    "retained_bytes": retained[identifier],
                    "path": list(reversed(path)),
                    "classification": "retention_candidate",
                    "truncated": cursor_id not in {None, "0"},
                }
            )
        notify("emit")
        parsed = stack_result(
            [([name], row["shallow_bytes"]) for name, row in histogram.items()],
            unit="bytes",
            metadata={
                "format": "hprof",
                "timestamp_ms": timestamp_ms,
                "identifier_size": identifier_size,
                "index_resumed": resumed,
                "index_schema_version": INDEX_SCHEMA_VERSION,
                "index_rebuilt": index_rebuilt,
            },
        )
        parsed["sections"] = {
            "heap": {
                "format": "hprof",
                "unit": "bytes",
                "size_basis": "hprof_serialized_payload_excluding_vm_headers_and_alignment",
                "totals": {
                    "objects": len(objects),
                    "shallow_bytes": sum(value[1] for value in objects.values()),
                    "root_count": len(root_kinds),
                    "reachable_objects": len(seen) - 1,
                    "unreachable_objects": len(objects) - len(seen) + 1,
                },
                "classes": [
                    {
                        "class_name": name,
                        "retained_bytes": value["retained_bytes"],
                        **value,
                    }
                    for name, value in sorted(
                        histogram.items(),
                        key=lambda item: (-item[1]["shallow_bytes"], item[0]),
                    )[:200]
                ],
                "dominators": [node_row(identifier) for identifier in top],
                "leak_paths": paths,
                "limitations": [
                    "对象大小使用 HPROF 字段和数组载荷字节，不含 VM 对象头、压缩引用差异与对齐；retained 同样采用此权重。",
                    "java.lang.ref.Reference.referent 不作为强引用。retention candidate 需跨时间堆对比才能确认泄漏。",
                    "类间 retained 值不可直接相加；同类嵌套 dominator 已去重。",
                ],
            }
        }
        return parsed
    finally:
        connection.close()
