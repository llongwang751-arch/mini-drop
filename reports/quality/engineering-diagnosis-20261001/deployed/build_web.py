from pathlib import Path
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET

root=Path.cwd();stage=Path(__file__).resolve().parent
release=stage/'release';head=(release/'source-head.txt').read_text().strip()
web=release/'deployment-source/web'
for name in ('package-lock.json','src/pages/AIDiagnosis.jsx','src/pages/AIDiagnosis.test.jsx','src/utils/reportPresentation.js','src/hooks/useSSE.js','src/hooks/useSSE.test.jsx','src/components/EngineeringDiagnosisSummary.jsx','src/components/EngineeringDiagnosisSummary.test.jsx','src/components/EvalPanel.jsx','src/components/FaultPlazaPanel.jsx','src/components/FaultPlazaPanel.test.jsx','public/report-assets/engineering-diagnosis/index.json'):
 path=web/name
 assert path.read_bytes()==subprocess.check_output(['git','show',head+':web/'+name]),name
def run(args,log):
 r=subprocess.run(args,cwd=web,text=True,encoding='utf-8',capture_output=True,timeout=240,shell=False)
 (stage/log).write_text(r.stdout+r.stderr,encoding='utf-8')
 assert r.returncode==0,log
run(['npm.cmd','test','--','--maxWorkers=2','--reporter=junit','--outputFile='+str(stage/'web-engineering-clean-junit.xml')], 'web-engineering-clean-test.log')
r=ET.parse(stage/'web-engineering-clean-junit.xml').getroot()
assert r.attrib['tests']=='299' and r.attrib['failures']=='0' and r.attrib['errors']=='0'
run(['npm.cmd','run','build'],'web-engineering-clean-build.log')
check=release/'check_web_bundle.mjs'
check.write_bytes(subprocess.check_output(['git','show',head+':scripts/check_web_bundle.mjs']))
run(['node',str(check),str(web/'dist')],'web-engineering-clean-bundle.log')
(stage/'web-engineering-clean-provenance.json').write_text(json.dumps({'source_head':head,'tests':299,
 'source':'git archive verified against immutable Git objects before execution',
 'lock_sha256':hashlib.sha256((web/'package-lock.json').read_bytes()).hexdigest(),
 'web_files':len([p for p in (web/'dist').rglob('*') if p.is_file()])},indent=2),encoding='utf-8')
print(json.dumps({'source':head,'tests':299,'build':'PASSED','bundle':'PASSED'}))
