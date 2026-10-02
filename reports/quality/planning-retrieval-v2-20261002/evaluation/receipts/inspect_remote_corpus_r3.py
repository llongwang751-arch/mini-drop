"""Read actual deployed corpus identities, without any provider/index access."""
from pathlib import Path
import json,sys
STAGE=Path(__file__).parent
ROOT=STAGE.parents[2]
sys.path.insert(0,str(ROOT/'.tmp-planning-retrieval-v2-source-r3'))
from scripts.evaluate_planning_retrieval_v2 import load_remote_helper,write_json
helper=load_remote_helper(ROOT/'output/acceptance/deployment-20260930/run_strict.py')
code=r'''
import hashlib,json
from pathlib import Path
import server.app.agent_runtime.retrieval as retrieval
from server.app.agent_runtime.semantic_retrieval import corpus
root=Path(retrieval.__file__).resolve().parents[3]
knowledge=root/'knowledge'
files={p.relative_to(root).as_posix():{'bytes':len(p.read_bytes()),'raw_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
 'lf_sha256':hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
 'crlf_count':p.read_bytes().count(b'\r\n'),'lf_count':p.read_bytes().count(b'\n')}
 for p in sorted(knowledge.glob('*')) if p.suffix in {'.json','.md'}}
chunks=corpus(knowledge)
print(json.dumps({'scope':'READ_ONLY_DEPLOYED_CORPUS_NO_PROVIDER_OR_INDEX_ACCESS','module_path':str(Path(retrieval.__file__).resolve()),
 'knowledge_root':str(knowledge),'files':files,'chunks':chunks,'provider_calls_attempted':0,'index_access_attempted':False},ensure_ascii=False))
'''
outer='import subprocess\ncode='+repr(code)+'\n'
outer+="p=subprocess.run(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-B','-'],input=code,text=True,capture_output=True,timeout=30)\n"
outer+="assert p.returncode==0,'Deployed read-only corpus receipt unavailable'\nprint(p.stdout,end='')\n"
raw=helper.remote(outer);value=json.loads(raw)
write_json(STAGE/'deployed-corpus-raw-identity-r3.json',value)
print(json.dumps({k:v for k,v in value.items() if k not in {'chunks','files'}}))
print(json.dumps({k:{key:row[key] for key in ('bytes','crlf_count','lf_count')} for k,row in value['files'].items()}))
