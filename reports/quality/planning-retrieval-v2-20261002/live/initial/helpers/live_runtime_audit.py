"""Read-only audit of the three owned live smoke planner checkpoints/log types."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent


def main():
    spec = importlib.util.spec_from_file_location("live_provider", ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    summary = json.loads((STAGE / "live-smoke-r3/summary.json").read_text(encoding="utf-8"))
    ids = [row["diagnosis_id"] for row in summary["cases"]]
    worker = '''import json
from langgraph.checkpoint.postgres import PostgresSaver
from server.app.drop_insight.diagnosis_agent import _database_url_for_psycopg,_accepted_tool_payload
ids=%r
result=[]
with PostgresSaver.from_conn_string(_database_url_for_psycopg()) as saver:
 for did in ids:
  checkpoints=list(saver.list({'configurable':{'thread_id':did}},limit=1))
  checkpoint=checkpoints[0] if checkpoints else None
  messages=[] if checkpoint is None else list(checkpoint.checkpoint.get('channel_values',{}).get('messages',[]))
  result.append({'diagnosis_id':did,'checkpoint_namespace':None if checkpoint is None else checkpoint.config['configurable'].get('checkpoint_ns'),'checkpoint_present':checkpoint is not None,'messages':[{'type':m.type,'name':getattr(m,'name',None),'tool_call_id':getattr(m,'tool_call_id',None),'tool_calls':getattr(m,'tool_calls',None),'content':str(m.content)[:4000],'usage':getattr(m,'usage_metadata',None)} for m in messages if m.type in {'ai','tool'}],'accepted_proposal':_accepted_tool_payload(messages)})
print(json.dumps(result,ensure_ascii=False))
''' % ids
    remote = "import json,subprocess\n" + "ids=" + repr(ids) + "\n"
    remote += "r=subprocess.run(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-'],input=" + repr(worker) + ",capture_output=True,text=True,check=True)\n"
    remote += "result={'checkpoint_audit':json.loads(r.stdout),'provider_failure_log_types':[]}\n"
    remote += "logs=subprocess.run(['docker','logs','--since','2026-10-02T09:18:00Z','mini-drop-control-diagnosis-worker-1'],capture_output=True,text=True,check=True)\n"
    remote += "for line in (logs.stdout+'\\n'+logs.stderr).splitlines():\n try:\n  row=json.loads(line)\n except ValueError:\n  continue\n if row.get('diagnosis_id') in ids and ('failed' in str(row.get('event','')) or 'circuit' in str(row.get('event',''))):\n  result['provider_failure_log_types'].append({k:row.get(k) for k in ['event','diagnosis_id','framework','error','status_code']})\n"
    remote += "print(json.dumps(result,ensure_ascii=False))\n"
    result = json.loads(provider.remote(remote))
    output = STAGE / "live-runtime-audit-r3.json"
    assert not output.exists(), "preserve original runtime audit bytes"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checkpoints": len(result["checkpoint_audit"]), "normal": result["checkpoint_audit"][0]["accepted_proposal"], "failure_log_types": result["provider_failure_log_types"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
