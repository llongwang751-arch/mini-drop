"""Assemble an offline atlas; no server, collection or model calls."""
import hashlib
import json
import re
import html
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
atlas = json.loads((HERE / 'atlas-data.json').read_text(encoding='utf-8'))
collectors = json.loads((HERE / 'collectors.json').read_text(encoding='utf-8'))
types = {}
dimensions = {}
for diagram in atlas['diagrams']:
    spec_bytes = (HERE / (diagram['file'] + '.json')).read_bytes()
    spec = json.loads(spec_bytes)
    artifact_bytes = (HERE / (diagram['file'] + '.html')).read_bytes()
    receipts = [HERE / (diagram['file'] + suffix) for suffix in ('.delivery.json', '.receipt.json', '.deliver.json')]
    receipt = next((json.loads(r.read_text(encoding='utf-8')) for r in receipts if r.is_file()), None)
    if not receipt or not receipt.get('ok'):
        raise ValueError(f"No successful delivery for {diagram['file']}")
    assert receipt['specification']['sha256'] == hashlib.sha256(spec_bytes).hexdigest(), diagram['file']
    assert receipt['artifact']['sha256'] == hashlib.sha256(artifact_bytes).hexdigest(), diagram['file']
    types[diagram['file']] = spec['diagram_type']
    svg = re.search(r'<svg\b[^>]*\bviewBox="0 0 ([\d.]+) ([\d.]+)"', artifact_bytes.decode('utf-8'))
    if not svg:
        raise ValueError(f"No SVG bounds for {diagram['file']}")
    dimensions[diagram['id']] = [float(svg[1]), float(svg[2])]
data = {'atlas': atlas, 'collectors': collectors, 'types': types, 'dimensions': dimensions}
facts = [json.loads((HERE / name).read_text(encoding='utf-8')) for name in ('ai-facts.json', 'state-facts.json', 'business-signal-facts.json', 'observability-facts.json') if (HERE / name).is_file()]
references = {}

def gather(value):
    if isinstance(value, dict):
        if isinstance(value.get('path'), str) and isinstance(value.get('line'), int):
            references.setdefault(value['path'], set()).add(value['line'])
        for child in value.values():
            gather(child)
    elif isinstance(value, list):
        for child in value:
            gather(child)

gather([atlas, collectors, *facts])
gather([{'path': m['path'], 'line': 1} for m in atlas['modules']])
manifest = {'schema_version': 1, 'scope': 'Current workspace bytes; base revision is provenance, not a claim that dirty files equal Git HEAD.', 'base_revision': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(), 'files': []}
for relative, lines in sorted(references.items()):
    source = (ROOT / relative).resolve()
    if not source.is_relative_to(ROOT) or not source.is_file():
        raise ValueError(f'Invalid source: {relative}')
    source_bytes = source.read_bytes()
    source_lines = source_bytes.decode('utf-8').splitlines()
    if any(n < 1 or n > len(source_lines) for n in lines):
        raise ValueError(f'Invalid line: {relative}')
    sha = hashlib.sha256(source_bytes).hexdigest()
    out = HERE / 'sources' / (relative + '.html')
    out.parent.mkdir(parents=True, exist_ok=True)
    back = os.path.relpath(HERE / 'index.html', out.parent).replace('\\', '/')
    items = ''.join(f'<li id="L{i}"><a class="ln" href="#L{i}" aria-label="第 {i} 行">{i}</a><code>{html.escape(line) or " "}</code></li>' for i, line in enumerate(source_lines, 1))
    title = html.escape(relative)
    source_html = f'''<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · Drop 源码快照</title><style>body{{margin:0;background:#f7f9fc;color:#182c40;font:14px/1.7 Consolas,"Microsoft YaHei",monospace}}header{{padding:20px 28px;background:#fff;border-bottom:1px solid #d5deea;overflow-wrap:anywhere}}h1{{font-size:17px;margin:10px 0}}p{{font-size:12px;color:#4d657e}}a{{color:#145cc4}}ol{{list-style:none;margin:0;padding:20px 0;min-width:max-content}}li{{display:flex;padding-right:24px;min-height:23px}}li:target{{background:#e0f4c9;outline:1px solid #78a54f}}.ln{{display:inline-block;flex:none;width:76px;padding-right:20px;text-align:right;color:#63778c;text-decoration:none;user-select:none}}code{{white-space:pre}}@media(prefers-color-scheme:dark){{body{{background:#101b27;color:#e1eaf4}}header{{background:#162433;border-color:#34485c}}p,.ln{{color:#b6c7d8}}a{{color:#8cbcff}}li:target{{background:#25472d}}}}</style><header><a href="{html.escape(back)}#about">← 回到图册</a><h1>{title}</h1><p>本地工作区只读快照 · SHA-256 {sha}<br>基础提交 {manifest['base_revision']}；此文件可能包含未提交修改，以 SHA 为准。点击行号可定位和复制链接。</p></header><ol>{items}</ol></html>'''
    out.write_text(source_html, encoding='utf-8')
    manifest['files'].append({'path': relative, 'sha256': sha, 'bytes': len(source_bytes), 'line_count': len(source_lines), 'referenced_lines': sorted(lines), 'snapshot': out.relative_to(HERE).as_posix()})
(HERE / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
template = (HERE / 'index.template.html').read_text(encoding='utf-8')
assert template.count('__ATLAS_DATA__') == 1
(HERE / 'index.html').write_text(template.replace('__ATLAS_DATA__', payload), encoding='utf-8')
print(f"Atlas: {len(atlas['diagrams'])} diagrams / {len(collectors['collectors'])} collectors")
