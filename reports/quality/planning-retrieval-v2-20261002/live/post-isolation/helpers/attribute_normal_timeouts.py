"""Only read safe checkpoint structure/log timestamps/config, never call a model."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent


def main():
    attempts = []
    for name in ("r3", "r4"):
        summary = json.loads((STAGE / f"live-smoke-{name}/summary.json").read_text(encoding="utf-8"))
        row = next(row for row in summary["cases"] if row["name"] == "normal")
        attempts.append({"attempt": name, "diagnosis_id": row["diagnosis_id"], "versioned": name == "r4"})
    spec = importlib.util.spec_from_file_location("attribution_provider", ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    worker = '''import json,os
from langgraph.checkpoint.postgres import PostgresSaver
from server.app.drop_insight.diagnosis_agent import _database_url_for_psycopg
from server.app.agent_runtime.runtime import checkpoint_thread_id
from server.app.agent_runtime.deadlines import planning_seconds,PLANNING_STAGE_SECONDS
from server.app.agent_runtime.memory import AgentMemoryPolicy
from server.app.database import new_session
from server.app.models import DropInsightSessionModel
from server.app.ai_provider import get_ai_settings
from server.app.agent_runtime.model_factory import create_chat_model
attempts=%r
def structure(m):
 result={'type':m.type,'name':getattr(m,'name',None),'message_id':getattr(m,'id',None)}
 if m.type=='ai':
  calls=getattr(m,'tool_calls',[]) or []
  result.update(tool_names=[c.get('name') for c in calls],tool_call_ids=[c.get('id') for c in calls],planning_dispositions=[(c.get('args') or {}).get('output',{}).get('disposition') for c in calls if c.get('name')=='finish_diagnosis_plan'],usage=getattr(m,'usage_metadata',None))
 if m.type=='tool':
  result['tool_call_id']=getattr(m,'tool_call_id',None)
  if result['name'] in {'finish_diagnosis_plan','request_diagnostic_probe'}:
   try:
    p=json.loads(m.content)
    result.update(accepted=p.get('accepted'),code=p.get('code'))
   except ValueError:
    result['accepted']=None
 return result
result=[]
with PostgresSaver.from_conn_string(_database_url_for_psycopg()) as saver:
 for row in attempts:
  did=row['diagnosis_id'];key=checkpoint_thread_id(did) if row['versioned'] else did
  cp=list(saver.list({'configurable':{'thread_id':key}},limit=100))
  last=cp[0] if cp else None
  messages=[] if last is None else list(last.checkpoint.get('channel_values',{}).get('messages',[]))
  with new_session() as s:
   d=s.get(DropInsightSessionModel,did);mode=d.mode;budget=d.budget_json;created=d.created_at.isoformat()
  frames=[]
  for c in reversed(cp):
   ms=c.checkpoint.get('channel_values',{}).get('messages',[])
   frames.append({'checkpoint_ts':c.checkpoint.get('ts'),'step':c.metadata.get('step'),'source':c.metadata.get('source'),'channel_names':list(c.checkpoint.get('channel_values',{})),'message_count':len(ms),'ai_count':sum(m.type=='ai' for m in ms),'tool_count':sum(m.type=='tool' for m in ms),'last_message':structure(ms[-1]) if ms else None,'pending_error_types':[type(w[2]).__name__ for w in c.pending_writes if w[1]=='__error__']})
  result.append({**row,'physical_key':key,'mode':mode,'budget':budget,'created_at':created,'planning_seconds_now':planning_seconds(did,PLANNING_STAGE_SECONDS),'final_messages':[structure(m) for m in messages if m.type in {'ai','tool'}],'checkpoints':frames})
settings=get_ai_settings()
model=create_chat_model(settings,temperature=0.1,max_tokens=1000,timeout=30)
print(json.dumps({'attempts':result,'current_client':{'provider':settings.provider,'model':settings.model,'request_timeout':model.request_timeout,'max_retries':model.max_retries,'max_tokens':model.max_tokens,'memory':AgentMemoryPolicy.from_env().__dict__,'bounded_model_timeout_cap_seconds':45,'planning_stage_seconds':PLANNING_STAGE_SECONDS,'summary_model_timeout_cap_seconds':10},'model_invocations':0}))
''' % attempts
    remote = "import json,subprocess\nids=" + repr([row["diagnosis_id"] for row in attempts]) + "\n"
    remote += "r=subprocess.run(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-'],input=" + repr(worker) + ",capture_output=True,text=True,check=True)\nresult=json.loads(r.stdout)\nresult['current_worker_logs']=[]\n"
    remote += "logs=subprocess.run(['docker','logs','--since','2026-10-02T09:50:00Z','mini-drop-control-diagnosis-worker-1'],capture_output=True,text=True,check=True)\n"
    remote += "for line in (logs.stdout+'\\n'+logs.stderr).splitlines():\n try:\n  row=json.loads(line)\n except ValueError:\n  continue\n if row.get('diagnosis_id') in ids:\n  result['current_worker_logs'].append({k:row.get(k) for k in ['ts','event','diagnosis_id','framework','error','reason','maximum_corrections','status_code']})\n"
    remote += "print(json.dumps(result))\n"
    result = json.loads(provider.remote(remote))
    result["scope"] = "No model calls, no hidden reasoning/messages/credentials exported. Checkpoint/log timestamps are graph milestones, not measured HTTP per-call latency. Old worker logs were not retained here; r3 error type was captured by initial immutable audit."
    result["attribution"] = {
        "r3": "Two completed model responses executed two different reference lookups; no finish/probe output accepted; later client OpenAITimeoutError. Exact failing main vs summarization request is not logged.",
        "r4": "One completed model response proposed INVESTIGATE in finish_diagnosis_plan; real validator rejected INVALID_PLANNING_OUTPUT (collection failure as refutation). A semantic correction was attempted, then client OpenAITimeoutError; no accepted planning result.",
        "budget": "Both sessions are ASSISTED with requested 120 seconds. Interactive planning_seconds returns stage cap 60, not the autonomous 120-45 remainder. Main calls use min(45, remaining stage seconds), summary calls min(10, remaining); SDK max_retries=0. OpenAITimeoutError differs from explicit PlanningDeadlineExceeded; whether network, provider generation or remaining timeout caused the stall cannot be separated from these logs."}
    output = STAGE / "normal-timeout-attribution.json"
    assert not output.exists()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"attempts": len(result["attempts"]), "current_client": result["current_client"], "logs": result["current_worker_logs"], "model_invocations": 0}))


if __name__ == "__main__":
    main()
