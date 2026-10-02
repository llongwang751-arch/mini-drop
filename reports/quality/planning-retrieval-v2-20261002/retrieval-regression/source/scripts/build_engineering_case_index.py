"""Build the default defect catalog from pinned, independently checked evidence.

Known engineering fixes are graded separately from model-generated causal roots.
Missing tests, unexpected skips, duplicate identities or altered bytes fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT/'contracts/engineering_cases.json'
OUTPUT = ROOT/'web/public/report-assets/engineering-cases/index.json'


def evidence(root: Path, item: dict) -> tuple[Path, bytes]:
    relative = Path(item['path'])
    path = (root/relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError('evidence path outside workspace')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item['sha256']:
        raise ValueError('evidence hash mismatch: '+item['path'])
    return path, raw


def junit(raw: bytes, selector: dict, expected: int) -> dict:
    selected = [test for test in ET.fromstring(raw).iter('testcase')
                if test.get('classname') == selector['classname']
                and (not selector.get('name') or test.get('name') == selector['name'])]
    names = [test.get('name') for test in selected]
    if len(selected) != expected or len(names) != len(set(names)):
        raise ValueError('missing or duplicate selected tests')
    failed = sum(test.find('failure') is not None or test.find('error') is not None for test in selected)
    skipped = sum(test.find('skipped') is not None for test in selected)
    return {'selected': len(selected), 'failed': failed, 'skipped': skipped,
            'passed': len(selected)-failed-skipped, 'test_names': names}


def evaluate_case(case: dict, root: Path) -> dict:
    loaded = {name: evidence(root, item)[1] for name, item in case['evidence'].items()}
    kind = case['kind']
    if kind == 'PARALLEL_TIMING_REPLAY':
        report = json.loads(loaded['reproduction'])
        if (report['schema'] != 'mini-drop.parallel-timing-reproduction.v1'
                or report['scope'] != 'DETERMINISTIC_TELEMETRY_REPLAY; NO_LIVE_LLM; NO_AI_ROOT_GRADE'
                or report['before']['source_sha256'] != hashlib.sha256(loaded['before_source']).hexdigest()
                or report['after']['source_sha256'] != hashlib.sha256(loaded['after_source']).hexdigest()
                or report['expected_retrieval_ms'] != 2000
                or report['intervals_seconds'] != {'search': [0, 10], 'embeddings': [[0, 6], [2, 8]]}
                or report['before']['actual_retrieval_ms'] != 0
                or report['before']['matches_expected'] is not False
                or report['after']['actual_retrieval_ms'] != 2000
                or report['after']['matches_expected'] is not True):
            raise ValueError('timing reproduction does not meet the independent interval oracle')
        before = {'selected': 1, 'failed': 1, 'passed': 0, 'skipped': 0, 'retrieval_ms': 0}
        after = {'selected': 1, 'failed': 0, 'passed': 1, 'skipped': 0, 'retrieval_ms': 2000}
    elif kind == 'JUNIT_BEFORE_AFTER':
        before = junit(loaded['before'], case['selector'], case['selected_tests'])
        after = junit(loaded['after'], case['selector'], case['selected_tests'])
        if before['test_names'] != after['test_names']:
            raise ValueError('before/after test identities differ')
    elif kind == 'VITEST_SELECTED_BEFORE_AFTER':
        old = loaded['before'].decode('utf-8')
        if (case['before_test_title'] not in old
                or not re.search(r'Tests\s+1 failed\s*\|\s*16 skipped\s*\(17\)', old)
                or 'expected document not to contain element' not in old):
            raise ValueError('missing selected notification regression')
        before = {'selected': 1, 'failed': 1, 'passed': 0, 'skipped': 0}
        after = junit(loaded['after'], case['selector'], 1)
    else:
        raise ValueError('unsupported defect evidence kind')
    if (before['failed'] != case['expected_before_failures'] or before['skipped']
            or after['failed'] or after['skipped'] or after['passed'] != after['selected']):
        raise ValueError('defect was not reproduced and fully regressed')
    return {**{key: case[key] for key in ('id', 'title', 'area', 'symptom', 'root_cause', 'fix',
                                        'scope_note', 'reproduce_command', 'source_url')},
            'status': 'VERIFIED_DEFECT_FIX', 'validation_scope': 'ENGINEERING_DEFECT_REGRESSION',
            'model_auto_root_cause': 'NOT_EVALUATED', 'before': before, 'after': after,
            'evidence': [{'role': role, 'filename': case['id']+'/'+Path(item['path']).name,
                          'sha256': item['sha256']} for role, item in case['evidence'].items()]}


def build(plan_path: Path = PLAN, root: Path = ROOT) -> dict:
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if plan['schema'] != 'mini-drop.engineering-case-plan.v1' or not plan['cases']:
        raise ValueError('invalid defect catalog contract')
    ids = [case['id'] for case in plan['cases']]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[a-z][a-z0-9-]{1,63}', value) for value in ids):
        raise ValueError('duplicate or unsafe defect id')
    return {'schema': 'mini-drop.engineering-case-index.v1', 'revision': plan['revision'],
            'validation_scope': 'ENGINEERING_DEFECT_REGRESSION', 'model_auto_root_cause': 'NOT_EVALUATED',
            'legacy_fault_catalog': {'count': 21, 'preserved': True, 'affects_this_score': False},
            'cases': [evaluate_case(case, root) for case in plan['cases']]}


def generate(plan_path: Path = PLAN, root: Path = ROOT, output: Path = OUTPUT, check: bool = False) -> dict:
    result = build(plan_path, root)
    raw = (json.dumps(result, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if check:
        if not output.exists() or output.read_bytes() != raw:
            raise ValueError('generated defect index drift')
        for case in plan['cases']:
            for item in case['evidence'].values():
                source, body = evidence(root, item)
                target = output.parent/case['id']/source.name
                if not target.exists() or target.read_bytes() != body:
                    raise ValueError('generated defect artifact drift')
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        for case in plan['cases']:
            for item in case['evidence'].values():
                source, _ = evidence(root, item)
                target = output.parent/case['id']/source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        output.write_bytes(raw)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = generate(check=args.check)
    print(json.dumps({'verified_defect_cases': len(result['cases']),
                      'model_auto_root_cause': result['model_auto_root_cause'], 'check_only': args.check}))
