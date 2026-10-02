from pathlib import Path
import difflib
import subprocess

stage=Path(__file__).resolve().parent
paths=['web/src/utils/reportPresentation.js','web/src/utils/observationAssessment.js',
       'web/src/components/ObservabilityOverview.jsx','web/src/components/ConclusionCard.jsx',
       'web/src/utils/reportPresentation.test.js','web/src/components/ObservabilityOverview.test.jsx']
for name in paths:
 p=Path(name)
 base=subprocess.check_output(['git','show','HEAD:'+name]).decode('utf-8')
 before=(stage/('old-'+p.name)).read_text(encoding='utf-8')
 lines=base.splitlines(keepends=True);old=before.splitlines(keepends=True)
 user_additions=[]
 for kind,a,b,c,d in difflib.SequenceMatcher(a=lines,b=old).get_opcodes():
  if kind=='equal':continue
  assert kind=='insert',(name,'pre-existing functional changes require a separate merge')
  additions=old[c:d]
  assert all(not line.strip() or line.lstrip().startswith('//') for line in additions),name
  user_additions+=additions
 staged=p.read_text(encoding='utf-8')
 for line in user_additions:
  assert line in staged,name
  staged=staged.replace(line,'',1)
 blob=subprocess.check_output(['git','hash-object','-w','--stdin'],input=staged.encode('utf-8')).decode().strip()
 subprocess.run(['git','update-index','--cacheinfo','100644,'+blob+','+name],check=True)
 print(name,'user comment lines preserved:',len(user_additions))
subprocess.run(['git','add','--','web/src/test/fixtures/performance-io-refutation.json',
                'docs/PROJECT_CONTEXT.md','docs/PERFORMANCE_DIAGNOSIS.md'],check=True)
