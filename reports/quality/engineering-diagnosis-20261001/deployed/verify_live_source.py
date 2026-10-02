from pathlib import Path
import hashlib
import json
import subprocess

root=Path.cwd()
stage=Path(__file__).resolve().parent
live=root/'output/frontend-redesign-20261001/cloud-release'
head=(stage/'release/source-head.txt').read_text().strip()
manifest=json.loads((live/'source-manifest.json').read_text(encoding='utf-8'))
rows=[]
for name in ('web/src/components/EngineeringDiagnosisSummary.jsx','web/src/components/EngineeringDiagnosisSummary.test.jsx','web/public/report-assets/engineering-diagnosis/index.json'):
    blob=subprocess.check_output(['git','show',head+':'+name])
    raw=(live/'source'/name).read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    assert digest==manifest['source_files'][name]
    assert raw.replace(b'\r\n',b'\n')==blob.replace(b'\r\n',b'\n'),name
    rows.append({'path':name,'git_blob_sha256':hashlib.sha256(blob).hexdigest(),'live_source_sha256':digest,
                 'feature_matches':True,'difference':'NONE' if raw==blob else 'EOL_ONLY'})
expected=json.loads(subprocess.check_output(['git','show',head+':web/public/report-assets/engineering-diagnosis/index.json']))
assert json.loads((stage/'live-index-before.json').read_text(encoding='utf-8'))==expected
(stage/'live-source-comparison.json').write_text(json.dumps({'exact_feature_source':head,
    'live_release':manifest['release_tag'],'live_source_kind':manifest['source_kind'],
    'matching_feature_files':rows,'live_index_matches_generated_semantics':True,
    'boundary':'Shared EvalPanel/FaultPlazaPanel include separate visual changes. Live Web is a frozen worktree build, not the exact Git commit.'},indent=2),encoding='utf-8')
dest=stage/'live-release'
dest.mkdir(exist_ok=False)
for name in ('source-manifest.json','manifest.json','deployment.json','public-verification.json','test-junit.xml','test.log','build.log','bundle.log'):
    p=live/name
    if p.is_file():(dest/name).write_bytes(p.read_bytes())
script=(stage/'verify_runtime.py').read_text(encoding='utf-8')
script=script.replace("(stage/'release/manifest.json')", "(stage/'live-release/manifest.json')")
script=script.replace("office=json.loads", "web['deployment_files']=web['files']\nweb['git_head']=None\noffice=json.loads")
script=script.replace("assert result['legacy_root_count']==0", "assert result['legacy_root_count']==0\nresult['feature_source']=(stage/'release/source-head.txt').read_text().strip()\nresult['web_source_kind']=web['source_kind']\n(stage/'final-runtime-verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')")
(stage/'verify_runtime.py').write_text(script,encoding='utf-8')
print(json.dumps({'feature_source':head,'live_release':manifest['release_tag'],'files':rows,
    'deployment':'Already published by parallel frontend task; verified in place, no stale source activation.'}))
