"""Fetch pinned upstream binaries; verify upstream SHA256 before deployment."""
import argparse
import concurrent.futures
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/lightweight-business'
LOCK = {r['id']:r for r in json.loads(Path(__file__).with_name('artifact-lock.json').read_text(encoding='utf-8'))['artifacts']}
CATALOG = json.loads((ROOT / 'integrations/lightweight/catalog.json').read_text(encoding='utf-8'))['services']

def fetch(row):
    assert row['version']==LOCK[row['id']]['version'], 'Version changes require a reviewed artifact lock'
    result = {**row, **LOCK[row['id']]}
    if row['kind']=='container':result['image']=LOCK[row['id']]['image_digest']
    if row['kind'] != 'binary': return result
    folder = OUT / 'upstream' / row['id']; folder.mkdir(parents=True,exist_ok=True)
    prefix = f"https://github.com/{row['repo']}/releases/download/{row['version']}/"
    for name in [row['asset'],row['checksums']]:
        p=folder/name
        if not p.exists():
            with urllib.request.urlopen(prefix+name,timeout=90) as response:
                p.write_bytes(response.read())
    checksum_lines=(folder/row['checksums']).read_text().splitlines()
    expected=next(line.split()[0] for line in checksum_lines if line.split()[-1].lstrip('*')==row['asset'])
    actual=hashlib.sha256((folder/row['asset']).read_bytes()).hexdigest()
    assert expected==actual==LOCK[row['id']]['asset_sha256'], 'upstream asset checksum mismatch'
    with tarfile.open(folder/row['asset']) as archive:
        member=next(x for x in archive.getmembers() if x.isfile() and Path(x.name).name==row['binary'])
        binary=archive.extractfile(member).read()
        (folder/row['binary']).write_bytes(binary)
    result.update(asset_sha256=actual,binary_sha256=hashlib.sha256(binary).hexdigest(),binary_bytes=len(binary))
    print(row['id']+' verified',flush=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--work-dir',type=Path,default=OUT);args=parser.parse_args();OUT=args.work_dir.resolve();OUT.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(fetch,CATALOG))
    (OUT/'upstream-lock.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Pinned release receipts written')
