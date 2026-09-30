from pathlib import Path
import json
import shutil
import zipfile

stage=Path(__file__).resolve().parent
source=stage/'after-multi-hour'
target=Path('reports/quality/performance-fix-20260930/load-multi-interrupted')
target.mkdir(exist_ok=True)
result=json.loads((source/'interruption.json').read_text(encoding='utf-8'))
assert result['status']=='INTERRUPTED' and result['owned_remote_pid_exited']
for name in ['report.json','interruption.json','interrupted-remote-completed.json']:
    dst=target/name
    if dst.exists():assert dst.read_bytes()==(source/name).read_bytes()
    else:shutil.copyfile(source/name,dst)
bundle=target/'evidence.zip'
assert not bundle.exists()
with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.iterdir()):
        if path.suffix in {'.json','.jsonl','.log'}:archive.write(path,path.name)
    for path in sorted((stage/'after-multi-source').rglob('*')):
        if path.is_file():archive.write(path,'source/'+path.relative_to(stage/'after-multi-source').as_posix())
with zipfile.ZipFile(bundle) as archive:assert archive.testzip() is None
print(json.dumps(result))
