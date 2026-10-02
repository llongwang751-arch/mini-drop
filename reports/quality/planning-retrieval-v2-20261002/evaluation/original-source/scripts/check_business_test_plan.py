"""Validate the maintained business plan and emit a conservative PR test list."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_business_acceptance import CASES


def validate(plan,root=ROOT):
    if plan.get('schema')!='mini-drop.business-test-plan.v1': raise ValueError('unsupported plan schema')
    ids=[]
    external=plan.get('external_cases',[])
    if {case['id'] for case in external}!={'RAG-ACTUAL-01'}:raise ValueError('missing actual RAG source integration plan')
    for case in plan['cases']+external:
        ids.append(case['id'])
        for key in ('requirement','owner','risk','paths','test','runner','required_outcome'):
            if not case.get(key):raise ValueError(f"{case['id']}: missing {key}")
        if case.get('oracle_visibility')!='HARNESS_ONLY':raise ValueError('oracle must stay outside Agent input')
        for key in ('test','runner'):
            path=(root/case[key]).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError(f'missing or unsafe {key}')
        if case['required_outcome'] not in ('IMPROVEMENT_VERIFIED','DEGRADED_AVAILABLE'):raise ValueError('invalid expected outcome')
    if len(set(ids))!=len(ids) or {case['id'] for case in plan['cases']}!=set(CASES):raise ValueError('plan and executable scenarios differ')
    if any(case.get('execution')!='FROZEN_EXTERNAL_SOURCE_REQUIRED' or not case.get('source_contract') for case in external):
        raise ValueError('external source runs must declare their dependency and execution boundary')
    return plan


def affected(plan,paths):
    # Shared boundaries always trigger all cases. Unknown code changes cannot
    # be considered covered by a path map alone: retain all-case smoke fallback.
    cases=plan['cases']+plan.get('external_cases',[])
    if any(p.startswith(tuple(plan['shared_paths'])) for p in paths):return [x['id'] for x in cases]
    matched=[x['id'] for x in cases if any(p.startswith(tuple(x['paths'])) for p in paths)]
    if any(not p.endswith(('.md','.png','.jpg')) and not any(p.startswith(tuple(x['paths'])) for x in cases) for p in paths):
        return [x['id'] for x in cases]
    return matched


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base');parser.add_argument('--head',default='HEAD')
    parser.add_argument('--output',type=Path);args=parser.parse_args()
    plan=validate(json.loads((ROOT/'contracts/business_test_plan.json').read_text(encoding='utf-8')))
    changes=subprocess.check_output(['git','diff','--name-only',args.base,args.head],cwd=ROOT,text=True).splitlines() if args.base else []
    result={'plan_valid':True,'affected_cases':affected(plan,changes) if args.base else [x['id'] for x in plan['cases']+plan.get('external_cases',[])],
            'external_execution':'NOT_RUN_BY_PLAN_VALIDATION; requires frozen RAG sources',
            'changed_paths':changes,'scope':'selection aid; full business smoke remains required'}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
