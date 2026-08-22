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
