import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path.cwd()
BASE = ROOT / 'docs/diagrams/drop-system'

def read(name):
    return json.loads((BASE / name).read_text(encoding='utf-8'))

def fingerprint(path):
    data = path.read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}

def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)

atlas = read('atlas-data.json')
catalog = json.loads((ROOT / 'contracts/task-kinds.json').read_text(encoding='utf-8'))
collectors = read('collectors.json')
assert {x['id'] for x in catalog} == {x['id'] for x in collectors['collectors'] + collectors['special_cases']}
assert len(collectors['collectors']) == 16
review_files = ['root-visual-review.json', 'collection-visual-review.json', 'ai-visual-review.json', 'state-visual-review.json', '13-visual-review.json', '14-visual-review.json']
reviews = {name: read(name) for name in review_files}
png_report = read('read-png-receipt.json')
assert png_report['passed']
portal = read('portal-browser-check.json')
assert portal['passed'] and not portal['errors'] and not portal['network']
assert portal['artifact'] == fingerprint(BASE / 'index.html')
files = {'index.html', 'index.template.html', 'build-atlas.py', 'README.md', 'atlas-data.json', 'collectors.json', 'ai-facts.json', 'state-facts.json', 'business-signal-facts.json', 'observability-facts.json', 'source-manifest.json', 'portal-browser-check.json', 'read-png-receipt.json', *review_files}
report = {
    'schema_version': 1,
    'date': '2026-10-24',
    'status': 'passed-for-default-read',
    'scope': 'Current workspace documentation and static diagrams only. Deterministic delivery, automated browser evidence and perceptual screenshot review are independent.',
    'production_actions': False,
    'model_calls': 0,
    'collector_runs': 0,
    'application_runtime_changes': False,
    'diagrams': [],
}
for d in atlas['diagrams']:
    stem = d['file']
    spec = read(stem + '.json')
    delivery_name = next(stem+s for s in ('.delivery.json', '.receipt.json', '.deliver.json') if (BASE/(stem+s)).is_file())
    delivery = read(delivery_name)
    assert delivery['ok'] and delivery['specification'] == fingerprint(BASE / (stem+'.json')) and delivery['artifact'] == fingerprint(BASE / (stem+'.html')), stem
    assert delivery['validation']['checksPassed'] == 9 and delivery['validation']['checkCount'] == 9
    assert delivery['validation']['errors'] == 0 and delivery['validation']['warnings'] == 0
    assert delivery['validation']['compositionProfile'] == 'showcase'
    count = len(spec.get('components', spec.get('participants', spec.get('nodes', []))))
    assert 1 <= count <= 12, (stem, count)
    browser_name = stem+'.visual-check.json'
    browser = read(browser_name)
    assert browser['status'] == 'pass' and browser['ok'] and browser['artifact']['sha256'] == delivery['artifact']['sha256'], stem
    assert browser['visualReview'] == 'pending'
    assert len(browser['containment']['viewports']) == 4 and all(v['ok'] for v in browser['containment']['viewports'])
    assert browser['captures']['status'] == 'pass' and len(browser['captures']['screenshots']) == 4
    review_match = None
    for name, document in reviews.items():
        for entry in objects(document):
            if entry.get('artifact', {}).get('sha256') == delivery['artifact']['sha256'] and entry.get('specification', {}).get('sha256') == delivery['specification']['sha256'] and 'visual_review' in entry:
                visual = entry['visual_review']
                status = visual.get('status') if isinstance(visual, dict) else visual
                if status == 'passed':
                    rounds = visual.get('correction_rounds', entry.get('correction_rounds', 0)) if isinstance(visual, dict) else entry.get('correction_rounds', 0)
                    assert 0 <= rounds <= 2
                    review_match = {'receipt': name, 'status': status, 'correction_rounds': rounds}
                    break
        if review_match:
            break
    assert review_match, 'No current perceptual review for '+stem
    png = next(p for p in png_report['diagrams'] if p['id'] == d['id'])
    assert png['artifact_sha256'] == delivery['artifact']['sha256']
    assert {k: png['png'][k] for k in ('sha256','bytes')} == fingerprint(BASE / png['png']['path'])
    report['diagrams'].append({'id': d['id'], 'title': d['title'], 'diagram_type': spec['diagram_type'], 'primary_entities': count, 'output': str(BASE / (stem+'.html')), 'specification': delivery['specification'], 'artifact': delivery['artifact'], 'validation': delivery['validation'], 'delivery_receipt': delivery_name, 'browser_evidence': {'status': 'passed', 'receipt': browser_name, 'viewports': len(browser['containment']['viewports']), 'captures': len(browser['captures']['screenshots'])}, 'visual_review': review_match, 'read_png': png['png']})
    files.update({stem+'.json', stem+'.html', delivery_name, browser_name, stem+'.visual-check.html', png['png']['path']})
    files.update(s['file'] for s in browser['captures']['screenshots'])
