from pathlib import Path

stage = Path(__file__).resolve().parent
old = Path('output/acceptance/performance-diagnosis-20261001')
release = stage / 'release'
release.mkdir(exist_ok=False)
for name in ('activate_platform.py', 'ci_status.py'):
    (release / name).write_bytes((old / 'release-neutral' / name).read_bytes())
prepare = (old / 'release-neutral/prepare_release.py').read_text(encoding='utf-8')
start = prepare.index("helper=(ROOT/")
end = prepare.index('paths=subprocess', start)
prepare = prepare[:start] + prepare[end:]
prepare = prepare.replace("extras=['docs/'", "extras=['demo/go-hotspot/'+name for name in ('main.go','main_test.go','go.mod','Dockerfile')]+['docs/'")
(release / 'prepare_release.py').write_text(prepare, encoding='utf-8')
deploy = (old / 'release-neutral/deploy_runtime.py').read_text(encoding='utf-8')
deploy = deploy.replace("SERVICES = ('diagnosis-worker', 'analyzer', 'web')", "SERVICES = ('diagnosis-worker', 'analyzer', 'web', 'go-hotspot')")
line = "        recipe.write_text('FROM ' + obj['Image'] + '\\n' + copies)"
assert line in deploy
deploy = deploy.replace(line, """        if service == 'go-hotspot':
            recipe = STAGE / 'source/demo/go-hotspot/Dockerfile'
        else:
            recipe.write_text('FROM ' + obj['Image'] + '\\n' + copies)""")
line = "        subprocess.run(['docker', 'build', '-f', str(recipe), '-t', image, str(STAGE / 'source')], check=True)"
assert line in deploy
deploy = deploy.replace(line, """        context = STAGE / 'source/demo/go-hotspot' if service == 'go-hotspot' else STAGE / 'source'
        subprocess.run(['docker', 'build', '-f', str(recipe), '-t', image, str(context)], check=True)
        if service == 'go-hotspot':
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sh', image,
                            '-c', 'test -x /usr/local/bin/go-hotspot'], check=True)""")
(release / 'deploy_runtime.py').write_text(deploy, encoding='utf-8')
verify = (old / 'verify_runtime.py').read_text(encoding='utf-8').replace('release-neutral/', 'release/')
(stage / 'verify_runtime.py').write_text(verify, encoding='utf-8')
collect = (old / 'collect_ci.py').read_text(encoding='utf-8').replace('release-neutral/', 'release/').replace('ci-artifacts-neutral', 'ci-artifacts')
(stage / 'collect_ci.py').write_text(collect, encoding='utf-8')
(stage / 'run_browser.py').write_bytes((old / 'run_browser.py').read_bytes())
browser = (old / 'browser_performance.mjs').read_text(encoding='utf-8').replace('performance-diagnosis-20261001/browser', 'performance-localization-20261001/browser')
(stage / 'browser_performance.mjs').write_text(browser, encoding='utf-8')
(stage / 'health-diagnosis-id.txt').write_text('insight_731431c05e5a44588cba6c4204022d54')
