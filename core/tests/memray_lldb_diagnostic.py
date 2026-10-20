"""只在夹具中比较 LLDB 附加行为，生产代码不会导入此模块。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import re
from diagnostic_state import identity, limits


def main():
    pid=int(sys.argv[1])
    token=sys.argv[2]
    if not token.startswith("lldb-") or len(token)!=37 or any(c not in "0123456789abcdef" for c in token[5:]):
        raise RuntimeError("Fixture identity required")
    before=identity(pid)
    if os.geteuid()!=0 or before["uid"]!=10001 or pid<=1:
        raise RuntimeError("Use only the explicitly isolated UID10001 toy")
    lldb=Path(shutil.which("lldb") or "/usr/bin/lldb-16").resolve(strict=True)
    manifest=json.loads(Path('/opt/drop-tools/manifest.json').read_text())
    profiler=Path(manifest['tools']['memray']['path'])
    if hashlib.sha256(profiler.read_bytes()).hexdigest()!=manifest['tools']['memray']['sha256']:
        raise RuntimeError("Profiler integrity failed")
    directory=Path('/run/drop-helper-incoming')/token
    directory.mkdir(mode=0o1770);directory.chmod(0o1770);os.chown(directory,0,10001)
    raw=directory/'allocations.bin'
    begin=time.monotonic()
    interrupted=False
    diagnostics=[]
    try:
        result=subprocess.run([str(profiler),'attach','--method','lldb','--duration','2','-o',str(raw),str(pid)],
            capture_output=True,timeout=30,env={'PATH':'/opt/drop-tools/lldb-bin:/usr/bin:/bin','LC_ALL':'C'},preexec_fn=limits)
        code=result.returncode
        diagnostics=[re.sub(r"0x[0-9a-fA-F]+","<address>",line[:512])
                     for line in (result.stdout+result.stderr).decode(errors='replace').splitlines()
                     if any(word in line.lower() for word in ('error','failed','cannot','permission','not found','debugger'))][:12]
    except subprocess.TimeoutExpired:
        code=None;interrupted=True
    after=identity(pid) if Path('/proc',str(pid)).exists() else None
    report={'validation_mode':'fixture_only_lldb_comparison_not_production_capture',
        'returncode':code,'timed_out':interrupted,'wall_seconds':round(time.monotonic()-begin,3),
        'target_identity_unchanged':before==after,'lldb_sha256':hashlib.sha256(lldb.read_bytes()).hexdigest(),
        'raw_bytes':raw.stat().st_size if raw.exists() else 0,'fixed_tool_diagnostics':diagnostics}
    print(json.dumps(report))


if __name__=='__main__':main()
