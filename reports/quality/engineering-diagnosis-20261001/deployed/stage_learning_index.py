from pathlib import Path
import io
import importlib.util
import os
import subprocess
import tarfile

stage=Path(__file__).resolve().parent
source=Path.cwd()/'.tmp-engineering-guide'
source.mkdir(exist_ok=False)
tree=subprocess.check_output(['git','write-tree']).decode().strip()
raw=subprocess.check_output(['git','archive','--format=tar',tree])
with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
    for member in archive.getmembers():
        assert (source/member.name).resolve().is_relative_to(source)
    archive.extractall(source,filter='data')
subprocess.run(['git','init','--quiet'],cwd=source,check=True)
subprocess.run(['git','add','-f','--','.'],cwd=source,check=True,capture_output=True)
subprocess.run(['python',str(source/'scripts/generate_learning_guide_file_index.py')],cwd=source,check=True)
spec=importlib.util.spec_from_file_location('generator',source/'scripts/generate_learning_guide_file_index.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
generated=(source/'docs/PROJECT_LEARNING_GUIDE.md').read_text(encoding='utf-8')
block=generated.split(g.START,1)[1].split(g.END,1)[0]
guide=Path('docs/PROJECT_LEARNING_GUIDE.md')
before=guide.read_text(encoding='utf-8');prefix,rest=before.split(g.START,1);_,suffix=rest.split(g.END,1)
temporary=stage/'guide-work.tmp';temporary.write_text(prefix+g.START+block+g.END+suffix,encoding='utf-8');os.replace(temporary,guide)
base=subprocess.check_output(['git','show','HEAD:docs/PROJECT_LEARNING_GUIDE.md']).decode('utf-8');prefix,rest=base.split(g.START,1);_,suffix=rest.split(g.END,1)
blob=subprocess.check_output(['git','hash-object','-w','--stdin'],input=(prefix+g.START+block+g.END+suffix).encode('utf-8')).decode().strip()
subprocess.run(['git','update-index','--cacheinfo','100644,'+blob+',docs/PROJECT_LEARNING_GUIDE.md'],check=True)
print('Index generated from isolated staged source; concurrent source comments, visual redesign and user guide prose preserved outside commit.')
