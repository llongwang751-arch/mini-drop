"""Build the reviewed AGI-saber SQLite ingest patch from an immutable cloud release.

Inputs are the pre-fix source and reviewed post-fix source, supplied explicitly.
The output is a standard patch against paths beneath the AGI-saber project root.
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path


PATCH_FILES = {
    "ingest": {
        "local_repos.py": "internal/application/local_repos.py",
        "hybrid.py": "internal/rag/hybrid.py",
        "rag.py": "internal/rag/rag.py",
    },
    "milvus-lite": {"infra.py": "internal/infra/infra.py"},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=PATCH_FILES, default="ingest")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    chunks: list[str] = []
    for source, target in PATCH_FILES[args.scope].items():
        before = (args.before / source).read_text(encoding="utf-8").splitlines(keepends=True)
        after = (args.after / source).read_text(encoding="utf-8").splitlines(keepends=True)
        if before == after:
            parser.error(f"{source} has no changes")
        chunks.extend(difflib.unified_diff(before, after, fromfile=f"a/{target}", tofile=f"b/{target}"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(chunks))
    print(args.output)


if __name__ == "__main__":
    main()
