from pathlib import Path
import json
import uuid

STAGE=Path(__file__).resolve().parent
source=(STAGE/'run_health_states.py').read_text(encoding='utf-8').split("if __name__=='__main__':",1)[0]
trace=uuid.uuid4().hex;span=uuid.uuid4().hex[:16]
needle="'target':{'agent_id':'control-interview-demo-agent','pid':proc['pid']}"
assert source.count(needle)==1
source=source.replace(needle,"'target':{'agent_id':'control-interview-demo-agent','pid':proc['pid'],'trace_id':trace,'span_id':span}")
namespace={'__file__':str(__file__),'__name__':'postrelease','trace':trace,'span':span}
exec(compile(source,str(STAGE/'run_health_states.py'),'exec'),namespace)
records=namespace['check']('go-postrelease-trace','NORMAL_OBSERVED')
target=records['diagnosis']['target']
assert target['trace_id']==trace and target['span_id']==span
(STAGE/'postrelease-correlation.json').write_text(json.dumps({'passed':True,'diagnosis_id':records['diagnosis']['diagnosis_id'],
    'scope':'SYNTHETIC_CORRELATION_LABELS_ON_REAL_NORMAL_CHECK; NOT_REQUEST_CAUSALITY',
    'trace_id':trace,'span_id':span,'process_binding':target['process_binding']},indent=2),encoding='utf-8')
print('New release preserved correlation labels through trusted process binding')
