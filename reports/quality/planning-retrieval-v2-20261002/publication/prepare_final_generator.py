"""Clarify a browser receipt with no downloads, preserving local annotations."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).parent
NAME = 'scripts/build_interview_delivery.py'
HEAD = '73b4b18ad83553a5012a0025dfb78bb21ff8dc7f'
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip() == HEAD
old = '''        f"| 线上浏览器 | {len(browser['verified_downloads'])} 份下载 SHA 一致、{len(browser['layouts'])} 次布局检查通过 |",'''
new = '''        (f"| 线上浏览器 | {len(browser['verified_downloads'])} 份下载 SHA 一致、{len(browser['layouts'])} 次布局检查通过 |"
         if browser['verified_downloads'] else
         f"| 线上浏览器 | 本次未执行证据下载；{len(browser['layouts'])} 次布局检查通过 |"),'''
def transform(text):
    assert text.count(old) == 1
    return text.replace(old, new, 1)
clean = transform(subprocess.check_output(['git', '-c', 'core.autocrlf=false', 'show', HEAD + ':' + NAME], cwd=ROOT).decode())
dirty = transform((ROOT / NAME).read_text(encoding='utf-8'))
target = STAGE / 'final-generator-candidate' / NAME
assert not target.exists()
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(clean, encoding='utf-8', newline='\n')
(ROOT / NAME).write_text(dirty, encoding='utf-8', newline='\n')
(STAGE / 'final-generator-source-map.json').write_text(json.dumps({'base_head': HEAD, 'files': [
    {'path': NAME, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'clean_candidate': str(target)}]}, indent=2) + '\n', encoding='utf-8')
print('Prepared exact no-download browser wording; generator checks remain unchanged.')
