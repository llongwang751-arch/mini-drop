from pathlib import Path
import ast,json,math,sys
root=Path.cwd();src=root/'scripts/evaluate_heldout_diagnosis.py';reportpath=root/'reports/quality/interview-release-20261002/heldout/first-run/report.json'
report=json.loads(reportpath.read_bytes());prefix='benchmarks/retrieval/heldout_20261002_'
questions=json.loads((root/(prefix+'public.json')).read_bytes());private=json.loads((root/(prefix+'private.json')).read_bytes());records=[json.loads((reportpath.parent/'records'/(c['case_id']+'.json')).read_bytes()) for c in questions['cases']]
names={'CATEGORIES','DISPOSITIONS','RUNTIME_TOOLS','parse_output','token_usage','score_records'}
nodes=[]
for n in ast.parse(src.read_bytes()).body:
 name=n.name if isinstance(n,ast.FunctionDef) else (n.targets[0].id if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) else None)
 if name in names:nodes.append(n)
namespace={'json':json};exec(compile(ast.Module(body=nodes,type_ignores=[]),str(src),'exec'),namespace)
def naive_sum(items,start=0):
 for item in items:start+=item
 return start
def stable_sum(items,start=0):
 values=list(items)
 return math.fsum(values)+start if any(isinstance(v,float) for v in values) else sum(values,start)
for mode,fn in [('current',sum),('python311_naive',naive_sum),('exact_fsum',stable_sum)]:
 namespace['sum']=fn
 metrics,cases=namespace['score_records'](questions,private,records)
 print(json.dumps({'python':sys.version.split()[0],'mode':mode,'metric_diff':{k:[report['metrics'][k],v] for k,v in metrics.items() if report['metrics'][k]!=v},'case_diff_count':sum(a!=b for a,b in zip(cases,report['cases'])),'recall':metrics['recall_at_3'],'mrr':metrics['mrr_at_3']},ensure_ascii=False))
