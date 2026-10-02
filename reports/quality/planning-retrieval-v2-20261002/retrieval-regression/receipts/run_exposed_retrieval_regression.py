"""One retrieval-only regression on exposed v2 questions; never call chat."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,os,subprocess,sys
STAGE=Path(__file__).parent
ROOT=STAGE.parents[2]
LABEL='REGRESSION_ON_EXPOSED_V2_QUESTIONS_NOT_NEW_BLIND'
HELPER=ROOT/'output/acceptance/deployment-20260930/run_strict.py'
sha=lambda b:hashlib.sha256(b).hexdigest()
def write(path,value):
 with path.open('xb') as stream:stream.write((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode())
def copy_tree(source,dest):
 assert not dest.exists();dest.mkdir(parents=True)
 for path in sorted(source.rglob('*')):
  if not path.is_file():continue
  target=dest/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
  with target.open('xb') as stream:stream.write(path.read_bytes())
  assert sha(target.read_bytes())==sha(path.read_bytes())
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--source-root',type=Path,required=True)
 parser.add_argument('--source-head',required=True)
 parser.add_argument('--deployed-release',required=True)
 parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();clean=args.source_root.resolve();out=args.output.resolve()
 assert clean.is_relative_to(ROOT.resolve()) and out.is_relative_to(STAGE.resolve())
 assert not out.exists() and len(args.source_head)==40
 sys.path.insert(0,str(clean))
 from scripts import evaluate_planning_retrieval_v2 as evaluation
 manifest,questions=evaluation.validate_freeze()
 sources=evaluation.source_receipt()
 for name,row in sources.items():
  gitraw=subprocess.run(['git','show',args.source_head+':'+name],cwd=ROOT,check=True,capture_output=True).stdout
  assert sha(gitraw)==row['sha256'],'Clean source differs from designated exact Git source: '+name
 original_manifest=ROOT/'reports/quality/planning-retrieval-v2-20261002/evaluation/archive-manifest.json'
 initial=json.loads(original_manifest.read_bytes())
 assert initial['source_head']=='df0d3ef00a945e7d909f73868819799b2b7cc7f7'
 assert evaluation.MANIFEST_SHA==initial['frozen_manifest_sha256']
 out.mkdir(parents=True)
 write(out/'EXPOSURE.json',{'schema':'mini-drop.exposed-retrieval-regression-scope.v2','label':LABEL,
  'scope':'CURRENT_PRODUCTION_RETRIEVAL_ONLY_AFTER_PUBLIC_FIRST_RUN_FAILURE_ANALYSIS',
  'started_at_utc':datetime.now(timezone.utc).isoformat(),'source_head':args.source_head,
  'deployed_release':args.deployed_release,'first_source_head':initial['source_head'],
  'first_archive_manifest_sha256':sha(original_manifest.read_bytes()),
  'manifest_sha256':evaluation.MANIFEST_SHA,'question_count':24,'new_blind_evaluation':False,
  'chat_calls_attempted':0,'actual_tasks_dispatched':0,'actual_faults_injected':0,
  'index_creation_attempted':False,'questions_or_truth_or_grader_changed':False})
 parent=clean/'output/acceptance/planning-retrieval-v2-exposed-regression'
 assert not parent.exists();parent.mkdir(parents=True)
 env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONDONTWRITEBYTECODE']='1'
 for backend in ('BM25','HYBRID'):
  name=backend.casefold();command=[sys.executable,'-B',str(clean/'scripts/evaluate_planning_retrieval_v2.py'),
   '--retrieval-only','--backend',backend,'--remote-provider-helper',str(HELPER),'--output',str(parent/name)]
  with (out/(name+'.stdout.log')).open('xb') as stdout,(out/(name+'.stderr.log')).open('xb') as stderr:
   process=subprocess.run(command,cwd=clean,env=env,stdout=stdout,stderr=stderr)
  assert process.returncode==0,'Preserve failed raw trace; do not automatically repeat retrieval'
  copy_tree(parent/name,out/name)
  report=json.loads((out/name/'report.json').read_bytes())
  assert report['chat_calls_attempted']==0 and report['manifest_sha256']==evaluation.MANIFEST_SHA
  print(json.dumps({'label':LABEL,'backend':backend,'metrics':report['metrics'],'actual_backend_counts':report['actual_backend_counts']}),flush=True)
 assert sources==evaluation.source_receipt()
 summary={'schema':'mini-drop.exposed-retrieval-regression-summary.v2','label':LABEL,
  'status':'COMPLETED','source_head':args.source_head,'deployed_release':args.deployed_release,
  'new_blind_evaluation':False,'chat_calls_attempted':0,'questions_or_truth_or_grader_changed':False,
  'first_bm25_metrics':initial['model_and_bm25_metrics'],'first_hybrid_metrics':initial['hybrid_metrics'],
  'regression_metrics':{name:json.loads((out/name/'report.json').read_bytes())['metrics'] for name in ('bm25','hybrid')},
  'original_first_archive_unchanged_sha256':sha(original_manifest.read_bytes()),
  'files':{path.relative_to(out).as_posix():{'sha256':sha(path.read_bytes()),'bytes':path.stat().st_size}
   for path in sorted(out.rglob('*')) if path.is_file()}}
 write(out/'regression-summary.json',summary)
 print(json.dumps({'label':LABEL,'status':'COMPLETED','summary':str(out/'regression-summary.json')}),flush=True)
if __name__=='__main__':main()
