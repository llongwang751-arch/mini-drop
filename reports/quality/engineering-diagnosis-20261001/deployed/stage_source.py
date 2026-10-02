from pathlib import Path
import difflib
import subprocess

stage=Path(__file__).resolve().parent
for name in ('EvalPanel.jsx','FaultPlazaPanel.jsx','FaultPlazaPanel.test.jsx'):
    path=Path('web/src/components')/name
    base=subprocess.check_output(['git','show','HEAD:'+path.as_posix()]).decode('utf-8')
    before=(stage/('before-'+name)).read_text(encoding='utf-8')
    current=path.read_text(encoding='utf-8')
    a=base.splitlines(keepends=True);b=before.splitlines(keepends=True)
    user=[]
    for kind,_,__,start,end in difflib.SequenceMatcher(a=a,b=b).get_opcodes():
        if kind=='equal':continue
        assert kind=='insert'
        assert all(not s.strip() or s.lstrip().startswith('//') for s in b[start:end])
        user+=b[start:end]
    for line in user:
        assert line in current;current=current.replace(line,'',1)
    blob=subprocess.check_output(['git','hash-object','-w','--stdin'],input=current.encode('utf-8')).decode().strip()
    subprocess.run(['git','update-index','--cacheinfo','100644,'+blob+','+path.as_posix()],check=True)
    print(name,'user comments preserved:',len(user))
path='docs/README.md'
base=subprocess.check_output(['git','show','HEAD:'+path]).decode('utf-8')
line='- [工程诊断验收与扩展新问题](DIAGNOSIS_ACCEPTANCE.md)：默认门槛、冻结记录重评与新问题注册流程\n'
assert line in Path(path).read_text(encoding='utf-8')
base=base.replace('# 文档入口\n\n','# 文档入口\n\n'+line,1)
blob=subprocess.check_output(['git','hash-object','-w','--stdin'],input=base.encode()).decode().strip()
subprocess.run(['git','update-index','--cacheinfo','100644,'+blob+','+path],check=True)
owned=['contracts/engineering_diagnosis.json','contracts/quality_plan.json',
    'scripts/evaluate_engineering_diagnosis.py','scripts/build_engineering_diagnosis.py',
    'tests/test_engineering_diagnosis.py','web/src/components/EngineeringDiagnosisSummary.jsx',
    'web/src/components/EngineeringDiagnosisSummary.test.jsx','web/public/report-assets/engineering-diagnosis/index.json',
    'docs/DIAGNOSIS_ACCEPTANCE.md','docs/PROJECT_CONTEXT.md','docs/RESTART_HANDOFF.md','docs/PERFORMANCE_DIAGNOSIS.md']
subprocess.run(['git','add','--',*owned],check=True)
