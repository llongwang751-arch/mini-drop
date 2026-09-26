"""Generate a read-only UI projection from intact, completed measurements."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from server.app.drop_insight.business_acceptance import canonical_hash


def verified(path):
    value=json.loads(path.read_text(encoding='utf-8'))
    if canonical_hash({k:v for k,v in value.items() if k!='report_sha256'})!=value.get('report_sha256'):
        raise ValueError(f'evidence hash mismatch: {path.name}')
    return value


def build(source, actual_rag=None, diagnosis=None):
    campaign=verified(source)
    if campaign.get('status')!='COMPLETED':raise ValueError('campaign is incomplete')
    rows=[]
    for item in campaign['results']:
        path=(source.parent/item['case_file']).resolve()
        if not path.is_relative_to(source.parent.resolve()):raise ValueError('case path escapes report directory')
        case=verified(path)
        if item['report_sha256']!=case['report_sha256']:raise ValueError('summary case reference mismatch')
        rows.append({'scenario_id':case['scenario_id'],'title':case['title'],
            'comparison':case['comparison'],'ingestion':case['ingestion'],
            'ai_root_cause_verified':False,'case_sha256':case['report_sha256']})
    result={'schema':'mini-drop.business-view.v1','status':'AVAILABLE',
        'finished_at':campaign['finished_at'],'run_id':campaign['run_id'],
        'revision':campaign['revision'],'scope':campaign['scope'],
        'source_report':source.name,'source_sha256':campaign['report_sha256'],'cases':rows}
    if actual_rag is not None:
        actual=json.loads(actual_rag.read_text(encoding='utf-8'))
        if actual.get('status')!='COMPLETED' or actual.get('schema')!='mini-drop.actual-rag-acceptance.v1':
            raise ValueError('actual RAG run is incomplete')
        if canonical_hash({k:v for k,v in actual.items() if k!='sha256'})!=actual.get('sha256'):
            raise ValueError('actual RAG evidence hash mismatch')
        # Recalculate from raw windows. A stored summary alone is insufficient.
        from server.app.drop_insight.business_acceptance import MeasurementWindow,AcceptancePolicy,compare_business_windows
        comparison=compare_business_windows(*(MeasurementWindow.model_validate(actual['windows'][n])
            for n in ('baseline','fault','after')),AcceptancePolicy.model_validate(actual['policy']),actual['change'])
        # Stage sample counts are additive. Old reports cannot contain them,
        # but every pre-existing verdict, metric and raw-window hash must still
        # agree. The projection uses the enriched recalculation; source evidence
        # remains untouched. Incorrect historical zero-filled P95s still fail.
        comparable=deepcopy(comparison)
        for name, summary in comparable['summaries'].items():
            if 'stage_sample_counts' not in actual['comparison'].get('summaries',{}).get(name,{}):
                summary.pop('stage_sample_counts',None)
        if comparable!=actual['comparison']:raise ValueError('actual RAG comparison does not match measurements')
        row={'scenario_id':'RAG-ACTUAL-01','title':'实际办公助手：本地检索读取了无用向量',
            'comparison':comparison,'ai_root_cause_verified':False,'case_sha256':actual['sha256'],
            'source_kind':'ACTUAL_RAG_ENGINE','environment':actual['windows']['fault']['workload']['environment'],
            'background':actual['business_description'],
            'boundary':'实际 AGI-saber 的 Engine → HybridStore → LocalRagChunkRepo；固定测试语料、本地摘录。没有调用真实生成模型或 Milvus，也不覆盖原助手的登录、上传和聊天界面。'}
        if diagnosis is not None:
            linkage=json.loads(diagnosis.read_text(encoding='utf-8'))
            if canonical_hash({k:v for k,v in linkage.items() if k!='sha256'})!=linkage.get('sha256'):
                raise ValueError('diagnostic linkage hash mismatch')
            if linkage.get('target_matches') is not True:raise ValueError('diagnosis target mismatch')
            if linkage.get('source_sha256')!=actual['windows']['fault']['revision']:raise ValueError('diagnosis source mismatch')
            row['diagnosis']={k:linkage[k] for k in ('diagnosis_id','status','task_count','evidence_count','report_count')}
        rows.insert(0,row)
        result['actual_rag_source']=actual_rag.name
        result['finished_at']=actual['completed_at']
        result['revision']=actual['windows']['after']['revision'][:16]
        result['scope']='ACTUAL_RAG_ENGINE_AND_LOCAL_FIXTURES; SEPARATE_BUSINESS_AND_AI_VERDICTS'
    result['report_sha256']=canonical_hash(result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--actual-rag',type=Path);parser.add_argument('--diagnosis',type=Path)
    args=parser.parse_args();value=build(args.source,args.actual_rag,args.diagnosis);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
