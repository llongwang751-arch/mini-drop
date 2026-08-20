"""Export the FastAPI OpenAPI spec as a versioned contract deliverable.

Writes docs/contracts/openapi.v1.json so external clients and contract tests
have a frozen, diffable API surface (guide §6.7).
"""

from __future__ import annotations

import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "openapi.v1.json"


def main() -> None:
    from server.app.main import app

    spec = app.openapi()
    OUTPUT.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"exported {len(spec.get('paths', {}))} paths to {OUTPUT}")


if __name__ == "__main__":
    main()
