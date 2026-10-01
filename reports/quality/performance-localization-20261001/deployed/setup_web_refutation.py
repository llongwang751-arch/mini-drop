from pathlib import Path
import shutil
import subprocess

root=Path.cwd();stage=Path(__file__).resolve().parent
target=stage/'web-refutation-sandbox'/'web'
target.mkdir(parents=True,exist_ok=False)
for path in (root/'web').iterdir():
 if path.is_file():shutil.copy2(path,target/path.name)
for name in ('src','public'):
 shutil.copytree(root/'web'/name,target/name)
deps=(root/'output/code-comments-20261001/web-deps/node_modules').resolve()
subprocess.run(['powershell','-NoProfile','-Command',
               "New-Item -ItemType Junction -Path '"+str(target/'node_modules')+"' -Target '"+str(deps)+"' | Out-Null"],check=True)
print('Created owned Web test/build sandbox using matching locked dependencies.')
