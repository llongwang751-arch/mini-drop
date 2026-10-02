from pathlib import Path
import difflib
import subprocess

stage=Path(__file__).resolve().parent
for name in ('AIDiagnosis.jsx','AIDiagnosis.test.jsx'):
 path=Path('web/src/pages')/name
 base=subprocess.check_output(['git','show','HEAD:'+path.as_posix()]).decode('utf-8')
 current=path.read_text(encoding='utf-8')
 if name=='AIDiagnosis.jsx':
  before=(stage/('route-before-'+name)).read_text(encoding='utf-8')
  user=[]
  a=base.splitlines(keepends=True);b=before.splitlines(keepends=True)
  for kind,_,__,start,end in difflib.SequenceMatcher(a=a,b=b).get_opcodes():
   if kind=='equal':continue
   assert kind=='insert'
   assert all(not s.strip() or s.lstrip().startswith('//') for s in b[start:end])
   user+=b[start:end]
 else:
  header=current.splitlines(keepends=True)[0]
  assert header.startswith('// 前端回归验证') and header not in base
  user=[header]
 for line in user:assert line in current;current=current.replace(line,'',1)
 blob=subprocess.check_output(['git','hash-object','-w','--stdin'],input=current.encode('utf-8')).decode().strip()
 subprocess.run(['git','update-index','--cacheinfo','100644,'+blob+','+path.as_posix()],check=True)
 print(name,'user comment lines preserved:',len(user))
subprocess.run(['git','add','--','docs/PROJECT_CONTEXT.md','docs/PERFORMANCE_DIAGNOSIS.md'],check=True)
