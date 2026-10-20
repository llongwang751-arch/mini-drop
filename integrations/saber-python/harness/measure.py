"""固定的真实 FTS5 查询测量，不启动完整 Saber，也不调用外部接口。"""
import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import statistics
import time

from saber_component import RELATIVE_SOURCE, execute_case, load_component, query_cases, synthetic_database, typed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    source = Path(args.source).resolve()
    module = load_component(source)
    cases = query_cases(args.seed)
    conn = synthetic_database(module)
    outputs, latencies = [], []
    profile = cProfile.Profile()
    if args.profile:
        profile.enable()
    start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    try:
        for case in cases:
            tick = time.perf_counter_ns()
            outputs.append(execute_case(module, conn, case))
            latencies.append(time.perf_counter_ns() - tick)
        elapsed = time.perf_counter_ns() - start
        cpu_elapsed = time.process_time_ns() - cpu_start
    finally:
        if args.profile:
            profile.disable()
        conn.close()
    serialized = json.dumps(typed(outputs), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    matching = [output for output in outputs if output["executed_query"] and output["hits"]]
    examples = [output for output in matching if len(output["query"]) < 1024][:2]
    examples += [output for output in matching if len(output["query"]) >= 1024][:2]
    samples = [{"query_sha256": hashlib.sha256(output["query"].encode()).hexdigest(),
                "query_characters": len(output["query"]), "executed_query": output["executed_query"],
                "hits": [{key: row[key] for key in ("pg_id", "score", "source", "engine")} for row in output["hits"]]}
               for output in examples]
    result = {"cases": len(cases), "output_sha256": hashlib.sha256(serialized).hexdigest(),
              "output_comparison": "recursive exact Python types and values: actual MATCH parameters, tenant, hit IDs/content and BM25 scores",
              "elapsed_ns": elapsed, "cpu_ns": cpu_elapsed, "median_case_ns": statistics.median(latencies),
              "p95_case_ns": sorted(latencies)[int(.95 * (len(latencies) - 1))],
              "source_sha256": hashlib.sha256((source / RELATIVE_SOURCE).read_bytes()).hexdigest(),
              "query_length_min": min(len(case["query"]) for case in cases),
              "query_length_max": max(len(case["query"]) for case in cases),
              "executed_sql_queries": sum(output["executed_query"] is not None for output in outputs),
              "sample_results": samples,
              "scope": "actual Saber SQLite FTS5 component, 64 synthetic queries and 64 indexed synthetic chunks; excludes index construction, not full Saber startup or production request latency"}
    if args.profile:
        rows = []
        for entry in profile.getstats():
            code = entry.code
            if not hasattr(code, "co_filename") or Path(code.co_filename).resolve() != (source / RELATIVE_SOURCE).resolve():
                continue
            rows.append({"path": RELATIVE_SOURCE, "line": code.co_firstlineno, "function": code.co_name,
                         "calls": entry.callcount, "self_seconds": entry.inlinetime, "total_seconds": entry.totaltime})
        rows.sort(key=lambda item: item["self_seconds"], reverse=True)
        result["hotspots"] = rows[:20]
        result["profile_unit"] = "seconds"
        result["profile_source"] = "actual cProfile execution of the enrolled real file with exact SHA; synthetic in-memory SQLite corpus"
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
