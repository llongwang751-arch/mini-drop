import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app import analyzer_runner


def _bundle(path: Path, *, symlink: bool = False) -> None:
    with tarfile.open(path, "w") as archive:
        item = tarfile.TarInfo("windows/window-0.data")
        if symlink:
            item.type = tarfile.SYMTYPE
            item.linkname = "../../outside"
            archive.addfile(item)
        else:
            payload = b"fake-perf-data"
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))


def test_continuous_bundle_maps_each_window_and_keeps_local_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    archive = tmp_path / "continuous-perf.tar"
    _bundle(archive)
    monkeypatch.setattr(
        analyzer_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b""),
    )

    def fake_outputs(output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "callgraph.json"
        path.write_text("{}")
        return [{
            "artifact_type": "callgraph_json",
            "filename": "callgraph.json",
            "local_path": str(path),
            "content_type": "application/json",
            "size_bytes": 2,
            "metadata": {},
        }]

    monkeypatch.setattr(analyzer_runner, "_collect_analyzer_outputs", fake_outputs)
    outputs = analyzer_runner.analyze_continuous_perf_bundle(
        "task-1",
        [{"artifact_type": "continuous_bundle", "local_path": str(archive)}],
    )
    assert outputs[0]["artifact_type"] == "continuous_callgraph_json"
    assert outputs[0]["metadata"]["window_index"] == 0
    assert Path(outputs[0]["local_path"]).is_file()


def test_continuous_bundle_rejects_matching_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    archive = tmp_path / "continuous-perf.tar"
    _bundle(archive, symlink=True)
    with pytest.raises(ValueError, match="non-regular"):
        analyzer_runner.analyze_continuous_perf_bundle(
            "task-1",
            [{"artifact_type": "continuous_bundle", "local_path": str(archive)}],
        )
