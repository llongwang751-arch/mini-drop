import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from server.app.drop_insight.diagnosis_agent import normalize_diagnosis_plan_for_display
from server.app.drop_insight.performance_criteria import performance_observation_plan

old = subprocess.check_output(['git', 'show', '3c3c87881124526605272818d8ff384bc0151fc3:server/app/drop_insight/diagnosis_agent.py'])
tree = ast.parse(old.decode('utf-8'))
names = {'_CJK_RE', '_LATIN_WORD_RE', '_is_chinese_first_text', 'normalize_diagnosis_plan_for_display'}
nodes = [n for n in tree.body if getattr(n, 'name', '') in names or
         isinstance(n, ast.Assign) and any(getattr(t, 'id', '') in names for t in n.targets)]
namespace = {'re': re, 'Any': Any}
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<frozen-old-source>', 'exec'), namespace)
plan = performance_observation_plan('IO_LATENCY')
proposal = {'tool_name': 'collect_sys_metrics', 'reasoning_summary': 'Inspect observed performance',
            'hypotheses': [{'statement': 'Observe synchronous I/O', 'rationale': 'Use real measurements',
                            'expected_observations': plan['expected'], 'falsification_criteria': plan['falsification']}]}
before = namespace['normalize_diagnosis_plan_for_display'](proposal, plan)
after = normalize_diagnosis_plan_for_display(proposal, plan)
assert before['hypotheses'][0]['expected_observations'] != plan['expected']
assert after['hypotheses'][0]['expected_observations'] == plan['expected']
assert after['hypotheses'][0]['falsification_criteria'] == plan['falsification']
result = {'scope': 'Offline frozen-source replay, not a new live acceptance',
          'old_source_sha256': hashlib.sha256(old).hexdigest(),
          'new_source_sha256': hashlib.sha256(Path('server/app/drop_insight/diagnosis_agent.py').read_bytes()).hexdigest(),
          'input': {'proposal': proposal, 'rule_plan': plan}, 'before': before, 'after': after}
Path(__file__).with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print('Frozen source loses numeric criteria; current source preserves all declared criteria.')
