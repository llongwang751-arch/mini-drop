"""Verify immutable deployed-hybrid receipts and scores without provider calls."""
from pathlib import Path
import hashlib,json,sys,tempfile
ROOT=Path(__file__).resolve().parents[3]
STAGE=Path(__file__).parent
CLEAN=ROOT/'.tmp-planning-retrieval-v2-source-r3'
sys.path.insert(0,str(CLEAN))
from scripts import evaluate_planning_retrieval_v2 as evaluation
from server.app.agent_runtime.relevance import assess_relevance,query_profile
from server.app.agent_runtime.semantic_retrieval import corpus
sha=lambda b:hashlib.sha256(b).hexdigest()
def verify(directory):
 report=json.loads((directory/'report.json').read_bytes());manifest,questions=evaluation.validate_freeze()
 assert report['schema']=='mini-drop.readonly-retrieval-evaluation.v2'
 assert report['scope']=='FROZEN_PUBLIC_KNOWLEDGE_RETRIEVAL_ONLY'
 assert report['manifest_sha256']==evaluation.MANIFEST_SHA
 assert report['metric_arithmetic']==evaluation.METRIC_ARITHMETIC
 assert report['requested_backend']=='HYBRID'
 for key in ['chat_calls_attempted','actual_tasks_dispatched','actual_faults_injected']:assert type(report[key]) is int and report[key]==0
 for key in ['index_creation_attempted','knowledge_is_evidence']:assert report[key] is False
 assert report['cost_usd'] is None and report['retrieval_provider_token_usage'] is None
 expected={'records/'+c['case_id']+'.json' for c in questions['cases']}|{'implementation-source.json','provenance.json'}
 pins=report['evidence_sha256'];assert set(pins)==expected
 for name,digest in pins.items():
  path=(directory/name).resolve();assert path.is_relative_to(directory.resolve()) and sha(path.read_bytes())==digest
 captured=json.loads((directory/'implementation-source.json').read_bytes());current=evaluation.source_receipt();assert captured==current
 provenance=json.loads((directory/'provenance.json').read_bytes());assert provenance['corpus_lf_sha256']==manifest['corpus_files']
 assert provenance['chat_calls_attempted']==0 and provenance['index_creation_attempted'] is False
 assert set(provenance['runtime_source_sha256'])=={'server/app/agent_runtime/retrieval.py','server/app/agent_runtime/relevance.py','server/app/agent_runtime/semantic_retrieval.py'}
 for name,digest in provenance['runtime_source_sha256'].items():assert current[name]['sha256']==digest
 deployed=json.loads((STAGE/'deployed-corpus-raw-identity-r3.json').read_bytes())
 assert deployed['scope']=='READ_ONLY_DEPLOYED_CORPUS_NO_PROVIDER_OR_INDEX_ACCESS'
 assert deployed['provider_calls_attempted']==0 and deployed['index_access_attempted'] is False
 assert set(deployed['files'])==set(manifest['corpus_files'])
 line_endings={}
 with tempfile.TemporaryDirectory(prefix='mini-drop-hybrid-public-raw-identity-') as temp:
  owned=Path(temp).resolve()
  for name,digest in manifest['corpus_files'].items():
   raw=(CLEAN/name).read_bytes().replace(b'\r\n',b'\n');receipt=deployed['files'][name]
   assert sha(raw)==digest==receipt['lf_sha256']
   variants={'LF':raw,'CRLF':raw.replace(b'\n',b'\r\n')}
   matches=[(kind,data) for kind,data in variants.items() if sha(data)==receipt['raw_sha256']]
   assert len(matches)==1,'Actual corpus differs beyond frozen LF_TEXT normalization'
   kind,data=matches[0];line_endings[name]=kind
   assert len(data)==receipt['bytes'] and data.count(b'\r\n')==receipt['crlf_count'] and data.count(b'\n')==receipt['lf_count']
   path=owned/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
  chunks=corpus(owned/'knowledge')
  assert chunks==deployed['chunks'],'Independent chunks differ from exact reconstructed deployed raw bytes'
 chunk_by_id={c['chunk_id']:c for c in chunks};assert len(chunk_by_id)==len(chunks)
 catalog={r['knowledge_id']:r for r in json.loads((CLEAN/'knowledge/catalog.json').read_bytes())};records=[]
 for case in questions['cases']:
  row=json.loads((directory/'records'/(case['case_id']+'.json')).read_bytes());trace=row['trace'];assert row['case_id']==case['case_id'] and row['query']==case['query']
  assert trace['query']==case['query'] and trace['relevance_query']==case['query']
  assert trace['query_hash']==sha(case['query'].encode()) and trace['relevance_query_hash']==sha(case['query'].encode())
  assert trace['requested_backend']=='HYBRID' and trace['evidence_contract']['is_evidence'] is False
  assert trace['top_k']==3 and trace['matched_count']==len(trace['matches'])<=3
  assert trace['query_profile']==query_profile(case['query'])
  assert trace['health_scope']=='RETRIEVAL_ONLY' and trace['no_match_is_normal'] is False
  assert trace['outcome']==('MATCHED' if trace['matches'] else 'NO_RELEVANT_KNOWLEDGE')
  assert trace['health']==('DEGRADED' if trace['degraded_reasons'] else 'HEALTHY')
  ids=list(dict.fromkeys(m['knowledge_id'] for m in trace['matches']))[:3];assert row['matched_ids']==ids
  for match in trace['matches']:
   entry=catalog[match['knowledge_id']];assert match['document']=='knowledge/'+entry['document']
   assert match['content_hash']==deployed['files'][match['document']]['raw_sha256']
   chunk=chunk_by_id[match['chunk_id']]
   for key in ('knowledge_id','document','content_hash','excerpt'):assert match[key]==chunk[key]
   assert sha((CLEAN/match['document']).read_bytes().replace(b'\r\n',b'\n'))==manifest['corpus_files'][match['document']]
   assert match['relevance']==assess_relevance(case['query'],entry) and match['relevance']['accepted'] is True
  records.append(row)
 private=json.loads((CLEAN/(evaluation.PREFIX+'private.json')).read_bytes());metrics,cases=evaluation.retrieval_metrics(questions,private,records)
 assert (metrics,cases)==(report['metrics'],report['cases'])
 backends={name:sum(r['trace']['actual_backend']==name for r in records) for name in sorted({r['trace']['actual_backend'] for r in records})};assert report['actual_backend_counts']==backends
 return {'schema':'mini-drop.independent-retrieval-raw-review.v2','status':'VERIFIED','scope':'EXACT_RAW_HASH_SOURCE_ADMISSION_AND_RESCORE_NOT_RERUN_SEMANTIC_RANKING','source_head':'df0d3ef00a945e7d909f73868819799b2b7cc7f7','report_sha256':sha((directory/'report.json').read_bytes()),'case_count':24,'actual_backend_counts':backends,'provider_calls_attempted':0,'knowledge_is_evidence':False,'deployed_corpus_identity_receipt_sha256':sha((STAGE/'deployed-corpus-raw-identity-r3.json').read_bytes()),'corpus_contract':'FROZEN_LF_TEXT_WITH_EXACT_DEPLOYED_RAW_SHA_AND_CHUNK_IDENTITIES','deployed_corpus_line_endings':line_endings,'independently_reconstructed_chunk_count':len(chunks),'metrics':metrics}
if __name__=='__main__':
 path=STAGE/'hybrid-first-run';result=verify(path)
 with (STAGE/'hybrid-independent-review-r3.json').open('xb') as f:f.write((json.dumps(result,indent=2)+'\n').encode())
 print(json.dumps(result))
