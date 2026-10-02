"""Verify exposed regression raw records; no chat or semantic ranking rerun."""
from pathlib import Path
import argparse,hashlib,json,sys,tempfile
ROOT=Path(__file__).resolve().parents[3]
sha=lambda b:hashlib.sha256(b).hexdigest()
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--source-root',type=Path,required=True)
 parser.add_argument('--output',type=Path,required=True)
 parser.add_argument('--source-head',required=True)
 args=parser.parse_args();clean=args.source_root.resolve();out=args.output.resolve()
 assert clean.is_relative_to(ROOT.resolve()) and out.is_relative_to(ROOT.resolve())
 sys.path.insert(0,str(clean))
 from scripts import evaluate_planning_retrieval_v2 as evaluation
 from server.app.agent_runtime.relevance import query_profile,assess_relevance
 from server.app.agent_runtime.semantic_retrieval import corpus
 label='REGRESSION_ON_EXPOSED_V2_QUESTIONS_NOT_NEW_BLIND'
 scope=json.loads((out/'EXPOSURE.json').read_bytes())
 assert scope['label']==label and scope['source_head']==args.source_head
 assert scope['new_blind_evaluation'] is False and scope['chat_calls_attempted']==0
 manifest,questions=evaluation.validate_freeze();sources=evaluation.source_receipt()
 helper=evaluation.load_remote_helper(ROOT/'output/acceptance/deployment-20260930/run_strict.py')
 code=r'''
import hashlib,json
from pathlib import Path
import server.app.agent_runtime.retrieval as retrieval
from server.app.agent_runtime.semantic_retrieval import corpus
root=Path(retrieval.__file__).resolve().parents[3]
names=('server/app/agent_runtime/retrieval.py','server/app/agent_runtime/relevance.py','server/app/agent_runtime/semantic_retrieval.py')
print(json.dumps({'scope':'READ_ONLY_DEPLOYED_CORPUS_NO_PROVIDER_OR_INDEX_ACCESS',
 'runtime_source_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names},
 'files':{p.relative_to(root).as_posix():{'bytes':len(p.read_bytes()),'raw_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
 'lf_sha256':hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()} for p in sorted((root/'knowledge').glob('*')) if p.suffix in {'.md','.json'}},
 'chunks':corpus(root/'knowledge'),'provider_calls_attempted':0,'index_access_attempted':False},ensure_ascii=False))
'''
 outer='import subprocess\ncode='+repr(code)+'\n'
 outer+="p=subprocess.run(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-B','-'],input=code,text=True,capture_output=True,timeout=30)\n"
 outer+="assert p.returncode==0,'Deployed corpus identity unavailable'\nprint(p.stdout,end='')\n"
 deployed=json.loads(helper.remote(outer))
 assert deployed['provider_calls_attempted']==0 and deployed['index_access_attempted'] is False
 for name,digest in deployed['runtime_source_sha256'].items():assert sources[name]['sha256']==digest
 assert set(deployed['files'])==set(manifest['corpus_files'])
 line_endings={}
 with tempfile.TemporaryDirectory(prefix='mini-drop-exposed-public-corpus-') as temp:
  owned=Path(temp).resolve()
  for name,digest in manifest['corpus_files'].items():
   lf=(clean/name).read_bytes().replace(b'\r\n',b'\n');remote=deployed['files'][name]
   assert sha(lf)==digest==remote['lf_sha256']
   possibilities={'LF':lf,'CRLF':lf.replace(b'\n',b'\r\n')}
   matches=[(kind,raw) for kind,raw in possibilities.items() if sha(raw)==remote['raw_sha256']]
   assert len(matches)==1
   kind,raw=matches[0];assert len(raw)==remote['bytes'];line_endings[name]=kind
   path=owned/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
  chunks=corpus(owned/'knowledge');assert chunks==deployed['chunks']
 chunk_by_id={chunk['chunk_id']:chunk for chunk in chunks}
 catalog={row['knowledge_id']:row for row in json.loads((clean/'knowledge/catalog.json').read_bytes())}
 public_entries=[catalog[kid] for kid in {chunk['knowledge_id'] for chunk in chunks}]
 private=json.loads((clean/(evaluation.PREFIX+'private.json')).read_bytes())
 reviewed={}
 for backend in ('bm25','hybrid'):
  directory=out/backend;report=json.loads((directory/'report.json').read_bytes())
  assert report['requested_backend']==backend.upper() and report['manifest_sha256']==evaluation.MANIFEST_SHA
  for key in ('chat_calls_attempted','actual_tasks_dispatched','actual_faults_injected'):assert report[key]==0
  assert report['index_creation_attempted'] is False and report['knowledge_is_evidence'] is False
  expected={'records/'+case['case_id']+'.json' for case in questions['cases']}|{'implementation-source.json','provenance.json'}
  assert set(report['evidence_sha256'])==expected
  for name,digest in report['evidence_sha256'].items():
   path=(directory/name).resolve();assert path.is_relative_to(directory.resolve()) and sha(path.read_bytes())==digest
  assert json.loads((directory/'implementation-source.json').read_bytes())==sources
  records=[]
  for case in questions['cases']:
   row=json.loads((directory/'records'/(case['case_id']+'.json')).read_bytes())
   assert row['case_id']==case['case_id'] and row['query']==case['query']
   if backend=='bm25':
    assert row=={'case_id':case['case_id'],'query':case['query'],**evaluation.bm25_record(case['query'])}
   else:
    trace=row['trace'];assert trace['query']==trace['relevance_query']==case['query']
    assert trace['query_hash']==trace['relevance_query_hash']==sha(case['query'].encode())
    assert trace['query_profile']==query_profile(case['query'])
    assert trace['evidence_contract']['is_evidence'] is False and trace['top_k']==3
    assert trace['matched_count']==len(trace['matches'])<=3
    assert trace['outcome']==('MATCHED' if trace['matches'] else 'NO_RELEVANT_KNOWLEDGE')
    assert row['matched_ids']==list(dict.fromkeys(match['knowledge_id'] for match in trace['matches']))[:3]
    for match in trace['matches']:
     chunk=chunk_by_id[match['chunk_id']]
     for key in ('knowledge_id','document','content_hash','excerpt'):assert match[key]==chunk[key]
     assert match['relevance']==assess_relevance(case['query'],catalog[match['knowledge_id']],catalog_entries=public_entries)
     assert match['relevance']['accepted'] is True
   records.append(row)
  metrics,cases=evaluation.retrieval_metrics(questions,private,records)
  assert (metrics,cases)==(report['metrics'],report['cases'])
  reviewed[backend]={'report_sha256':sha((directory/'report.json').read_bytes()),'metrics':metrics}
 first=ROOT/'reports/quality/planning-retrieval-v2-20261002/evaluation'
 original=json.loads((first/'archive-manifest.json').read_bytes())
 assert sha((first/'archive-manifest.json').read_bytes())==scope['first_archive_manifest_sha256']
 for name,row in original['files'].items():assert sha((first/name).read_bytes())==row['sha256'] and (first/name).stat().st_size==row['bytes']
 evaluation.write_json(out/'deployed-corpus-identity.json',deployed)
 receipt={'schema':'mini-drop.independent-exposed-retrieval-review.v2','status':'VERIFIED','label':label,
  'source_head':args.source_head,'new_blind_evaluation':False,'provider_calls_attempted':0,
  'scope':'EXACT_SOURCE_RAW_BM25_REPLAY_HYBRID_ADMISSION_AND_RESCORE_NOT_RERUN_SEMANTIC_RANKING',
  'deployed_corpus_line_endings':line_endings,'independent_chunk_count':len(chunks),
  'first_archive_all_original_bytes_unchanged':True,'reviewed':reviewed}
 evaluation.write_json(out/'independent-audit.json',receipt);print(json.dumps(receipt))
if __name__=='__main__':main()
