"""Small live embedding/rerank/Chroma check using synthetic public SRE text.

Credentials are read from the environment or a hidden prompt, never saved.
The generated result describes connectivity, not root-cause accuracy.
"""
from pathlib import Path
import argparse
import getpass
import json
import os
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app.agent_runtime.semantic_retrieval import RetrievalSettings, SemanticProvider, build_index, search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = getpass.getpass("SiliconFlow API key (hidden): ") if args.prompt_key else os.getenv("SILICONFLOW_API_KEY", "")
    settings = RetrievalSettings(api_key=key)
    provider = SemanticProvider(settings)
    started = time.monotonic()
    result = {"kind": "SYNTHETIC_CONNECTIVITY_SMOKE", "embedding_model": settings.embedding_model,
              "rerank_model": settings.rerank_model, "dimensions": settings.dimensions}
    try:
        import chromadb
        from chromadb.config import Settings
        # OS temp directory is new and independent of all production data.
        scratch = Path(tempfile.mkdtemp(prefix="mini-drop-retrieval-smoke-"))
        root = scratch / "knowledge"
        root.mkdir()
        (root / "cpu.md").write_text("# Python CPU\nUse py-spy to identify CPU hotspots and GIL contention. Check process CPU and own samples.", encoding="utf-8")
        (root / "lock.md").write_text("# Java lock contention\nUse async-profiler lock events and thread dumps to identify monitor waits. CPU samples alone cannot explain blocked threads.", encoding="utf-8")
        (root / "catalog.json").write_text(json.dumps([
            {"knowledge_id": "cpu", "title": "Python CPU", "document": "cpu.md"},
            {"knowledge_id": "lock", "title": "Java lock contention", "document": "lock.md"},
        ]), encoding="utf-8")
        client = chromadb.PersistentClient(path=str(scratch / "chroma"), settings=Settings(anonymized_telemetry=False))
        result["index"] = build_index(root, provider, client)
        trace = search("Java 请求线程阻塞，应检查哪个采样事件？", root, provider=provider, client=client)
        result.update(actual_backend=trace["actual_backend"], degraded_reasons=trace["degraded_reasons"],
                      matched_ids=[m["knowledge_id"] for m in trace["matches"]])
        result["passed"] = trace["actual_backend"] == "BM25_ENTITY_CHROMA_RRF_RERANK" and result["matched_ids"][:1] == ["lock"]
    except Exception as exc:
        result.update(passed=False, error_type=type(exc).__name__)
        result["failure_frames"] = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                                    for frame in traceback.extract_tb(exc.__traceback__)]
        # Safe provider codes have no raw payload or credentials.
        from server.app.agent_runtime.semantic_retrieval import RetrievalUnavailable
        if isinstance(exc, RetrievalUnavailable):
            result["error_code"] = str(exc)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
