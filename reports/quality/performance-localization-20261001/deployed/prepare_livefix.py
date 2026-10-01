from pathlib import Path

stage = Path(__file__).resolve().parent
old, new = stage / 'release-r3', stage / 'release-r4'
new.mkdir(exist_ok=False)
for name in ('activate_platform.py', 'ci_status.py', 'prepare_release.py'):
    (new / name).write_bytes((old / name).read_bytes())
deploy = (old / 'deploy_runtime.py').read_text(encoding='utf-8').replace(
    "SERVICES = ('diagnosis-worker', 'analyzer', 'web', 'go-hotspot')",
    "SERVICES = ('diagnosis-worker', 'analyzer', 'web')")
(new / 'deploy_runtime.py').write_text(deploy, encoding='utf-8')
for name in ('verify_runtime.py', 'collect_ci.py', 'collect_ci_logs.py'):
    text = (stage / name).read_text(encoding='utf-8').replace('release-r3/', 'release-r4/')
    if name == 'collect_ci.py':
        text = text.replace('ci-artifacts-buildfix', 'ci-artifacts-livefix')
    if name == 'collect_ci_logs.py':
        text = text.replace("stage / 'ci-logs'", "stage / 'ci-logs-livefix'")
    (stage / name).write_text(text, encoding='utf-8')
text = (stage / 'live_acceptance.py').read_text(encoding='utf-8')
text = text.replace('release-r3/', 'release-r4/').replace('strict-three-paths', 'strict-three-paths-r2')
start = text.index('downloads = stage')
text = text[:start] + "print('Fresh three-case campaign completed; grade with grade_campaign.py.', flush=True)\n"
(stage / 'live_acceptance_r2.py').write_text(text, encoding='utf-8')
