"""Fresh full business trial with explicit human hypotheses and actual probes."""
from pathlib import Path

STAGE=Path(__file__).resolve().parent
source=(STAGE/'run_business_fix.py').read_text(encoding='utf-8')
source=source.replace("mini-drop-business-fix-20261001T152600Z","mini-drop-business-fix-20261001T155100Z")
source=source.replace("out=STAGE/'business-fix'","out=STAGE/'business-fix-r3'")
source=source.replace("'auto_scope':True,'mode':'AUTONOMOUS'","'auto_scope':True,'mode':'ASSISTED'")
source=source.replace("'max_duration_seconds':100","'max_duration_seconds':300")
needle="            did=diagnosis['diagnosis_id'];(out/'diagnosis-created.json').write_text(json.dumps(diagnosis,ensure_ascii=False,indent=2),encoding='utf-8')"
assert source.count(needle)==1
source=source.replace(needle,needle+'''
            from server.app.drop_insight.cpu_criteria import cpu_observation_plan
            hypothesis=c.request('POST','/api/v2/diagnoses/'+did+'/hypotheses',cpu_observation_plan('PYTHON'))
            (out/'human-hypothesis.json').write_text(json.dumps(hypothesis,ensure_ascii=False,indent=2),encoding='utf-8')
            hid=hypothesis.get('hypothesis_id') or hypothesis.get('id')
            call=c.request('POST','/api/v2/diagnoses/'+did+'/tool-calls',{'hypothesis_id':hid,
                'tool_name':'start_pyspy_profile','arguments':{'agent_id':'tencent-cvm-worker-1','pid':pid,'duration_seconds':20,'sample_rate':99}})
            (out/'probe-requested.json').write_text(json.dumps(call,ensure_ascii=False,indent=2),encoding='utf-8')
            if call['status']=='PENDING_APPROVAL':
                decision=c.request('POST','/api/v2/diagnoses/'+did+'/tool-calls/'+call['tool_call_id']+'/decision',
                    {'approved':True,'reason':'用户已授权隔离业务的真实诊断及同负载修复验收'})
                (out/'probe-approved.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8')
            os_call=c.request('POST','/api/v2/diagnoses/'+did+'/tool-calls',{'hypothesis_id':hid,'tool_name':'collect_sys_metrics','arguments':{'agent_id':'tencent-cvm-worker-1','pid':pid,'duration_seconds':14}})
            (out/'os-probe-requested.json').write_text(json.dumps(os_call,ensure_ascii=False,indent=2),encoding='utf-8')
            if os_call['status']=='PENDING_APPROVAL':
                os_decision=c.request('POST','/api/v2/diagnoses/'+did+'/tool-calls/'+os_call['tool_call_id']+'/decision',{'approved':True,'reason':'用户已授权隔离业务的独立系统指标取证'})
                (out/'os-probe-approved.json').write_text(json.dumps(os_decision,ensure_ascii=False,indent=2),encoding='utf-8')
''')
exec(compile(source,str(STAGE/'run_business_fix.py'),'exec'),{'__file__':str(__file__),'__name__':'__main__'})
