from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_server_image_contains_runtime_import_roots() -> None:
    """Keep production image imports aligned with modules imported at startup."""

    dockerfile = (ROOT / "deploy" / "dockerfiles" / "server.Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "COPY server/ ./server/" in dockerfile
    assert "COPY scripts/ ./scripts/" in dockerfile

