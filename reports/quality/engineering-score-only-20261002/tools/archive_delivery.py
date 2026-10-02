"""Freeze this delivery and stage its documentation without user worktree edits."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
DEST = ROOT / 'reports/quality/engineering-score-only-20261002'


def copy(source, target):
    raw = source.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    assert not target.exists(), 'Preserve existing evidence: ' + str(target)
    target.write_bytes(raw)


def stage(name, raw):
    digest = subprocess.check_output(['git', 'hash-object', '-w', '--stdin'], cwd=ROOT, input=raw).decode().strip()
    subprocess.run(['git', 'update-index', '--add', '--cacheinfo', '100644,' + digest + ',' + name], cwd=ROOT, check=True)


def update(name, transform):
    clean = subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=ROOT).decode('utf-8').replace('\r\n', '\n')
    dirty = (ROOT / name).read_text(encoding='utf-8')
    stage(name, transform(clean).encode('utf-8'))
    temporary = STAGE / 'delivery-doc.tmp'
    temporary.write_text(transform(dirty), encoding='utf-8')
    os.replace(temporary, ROOT / name)


def main():
    assert not DEST.exists(), 'Use a fresh evidence archive'
    release = json.loads((STAGE / 'final-release/manifest.json').read_text(encoding='utf-8'))
    runtime = json.loads((STAGE / 'final-publication-verification.json').read_text(encoding='utf-8'))
    browser_dir = STAGE / 'browser-r2'
    browser = json.loads((browser_dir / 'result.json').read_text(encoding='utf-8'))
    ci = json.loads((STAGE / 'ci-evidence/summary.json').read_text(encoding='utf-8'))
    assert runtime['source_head'] == release['git_head']
    assert ci['source_head'] == release['git_head'] and ci['run_id'] == release['ci_evidence']['run_id']
    assert ci['status'] == 'VERIFIED' and ci['successful_jobs'] == ci['job_count'] == 14
    assert ci['source_equivalence']['same_git_tree'] is True
    counts = ci['main_suite_counts']
    assert counts['python']['failed'] == 0 and counts['web_vitest']['passed'] == 317
    assert counts['chromium']['passed'] == 8 and counts['chromium']['legacy_score_requests'] == 0
    for row in ci['metadata_files'] + ci['logs'] + [f for a in ci['artifacts'] for f in a['files']]:
        path = STAGE / 'ci-evidence' / row['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        assert path.stat().st_size == row['bytes']
    assert runtime['ci_jobs_succeeded'] == 14
    assert runtime['retired_score_asset_verified'] and runtime['public_fault_catalog_score_fields_omitted']
    assert browser['passed'] and browser['legacy_score_ui_absent'] and browser['fault_catalog_score_fields_retired']
    assert len(browser['verified_downloads']) == 41

    for name in ('final-publication-verification.json', 'final-runtime-predeployment.json', 'backend-review.json'):
        copy(STAGE / name, DEST / 'publication' / name)
    for name in ('manifest.json', 'platform-deployment.json', 'platform-deployment.log', 'ci-run.json',
                 'ci-jobs.json', 'source-head.txt', 'release-tag.txt', 'web-build.log'):
        copy(STAGE / 'final-release' / name, DEST / 'release' / name)
    for name in ('prepare_final_release.py', 'final_preflight.py', 'verify_final_runtime.py', 'browser_verify.py',
                 'browser_verify.mjs', 'archive_delivery.py', 'ci_status.py', 'collect_ci_evidence.py'):
        copy(STAGE / name, DEST / 'tools' / name)
    for name in ('deploy_runtime.py', 'activate_platform.py'):
        copy(STAGE / 'final-release' / name, DEST / 'tools' / name)
    for path in sorted((STAGE / 'ci-evidence').rglob('*')):
        if path.is_file():
            copy(path, DEST / 'ci' / path.relative_to(STAGE / 'ci-evidence'))
    for name in ('result.json', 'network.json', 'browser.log', 'expected-public.json'):
        copy(browser_dir / name, DEST / 'browser' / name)
        first = STAGE / 'browser' / name
        if first.is_file():
            copy(first, DEST / 'browser-initial' / name)
    screenshots = list(browser_dir.glob('*retired-score-current-overview*.png'))
    assert screenshots, 'Archive the current overview screenshot'
    for path in screenshots:
        copy(path, DEST / 'browser' / path.name)
    for name in ('browser-lineage.json',):
        copy(STAGE / name, DEST / 'publication' / name)
    copy(ROOT / 'output/acceptance/retire-legacy-20261002/web-tests-full.json', DEST / 'local/web-tests-full.json')
    copy(ROOT / 'output/acceptance/retire-legacy-20261002/browser-local-r1/result.json', DEST / 'local/browser-result.json')
    copy(ROOT / 'output/acceptance/seven-gaps-20261002/retire-default-strict-score.xml', DEST / 'local/backend-regression.xml')

    head, tag, run = release['git_head'], release['release_tag'], release['ci_evidence']['run_id']
    url = f'https://github.com/llongwang751-arch/mini-drop/actions/runs/{run}'
    detail = (
        f'本次移除已发布`{tag}`，应用源码`{head}`，[精确CI{run}]({url})完成14/14作业。'
        f"Python{counts['python']['passed']}项通过/{counts['python']['skipped']}项登记跳过，"
        '前端317项、Chromium固定数据8项及真实PostgreSQL14项零跳过通过；后端相关127项本地回归通过。Worker/Analyzer各211份源码、'
        'Web容器及公网58份资源SHA一致；13容器健康、21场景inactive，环境/挂载及另外10容器、'
        '紧邻部署前Office/API/Native基线核对通过。线上API的21场景均不含旧成绩字段，旧公开地址仅返回无分数替代地址；'
        '真实浏览器确认旧成绩及入口消失，新工程21/21、具体路径6/21、反证8条、四种宽度和既有体检/业务路径通过，'
        '41份证据重新下载SHA一致。没有新建诊断或重跑故障/压测。'
        '发布回执和测试见[本次归档](../reports/quality/engineering-score-only-20261002/manifest.json)。'
    )
    anchor = '实现、测试及最终发布见[当前成绩交付](../reports/architecture/engineering-score-only-20261002.md)。'
    for name in ('PROJECT_CONTEXT', 'RESTART_HANDOFF', 'DIAGNOSIS_ACCEPTANCE', 'PERFORMANCE_DIAGNOSIS',
                 'INTERVIEW_DEMO_GUIDE', 'FAULT_PLAZA_ACCEPTANCE', 'ENGINEERING_CASES'):
        def transform(text):
            assert text.count(anchor) == 1
            return text.replace(anchor, anchor + '\n\n' + detail, 1)
        update('docs/' + name + '.md', transform)
    report = 'reports/architecture/engineering-score-only-20261002.md'
    old = '已完成代码与专项回归，正在为精确提交执行最终 CI 和发布检查。最终来源、上线回执及只读浏览器检查另存于本任务的质量记录；应用发布后补充本段。'
    audit_note = ('\n\n首轮浏览器检查脚本未解包 `code/data` API 信封，收到 HTTP 200 后按错误结构读取场景数。'
                  '仅修正验收脚本后，在独立目录重跑通过，首轮记录和失败原因保留。页面及线上代码没有为此修改。'
                  '学习指南的旧读取器职责说明通过生成器源描述修正后重新生成。')
    update(report, lambda text: text.replace(old, detail.replace('../reports/quality/', '../quality/') + audit_note, 1))
    attribute = 'reports/quality/engineering-score-only-20261002/** -text whitespace=cr-at-eol,-blank-at-eol,-blank-at-eof\n'
    update('.gitattributes', lambda text: text.rstrip() + '\n' + attribute)
    def generator_description(text):
        old_reader = '读取并校验只读挂载的验收索引，为故障广场提供真实最近验收结果。'
        old_builder = '校验完成的 Campaign 和逐场证据哈希，生成页面最近验收结果索引。'
        assert text.count(old_reader) == text.count(old_builder) == 1
        text = text.replace(old_reader, '保留历史验收索引的离线哈希校验读取器；默认故障广场 API 不再调用。', 1)
        text = text.replace(old_builder, '离线校验历史 Campaign 与逐场证据哈希；当前页面和默认 API 不使用旧成绩索引。', 1)
        description = ('    "scripts/build_performance_audit.py": "按退役合同生成旧公开地址的四字段无分数替代标记，检查归档哈希和产物漂移。",\n'
                       '    "contracts/performance_audit.json": "旧公开成绩的退役策略与冻结证据哈希源合同；限定工程索引替代地址。",\n'
                       '    "web/src/components/PerformanceDiagnosisSummary.jsx": "保留为空的兼容组件，不渲染或请求旧成绩；当前工作台不调用。",\n')
        assert '    "scripts/build_performance_audit.py":' not in text
        return text.replace('EXACT = {\n', 'EXACT = {\n' + description, 1)
    generator_name = 'scripts/generate_learning_guide_file_index.py'
    old_generator = subprocess.check_output(['git', 'show', 'HEAD:' + generator_name], cwd=ROOT).decode('utf-8')
    update(generator_name, generator_description)
    new_generator = subprocess.check_output(['git', 'show', ':' + generator_name], cwd=ROOT).decode('utf-8')
    declarations = lambda raw: [ast.dump(node) for node in ast.parse(raw).body
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    assert declarations(old_generator) == declarations(new_generator), 'Documentation descriptions only'
    (DEST / 'publication/documentation-generator-verification.json').write_text(json.dumps({
        'scope': 'Documentation source descriptions and regenerated guide; no runtime application behavior changes',
        'deployed_application_head': head, 'source_path': generator_name,
        'before_sha256': hashlib.sha256(old_generator.encode()).hexdigest(),
        'after_sha256': hashlib.sha256(new_generator.encode()).hexdigest(),
        'function_and_class_ast_unchanged': True,
    }, indent=2) + '\n', encoding='utf-8')

    files = [{'path': p.relative_to(DEST).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
              'bytes': p.stat().st_size} for p in sorted(DEST.rglob('*')) if p.is_file()]
    manifest = {'schema': 'mini-drop.evidence-manifest.v1', 'campaign_id': 'engineering-score-only-20261002',
                'source_head': head, 'deployment_tag': tag, 'ci_run': run,
                'scope': 'Retire default historical causal grades; preserve current engineering21/6/8 and 21 scenarios; read-only runtime/browser verification',
                'files': files}
    (DEST / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for p in sorted(DEST.rglob('*')):
        if p.is_file():
            stage(p.relative_to(ROOT).as_posix(), p.read_bytes())
    spec = importlib.util.spec_from_file_location('guide', ROOT / 'scripts/generate_learning_guide_file_index.py')
    guide = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guide)
    tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').strip('\0').split('\0'))
    block = guide.render([name for name in guide.current_files() if name in tracked])
    update('docs/PROJECT_LEARNING_GUIDE.md', lambda text: text.split(guide.START, 1)[0].rstrip() + '\n\n' + block
           + text.split(guide.START, 1)[1].split(guide.END, 1)[1].lstrip('\r\n'))
    print(json.dumps({'frozen_files': len(files), 'source_head': head, 'tag': tag, 'ci_run': run,
                      'docs_and_evidence_staged': True}))


if __name__ == '__main__':
    main()
