from pathlib import Path

stage = Path(__file__).resolve().parent
old, new = stage / 'release-r3', stage / 'release-r5'
new.mkdir(exist_ok=False)
for name in ('activate_platform.py', 'ci_status.py', 'prepare_release.py', 'deploy_runtime.py'):
    (new / name).write_bytes((old / name).read_bytes())
for name in ('verify_runtime.py', 'collect_ci.py', 'collect_ci_logs.py'):
    text = (stage / name).read_text(encoding='utf-8').replace('release-r4/', 'release-r5/')
    if name == 'collect_ci.py':
        text = text.replace('ci-artifacts-livefix', 'ci-artifacts-final').replace("('python','postgres','web')", "('python','postgres','web','hotspot')")
    if name == 'collect_ci_logs.py':
        text = text.replace('ci-logs-livefix', 'ci-logs-final')
    (stage / name).write_text(text, encoding='utf-8')
text = (stage / 'verify_go.py').read_text(encoding='utf-8').replace('release-r3/', 'release-r5/')
(stage / 'verify_go.py').write_text(text, encoding='utf-8')
text = (stage / 'live_acceptance_r2.py').read_text(encoding='utf-8').replace('release-r4/', 'release-r5/')
(stage / 'live_acceptance_r2.py').write_text(text, encoding='utf-8')
