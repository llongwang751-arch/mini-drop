"""Behavior checks for synthetic teaching inputs; no external dependencies."""

import unittest

from tutorials.labs.analysis import (
    SSEDecoder,
    decode_lesson_json,
    heap_dominators,
    parse_folded,
    validate_lesson_contract,
)


class AnalysisLessonsTest(unittest.TestCase):
    def test_folded_recursive_function_counts_each_path_once(self):
        parsed = parse_folded(b"main;walk;walk 4\nmain;walk 6\n")
        walk = next(row for row in parsed["functions"] if row["name"] == "walk")
        self.assertEqual(walk["inclusive"], parsed["total_weight"])
        self.assertEqual(walk["self"], 10)

    def test_folded_rejects_unsafe_total_before_returning_a_tree(self):
        with self.assertRaisesRegex(ValueError, "INVALID_WEIGHT"):
            parse_folded(b"main 9007199254740991\nother 1\n")

    def test_shared_object_changes_retention_only_when_last_alternate_path_goes(self):
        objects = {"a": 1, "b": 2, "payload": 10}
        shared = heap_dominators(objects, {"a": ["payload"], "b": ["payload"]}, ["a", "b"])
        exclusive = heap_dominators(objects, {"a": ["payload"]}, ["a", "b"])
        self.assertEqual(shared["retained"]["a"], 1)
        self.assertEqual(exclusive["retained"]["a"], 11)
        self.assertEqual(shared["retained"]["@root"], exclusive["retained"]["@root"])

    def test_unrooted_cycle_is_not_made_reachable_by_its_own_references(self):
        result = heap_dominators({"rooted": 1, "a": 2, "b": 3}, {"a": ["b"], "b": ["a"]}, ["rooted"])
        self.assertEqual(result["unreachable"], ["a", "b"])
        self.assertEqual(result["retained"]["@root"], 1)

    def test_every_byte_boundary_preserves_complete_sse_event(self):
        wire = 'id: 9007199254740993\r\ndata: 你好\r\n\r\n'.encode("utf-8")
        decoder = SSEDecoder()
        events = []
        for value in wire:
            events.extend(decoder.push(bytes([value])))
        events.extend(decoder.finish())
        self.assertEqual(events, [{"id": "9007199254740993", "type": "message", "data": "你好"}])

    def test_unclosed_frame_cannot_skip_an_event_on_reconnect(self):
        decoder = SSEDecoder("41")
        self.assertEqual(decoder.push(b"id: 42\ndata: not complete\n") + decoder.finish(), [])
        self.assertEqual(decoder.last_event_id, "41")

    def test_contract_legacy_missing_source_does_not_become_controlled_measurement(self):
        result = validate_lesson_contract({"schema_version": 1, "kind": "LESSON_SELF_CPU_V2", "unit": "cpu_ticks", "weight": 0})
        self.assertEqual(result["provenance"], {"source": "unknown"})

    def test_contract_rejects_unrecognized_top_level_fields(self):
        with self.assertRaisesRegex(ValueError, "INVALID_FIELDS"):
            validate_lesson_contract({"schema_version": 1, "kind": "LESSON_SELF_CPU_V2", "unit": "cpu_ticks", "weight": 1, "path": "/arbitrary"})

    def test_contract_duplicate_source_cannot_be_lost_during_json_decoding(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE_KEY"):
            decode_lesson_json(b'{"provenance":{"source":"synthetic","source":"controlled_self"}}')


if __name__ == "__main__":
    unittest.main()
