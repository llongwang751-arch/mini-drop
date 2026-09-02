from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_worker_image_contains_runtime_import_roots() -> None:
    """The private Python worker image packages analysis and AI modules only."""

    dockerfile = (ROOT / "deploy" / "dockerfiles" / "python-worker.Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "COPY server/ ./server/" in dockerfile
    assert "COPY analyzer/ ./analyzer/" in dockerfile
    assert "COPY scripts/ ./scripts/" in dockerfile
    assert "FastAPI" not in dockerfile
