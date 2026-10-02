from pathlib import Path
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET

root=Path.cwd();stage=Path(__file__).resolve().parent
release=stage/'release-r6';head=(release/'source-head.txt').read_text().strip()
web=release/'deployment-source/web'
for name in subprocess.check_output(['git','ls-tree','-r','--name-only',head,'web']).decode().splitlines():
 path=release/'deployment-source'/name
 raw=path.read_bytes();blob=subprocess.check_output(['git','show',head+':'+name])
 if raw!=blob:
  # Windows Git archive applies text EOL conversion. Restore exact Git bytes
  # only after proving that EOL conversion is the sole difference.
  assert raw.replace(b'\r\n',b'\n')==blob.replace(b'\r\n',b'\n'),name
  temporary=release/'export-work';temporary.write_bytes(blob);os.replace(temporary,path)
 assert path.read_bytes()==blob,name
def run(args,log):
 r=subprocess.run(args,cwd=web,text=True,encoding='utf-8',capture_output=True,timeout=240,shell=False)
 (stage/log).write_text(r.stdout+r.stderr,encoding='utf-8')
 assert r.returncode==0,log
run(['npm.cmd','test','--','--maxWorkers=2','--reporter=junit','--outputFile='+str(stage/'web-clean-junit.xml')], 'web-clean-test.log')
r=ET.parse(stage/'web-clean-junit.xml').getroot()
assert r.attrib['tests']=='289' and r.attrib['failures']=='0' and r.attrib['errors']=='0'
run(['npm.cmd','run','build'],'web-clean-build.log')
check=release/'check_web_bundle.mjs'
check.write_bytes(subprocess.check_output(['git','show',head+':scripts/check_web_bundle.mjs']))
run(['node',str(check),str(web/'dist')],'web-clean-bundle.log')
(stage/'web-clean-provenance.json').write_text(json.dumps({'source_head':head,'tests':289,
 'source':'git archive verified against immutable Git objects before execution',
 'lock_sha256':hashlib.sha256((web/'package-lock.json').read_bytes()).hexdigest(),
 'web_files':len([p for p in (web/'dist').rglob('*') if p.is_file()])},indent=2),encoding='utf-8')
print(json.dumps({'source':head,'tests':289,'build':'PASSED','bundle':'PASSED'}))
