from pathlib import Path

stage = Path(__file__).resolve().parent
old = stage / 'release-r2'
new = stage / 'release-r3'
new.mkdir(exist_ok=False)
for name in ('activate_platform.py', 'ci_status.py', 'prepare_release.py', 'deploy_runtime.py'):
    (new / name).write_bytes((old / name).read_bytes())
for name in ('verify_runtime.py', 'verify_go.py', 'live_acceptance.py', 'collect_ci.py'):
    path = stage / name
    text = path.read_text(encoding='utf-8').replace('release-r2/', 'release-r3/')
    if name == 'collect_ci.py':
        text = text.replace("stage/'ci-artifacts'", "stage/'ci-artifacts-buildfix'")
    path.write_text(text, encoding='utf-8')
