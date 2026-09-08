"""Regression tests for repeatable bounded-memory fault cleanup."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_demo_module():
    path = Path(__file__).parents[1] / "demo" / "python-hotspot" / "app.py"
    spec = spec_from_file_location("mini_drop_python_hotspot", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_stop_releases_buffers_and_trims_linux_heap(monkeypatch):
    # Importing the demo creates its process-wide CPU fault singleton. Keep it
    # idle in this memory-only unit test so it cannot starve later test files.
    monkeypatch.setenv("CPU_HOTSPOT_ACTIVE", "0")
    module = _load_demo_module()
    fault = module.MemoryFault()
    fault._buffers = [bytearray(1024)]
    fault._target_bytes = 1024
    fault._enabled.set()
    calls = []
    monkeypatch.setattr(module, "_trim_process_heap", lambda: calls.append(True) or True)

    fault.stop()

    assert fault._buffers == []
    assert fault._target_bytes == 0
    assert fault._enabled.is_set() is False
    assert calls == [True]


def test_demo_sets_a_stable_linux_process_name_for_agent_discovery(monkeypatch):
    monkeypatch.setenv("CPU_HOTSPOT_ACTIVE", "0")
    module = _load_demo_module()
    calls = []

    class FakeLibc:
        @staticmethod
        def prctl(*args):
            calls.append(args)
            return 0

    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.ctypes, "CDLL", lambda _name: FakeLibc())

    assert module._set_linux_process_name("python-hotspot") is True
    assert calls[0][0] == 15  # Linux PR_SET_NAME
    assert calls[0][1].value == b"python-hotspot"
