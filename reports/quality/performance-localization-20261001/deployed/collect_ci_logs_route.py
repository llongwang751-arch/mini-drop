from pathlib import Path
import importlib.util
import json

stage = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('g', Path('output/quality/ci-validation-20260927/github_ci.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
c = g.session()
c.trust_env = False
jobs = json.loads((stage / 'release-r7/ci-jobs.json').read_text())['jobs']
out = stage / 'ci-logs-route'
out.mkdir(exist_ok=False)
for job in jobs:
    if job['name'] not in {'Python and Go independent CPU controls', 'Web (React)'}:
        continue
    response = c.get('https://api.github.com/repos/' + g.REPO + '/actions/jobs/' + str(job['id']) + '/logs', timeout=45)
    response.raise_for_status()
    (out / (str(job['id']) + '.log')).write_bytes(response.content)
    print(json.dumps({'job': job['name'], 'bytes': len(response.content), 'conclusion': job['conclusion']}))
