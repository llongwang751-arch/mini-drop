#!/usr/bin/env python3
"""Synthetic program transformations only; never actual profile evidence."""
import unittest

from stack_controls import compare_argv, stop_gate_argv


class StackControlTests(unittest.TestCase):
    def setUp(self):
        self.program = (
            "uprobe:/fixed/libc.so:ma*lloc /pid == 42/ { @size[tid]=arg0; }\n"
            'uretprobe:/fixed/libc.so:ma*lloc /pid == 42/ { printf("alloc:%s",ustack(perf,16)); }\n'
            'uprobe:/fixed/libc.so:fr*ee /pid == 42/ { printf("free:%s",ustack(perf,16)); }\n'
            "interval:s:1 /@events >= 10000/ { exit(); } interval:s:2 { exit(); } END { clear(@events); clear(@size); }"
        )
        self.arguments = ["/usr/bin/bpftrace", "-q", "-p", "42", "-e", self.program]

    def test_raw_control_changes_only_stack_format(self):
        original, raw = compare_argv(self.arguments, 42)
        self.assertEqual(original, self.arguments)
        self.assertEqual(raw[:5], original[:5])
        self.assertEqual(raw[5].replace("ustack(raw,16)","ustack(perf,16)"), original[5])

    def test_gate_covers_each_probe_and_is_preserved_in_end(self):
        controlled = stop_gate_argv(self.arguments,42)[5]
        self.assertEqual(controlled.count("/!@drop_done && (pid == 42)/"),3)
        self.assertEqual(controlled.count("{ @drop_done=1; exit(); }"),2)
        self.assertIn("END { @drop_done=1; clear(@events); clear(@size); }",controlled)
        self.assertNotIn("clear(@drop_done)",controlled)
        self.assertEqual(controlled.count("ustack(perf,16)"),2)

    def test_foreign_pid_is_rejected(self):
        with self.assertRaises(RuntimeError):
            stop_gate_argv(self.arguments,43)

    def test_changed_exit_structure_is_rejected(self):
        with self.assertRaises(RuntimeError):
            stop_gate_argv(self.arguments[:5]+[self.program.replace("END { ","FINISH { ")],42)

    def test_second_gate_instrumentation_is_rejected(self):
        with self.assertRaises(RuntimeError):
            stop_gate_argv(stop_gate_argv(self.arguments,42),42)


if __name__ == "__main__":
    unittest.main()
