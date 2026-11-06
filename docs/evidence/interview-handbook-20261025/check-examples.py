import json
import math
import statistics
from pathlib import Path

checks = []
def check(name, actual, expected):
    ok = actual == expected if not isinstance(expected, float) else math.isclose(actual, expected, rel_tol=1e-12)
    checks.append({'name': name, 'actual': actual, 'expected': expected, 'passed': ok})
    assert ok, name

check('nearest-rank P95 position for 20 samples', math.ceil(0.95 * 20), 19)
ratios = [1.20, 1.10, 0.95, 1.25, 1.15, 1.05]
check('median of six synthetic CPU ratios', statistics.median(ratios), 1.125)
check('speedup ratio to duration reduction', 1 - 1/1.125, 1/9)
check('strict ratio threshold count', sum(r > 1.05 for r in ratios), 4)
check('synthetic sampling relative CPU increase', (2.2 - 2.0)/2.0, 0.10)
check('nominal expected sampling opportunities', 99 * 20, 1980)
check('list and tuple serialize identically', json.dumps(['red','blue']) == json.dumps(('red','blue')), True)
check('list and tuple differ in Python type', type(['red','blue']) is not type(('red','blue')), True)
cached = ['red','blue']
first = cached
first.append('caller-added')
check('shared mutable result demonstrates cross-call pollution', 'caller-added' in cached, True)
check('SHA-256 hexadecimal representation', 64 * 4, 256)

weights = {'ROOT':0, 'A':8, 'B':16, 'C':16, 'D':32}
edges = {'ROOT':['A'], 'A':['B','C'], 'B':['D'], 'C':['D'], 'D':[]}
def reachable(removed=None):
    pending = ['ROOT']
    seen = set()
    while pending:
        node = pending.pop()
        if node == removed or node in seen:
            continue
        seen.add(node)
        pending.extend(edges[node])
    return seen
def retained(node):
    lost = reachable() - reachable(node)
    return sum(weights[n] for n in lost)
check('diamond graph A retained', retained('A'), 72)
check('shared child D is not retained by B', retained('B'), 16)
check('shared child D is not retained by C', retained('C'), 16)
edges['ROOT'].append('D')
check('additional root removes D from A retained', retained('A'), 40)
check('developer mock interview minutes', sum([5,8,12,8,6,6]), 45)
check('test developer mock interview minutes', sum([5,10,10,8,10,8,6,3]), 60)

out = {'scope':'Independent arithmetic and synthetic teaching-example checks only; not application/parser/JVM tests or real benchmarks.', 'passed': all(c['passed'] for c in checks), 'checks': checks, 'model_calls':0, 'collection_runs':0, 'application_tests_run':False}
Path('.local/interview_drafts/example-checks.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'passed':out['passed'],'checks':len(checks)},ensure_ascii=False))
