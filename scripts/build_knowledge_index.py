"""Build an immutable Chroma snapshot from approved repository knowledge."""
from pathlib import Path
import argparse
from dataclasses import replace
import getpass
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app.agent_runtime.semantic_retrieval import (
    RetrievalSettings, SemanticProvider, build_index,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-root", type=Path, default=ROOT / "knowledge")
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        settings = RetrievalSettings.from_env()
        if args.prompt_key:
            settings = replace(settings, api_key=getpass.getpass("SiliconFlow API key (hidden): "))
        result = {"status": "READY", **build_index(args.knowledge_root.resolve(), SemanticProvider(settings))}
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}))
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
