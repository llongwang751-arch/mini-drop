from pathlib import Path
import os
import subprocess
import sys

root=Path.cwd();sys.path.insert(0,str(root))
from scripts import generate_learning_guide_file_index as g
guide=root/'docs/PROJECT_LEARNING_GUIDE.md'
before=guide.read_text(encoding='utf-8')
temporary=Path(__file__).resolve().parent/'guide-generated-work.md'
assert not temporary.exists()
temporary.write_text(before,encoding='utf-8')
g.GUIDE=temporary
g.main()
generated=temporary.read_text(encoding='utf-8')
block=generated.split(g.START,1)[1].split(g.END,1)[0]
prefix,rest=before.split(g.START,1);_,suffix=rest.split(g.END,1)
updated=prefix+g.START+block+g.END+suffix
temporary.write_text(updated,encoding='utf-8')
os.replace(temporary,guide)
assert guide.read_text(encoding='utf-8').split(g.START,1)[0]==prefix
base=subprocess.check_output(['git','show','HEAD:docs/PROJECT_LEARNING_GUIDE.md']).decode('utf-8')
prefix,rest=base.split(g.START,1);_,suffix=rest.split(g.END,1)
staged=prefix+g.START+block+g.END+suffix
blob=subprocess.check_output(['git','hash-object','-w','--stdin'],input=staged.encode('utf-8')).decode().strip()
subprocess.run(['git','update-index','--cacheinfo','100644,'+blob+',docs/PROJECT_LEARNING_GUIDE.md'],check=True)
print('Source generator updated the appendix atomically; user prose stays outside the index.')
