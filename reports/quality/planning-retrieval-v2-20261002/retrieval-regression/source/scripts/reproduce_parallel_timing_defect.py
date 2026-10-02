"""Replay real before/after timing code against independently specified intervals.

This is a deterministic telemetry regression, not a live model or AI diagnosis.
The old assignment is extracted from the frozen source rather than reimplemented.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def reproduce(before: Path, after: Path) -> dict:
    old = before.read_bytes()
    tree = ast.parse(old)
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Subscript)
                           and isinstance(target.value, ast.Name) and target.value.id == 'stages'
                           and isinstance(target.slice, ast.Constant) and target.slice.value == 'retrieval_ms'
                           for target in node.targets)]
    if len(assignments) != 1:
        raise ValueError('expected one frozen retrieval calculation')
    # Trusted repository snapshot; execute only that assignment with its original inputs.
    namespace = {'stages': {'search_total_ms': 10000.0, 'embedding_ms': 12000.0}}
    statement = assignments[0]
    exec(compile(ast.Module(body=[statement], type_ignores=[]), str(before), 'exec'), namespace)
    spec = importlib.util.spec_from_file_location('office_timing_reproduction', after)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    window = module._Window()
    window.span('search_total_ms', 0, 10)
    window.span('embedding_ms', 0, 6)
    window.span('embedding_ms', 2, 8)
    stages, _ = window.question_timings()
    expected = 2000.0  # independently: search 0..10 minus embedding union 0..8
    actual_before, actual_after = namespace['stages']['retrieval_ms'], stages['retrieval_ms']
    return {
        'schema': 'mini-drop.parallel-timing-reproduction.v1',
        'scope': 'DETERMINISTIC_TELEMETRY_REPLAY; NO_LIVE_LLM; NO_AI_ROOT_GRADE',
        'expected_retrieval_ms': expected,
        'intervals_seconds': {'search': [0, 10], 'embeddings': [[0, 6], [2, 8]]},
        'before': {'source_sha256': hashlib.sha256(old).hexdigest(),
                   'actual_retrieval_ms': actual_before, 'matches_expected': actual_before == expected,
                   'source_line': statement.lineno, 'calculation': ast.get_source_segment(old.decode(), statement)},
        'after': {'source_sha256': hashlib.sha256(after.read_bytes()).hexdigest(),
                  'actual_retrieval_ms': actual_after, 'matches_expected': actual_after == expected},
        'status': 'VERIFIED_DEFECT_FIX' if actual_before != expected and actual_after == expected else 'REJECTED',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, default=ROOT/'integrations/agi_saber/request_observations.py')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = reproduce(args.before, args.after)
    result['measured_at'] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status': result['status'], 'before_ms': result['before']['actual_retrieval_ms'],
                      'after_ms': result['after']['actual_retrieval_ms']}))
    raise SystemExit(0 if result['status'] == 'VERIFIED_DEFECT_FIX' else 1)
