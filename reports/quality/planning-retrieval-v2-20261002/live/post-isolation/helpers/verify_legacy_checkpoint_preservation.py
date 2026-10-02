"""Read existing three physical keys and compare the captured immutable projection."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent


def main():
    archive = ROOT / "reports/quality/planning-retrieval-v2-20261002/live/initial"
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    original_path = archive / "live-runtime-audit-r3.json"
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == manifest["files"][original_path.name]["sha256"]
    original = json.loads(original_path.read_text(encoding="utf-8"))["checkpoint_audit"]
    ids = [row["diagnosis_id"] for row in original]
    spec = importlib.util.spec_from_file_location("preservation_provider", ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
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
    remote = "import subprocess\nr=subprocess.run(['docker','exec','-i','mini-drop-control-diagnosis-worker-1','python','-'],input=" + repr(worker) + ",capture_output=True,text=True,check=True)\nprint(r.stdout)\n"
    actual = json.loads(provider.remote(remote))
    pins = json.loads((STAGE / "r5-publication-verification.json").read_text(encoding="utf-8"))
    result = {"schema": "mini-drop.legacy-checkpoint-preservation.v1", "old_keys_present": all(row["checkpoint_present"] for row in actual),
              "captured_projection_unchanged": actual == original, "actual": actual,
              "current_source_head": pins["source_head"], "current_release": pins["release"],
              "original_source_head": manifest["source_head"], "original_audit_sha256": manifest["files"][original_path.name]["sha256"],
              "comparison_scope": "All three original physical keys remain. Compare the exact captured tool/AI message projection (content capped at 4000 chars), tool-call correlation and accepted proposal; no unrecorded full-checkpoint byte hash is claimed.",
              "writes_performed": 0}
    output = STAGE / "old-checkpoints-preserved-r4.json"
    assert not output.exists()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"old_keys": len(actual), "old_keys_present": result["old_keys_present"], "captured_projection_unchanged": result["captured_projection_unchanged"]}))
    assert result["old_keys_present"] and result["captured_projection_unchanged"]


if __name__ == "__main__":
    main()