manifest = read('source-manifest.json')
for source in manifest['files']:
    assert {k: source[k] for k in ('sha256','bytes')} == fingerprint(ROOT / source['path']), source['path']
    assert (BASE / source['snapshot']).is_file()
    files.add(source['snapshot'])
report['source_evidence'] = {'receipt': 'source-manifest.json', 'files': len(manifest['files']), 'distinct_path_lines': sum(len(s['referenced_lines']) for s in manifest['files']), 'base_revision': manifest['base_revision'], 'binding': fingerprint(BASE/'source-manifest.json')}
report['catalog'] = {'catalog_entries': len(catalog), 'documented_entries': len(collectors['collectors'])+len(collectors['special_cases']), 'agent_runners': 16, 'contract_binding': fingerprint(ROOT/'contracts/task-kinds.json')}
report['portal'] = {'receipt': 'portal-browser-check.json', 'checks_passed': len(portal['checks']), 'page_errors': len(portal['errors']), 'external_requests': len(portal['network']), 'artifact': fingerprint(BASE/'index.html')}
report['summary'] = {'diagrams': len(report['diagrams']), 'deterministic_checks_passed': 9*len(report['diagrams']), 'browser_viewports_passed': 4*len(report['diagrams']), 'browser_screenshots_reviewed': 4*len(report['diagrams']), 'read_pngs': len(png_report['diagrams'])}
report['limitations'] = [
    'Visual pass covers default READ/Still screenshots, not every interaction on every diagram.',
    'Canonical fine and Share Card exports on long-note sequence diagrams have observed overlap/right-edge clipping. Generation and UI cleanliness passed, fine typography did not. Use supplied read-png images for clear static diagrams.',
    'Viewer route/map/search/passport/SVG/PNG/Share Card operations were exercised on diagram03 only; see collection-visual-review.json.',
    'No live collection, model invocation, production recovery/deployment test or changed application build was run for this diagram-only task.'
]
(BASE/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
files.add('validation.json')
files.update(p.relative_to(BASE).as_posix() for p in BASE.glob('atlas-*.png'))
files.update(p.relative_to(BASE).as_posix() for p in (BASE/'evidence').rglob('*') if p.is_file())
archive=BASE.parent/'drop-system-atlas.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as package:
    for name in sorted(files):
        path=BASE/name
        assert path.is_file(), name
        package.write(path, 'drop-system/'+name)
with zipfile.ZipFile(archive) as package:
    assert package.testzip() is None
    assert len(package.namelist()) == len(files)
    assert not any('candidate-history/' in p or '..' in Path(p).parts for p in package.namelist())
package_receipt={'archive':str(archive),'artifact':fingerprint(archive),'entries':len(files),'excluded':'Rejected candidates, old overflow screenshots, stale diagnostics and temporary files','zip_crc':'passed'}
(BASE/'package-receipt.json').write_text(json.dumps(package_receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(archive.parent/(archive.name+'.sha256')).write_text(package_receipt['artifact']['sha256']+'  '+archive.name+'\n',encoding='utf-8')
print(json.dumps({'summary':report['summary'],'sources':report['source_evidence']['files'],'source_lines':report['source_evidence']['distinct_path_lines'],'portal_checks':len(portal['checks']),'package':package_receipt},ensure_ascii=False))
