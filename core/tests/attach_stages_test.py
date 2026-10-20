#!/usr/bin/env python3
"""Portable tests of diagnostic instrumentation; no real capture is simulated."""
import unittest

from attach_stages import ATTACH_COMMANDS, instrument_attach, stage_names


class AttachStagesTests(unittest.TestCase):
    def setUp(self):
        # Explicitly synthetic command-structure data, not capture evidence.
        self.source = "# synthetic diagnostic test input\n" + "\n".join(ATTACH_COMMANDS) + "\n"

    def test_every_original_command_is_retained_in_order(self):
        result = instrument_attach(self.source)
        retained = [line for line in result.splitlines(keepends=True)
                    if "echo DROP_STAGE_" not in line and not line.startswith(("set direct-call-timeout", "set unwind-on-timeout"))]
        self.assertEqual("".join(retained), self.source)

    def test_upstream_missing_stage_fails_closed(self):
        with self.assertRaises(RuntimeError):
            instrument_attach(self.source.replace("set $result = (int)access($libpath, 4)", ""))

    def test_duplicate_stage_fails_closed(self):
        with self.assertRaises(RuntimeError):
            instrument_attach(self.source + "continue\n")

    def test_second_instrumentation_is_rejected(self):
        with self.assertRaises(RuntimeError):
            instrument_attach(instrument_attach(self.source))

    def test_windows_line_endings_remain_intact(self):
        source = self.source.replace("\n", "\r\n")
        result = instrument_attach(source)
        self.assertNotIn("\n", result.replace("\r\n", ""))

    def test_only_fixed_stage_tokens_are_retained(self):
        output = "DROP_STAGE_ACCESS_BEGIN\n#0 secret-profile-data\nDROP_STAGE_PATH=/secret\nDROP_STAGE_ACCESS_END\n"
        self.assertEqual(stage_names(output), ["DROP_STAGE_ACCESS_BEGIN", "DROP_STAGE_ACCESS_END"])


if __name__ == "__main__":
    unittest.main()
