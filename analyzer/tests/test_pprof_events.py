import gzip
import json
import unittest

from drop_analyzer.errors import AnalysisError
from drop_analyzer.events import (
    analyze_io,
    analyze_memory,
    analyze_resources,
    analyze_script,
)
from drop_analyzer.pprof import analyze_pprof


def vi(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    return bytes(out + bytes([value]))


def f(number, value):
    return vi(number << 3 | (2 if isinstance(value, bytes) else 0)) + (
        vi(len(value)) + value if isinstance(value, bytes) else vi(value)
    )


def profile():
    # Explicitly constructed format fixture, never represented as a collected profile.
    strings = [b"", b"cpu", b"nanoseconds", b"root", b"leaf", b"env", b"prod"]
    raw = b"".join(f(6, text) for text in strings)
    raw += f(1, f(1, 1) + f(2, 2))
    raw += f(5, f(1, 1) + f(2, 3)) + f(5, f(1, 2) + f(2, 4))
    raw += f(4, f(1, 1) + f(4, f(1, 1))) + f(4, f(1, 2) + f(4, f(1, 2)))
    raw += f(2, f(1, vi(2) + vi(1)) + f(2, vi(100)) + f(3, f(1, 5) + f(2, 6)))
    return gzip.compress(raw)


class ProfileEventsTests(unittest.TestCase):
    def test_pprof_real_wire_tables_and_label_filter(self):
        parsed = analyze_pprof(profile(), kind="GO_CPU")
        self.assertEqual(parsed["metadata"]["sample_count"], 100)
        self.assertEqual(parsed["metadata"]["unit"], "nanoseconds")
        self.assertEqual(parsed["flamegraph"]["children"][0]["name"], "root")
        self.assertEqual(parsed["top"]["self"][0]["name"], "leaf")
        filtered = analyze_pprof(
            profile(), kind="GO_CPU", params={"labels": {"env": "dev"}}
        )
        self.assertEqual(filtered["metadata"]["sample_count"], 0)
        with self.assertRaises(AnalysisError):
            analyze_pprof(gzip.decompress(profile())[:-1], kind="GO_CPU")

    def test_io_quantiles_queue_hotspots(self):
        rows = [
            {
                "event": "io",
                "time_ns": 100,
                "duration_ns": 100,
                "bytes": 10,
                "operation": "read",
                "pid": 1,
                "device": "vda",
            },
            {
                "event": "io",
                "time_ns": 150,
                "duration_ns": 100,
                "bytes": 20,
                "operation": "write",
                "pid": 1,
                "device": "vda",
            },
        ]
        result = analyze_io(b"\n".join(json.dumps(row).encode() for row in rows))[
            "sections"
        ]["io"]
        self.assertEqual(result["bytes"], 30)
        self.assertEqual(result["queue"]["max_depth"], 2)
        self.assertEqual(result["latency_ns"], {"p50": 100, "p95": 100, "p99": 100})
        self.assertEqual(result["devices"][0]["operations"], 2)

    def test_memory_lifetimes_not_claimed_as_leaks(self):
        rows = [
            {"event": "alloc", "time_ns": 1, "address": "0x1", "size": 100},
            {"event": "alloc", "time_ns": 2, "address": "0x2", "size": 25},
            {"event": "free", "time_ns": 3, "address": "0x1"},
            {"event": "major_fault", "time_ns": 4},
        ]
        result = analyze_memory(b"\n".join(json.dumps(row).encode() for row in rows))[
            "sections"
        ]["memory"]
        self.assertEqual(
            (result["live_bytes"], result["peak_live_bytes"], result["freed_bytes"]),
            (25, 125, 100),
        )
        self.assertEqual(result["pagefaults"]["major"], 1)
        self.assertEqual(
            result["survivors"][0]["classification"], "unfreed_during_observation"
        )

    def test_resource_and_script_validation(self):
        result = analyze_resources(
            b'{"samples":[{"timestamp_ns":1,"rss_bytes":42,"cpu_percent":12.5}]}'
        )
        self.assertEqual(result["sections"]["resources"]["peaks"]["rss_bytes"], 42)
        self.assertEqual(
            analyze_script(b'{"template":"uname","exit_code":0,"stdout":"Linux"}')[
                "sections"
            ]["script"]["stdout"],
            "Linux",
        )
        with self.assertRaises(AnalysisError):
            analyze_script(b'{"template":"uname","exit_code":1}')

    def test_resource_network_context_switches_overhead_and_kernel_log_status(self):
        snapshot = {
            "samples": [
                {
                    "timestamp_ns": 1,
                    "network_rx_bytes": 42,
                    "network_tx_bytes": 84,
                    "voluntary_context_switches": 3,
                    "collector_cpu_ticks": 2,
                    "collector_rss_bytes": 1024,
                    "memory_total_bytes": 8192,
                    "memory_used_bytes": 4096,
                }
            ],
            "network_scope": "target_network_namespace_total",
            "context_switch_scope": "target process main thread counters",
            "collector_overhead": {
                "user_cpu_seconds": 0.01,
                "scope": "collector_process",
            },
            "dmesg": {
                "status": "permission_denied",
                "lines": [],
                "reason": "Kernel logs disabled by operator policy",
                "truncated": False,
            },
        }
        section = analyze_resources(json.dumps(snapshot).encode())["sections"][
            "resources"
        ]
        self.assertEqual(section["samples"][0]["network_rx_bytes"], 42)
        self.assertEqual(section["samples"][0]["voluntary_context_switches"], 3)
        self.assertEqual(section["network_scope"], snapshot["network_scope"])
        self.assertEqual(
            section["context_switch_scope"], snapshot["context_switch_scope"]
        )
        self.assertEqual(section["samples"][0]["memory_used_bytes"], 4096)
        self.assertEqual(section["collector_overhead"]["user_cpu_seconds"], 0.01)
        self.assertEqual(section["dmesg"]["status"], "permission_denied")
        snapshot["dmesg"] = {"status": "ok", "lines": ["x" * 1025]}
        with self.assertRaises(AnalysisError):
            analyze_resources(json.dumps(snapshot).encode())
        snapshot["dmesg"] = {"status": "ok", "lines": ["x" * 1024] * 65}
        with self.assertRaises(AnalysisError):
            analyze_resources(json.dumps(snapshot).encode())

    def test_io_syscall_errors_are_not_bytes_and_missing_latency_is_unknown(self):
        row = {
            "event": "io",
            "time_ns": 1792157000000000000,
            "bytes": -11,
            "operation": "tracepoint:syscalls:sys_enter_read",
        }
        parsed = analyze_io(json.dumps(row).encode())
        self.assertEqual(parsed["metadata"]["sample_count"], 0)
        self.assertEqual(parsed["sections"]["io"]["error_operations"], 1)
        self.assertEqual(parsed["sections"]["io"]["read_operations"], 1)
        self.assertIsNone(parsed["sections"]["io"]["latency_ns"]["p95"])

    def test_memory_partial_unmap_realloc_and_unknown_faults(self):
        rows = [
            {"event": "mmap", "time_ns": 1, "address": 4096, "size": 8192},
            {"event": "munmap", "time_ns": 2, "address": "0x2000", "size": 4096},
            {"event": "alloc", "time_ns": 3, "address": "0x4", "size": 100},
            {
                "event": "realloc",
                "time_ns": 4,
                "address": 4,
                "old_address": 4,
                "size": 200,
            },
            {"event": "pagefault", "time_ns": 5},
            {"event": "alloc", "time_ns": 6, "address": 0, "size": 100},
        ]
        memory = analyze_memory(
            b"\n".join(json.dumps(row).encode() for row in reversed(rows))
        )["sections"]["memory"]
        self.assertEqual(memory["live_bytes"], 4096 + 200)
        self.assertEqual(memory["freed_bytes"], 4096 + 100)
        self.assertEqual(
            memory["pagefaults"], {"minor": 0, "major": 0, "unknown": 1, "total": 1}
        )
        self.assertEqual(memory["failed_allocations"], 1)


if __name__ == "__main__":
    unittest.main()
