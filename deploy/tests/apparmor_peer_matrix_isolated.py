#!/usr/bin/env python3
"""只对可丢弃的受控进程执行真实且有界的 AppArmor 对端检查。

需要 root、Docker 与 apparmor_parser，不重新加载生产策略；三份仓库策略以随机名称复制。
不使用宿主 PID/网络命名空间或业务挂载，只清理由本次 owner 标签确认的容器。
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
OWNER = secrets.token_hex(6)
NAMES = ['drop-agent-controller', 'drop-diagnostic-target', 'drop-collector-helper']
IMAGE = os.environ.get('DROP_PEER_TEST_IMAGE', 'drop-agent:20261020.3')
containers = []
loaded = []
report = {'source': 'real disposable UID10001 processes, isolated PID/network namespaces',
          'production_profiles_reloaded': False, 'checks': {}, 'profile_sha256': {}}


def run(argv, timeout=20):
    value = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if value.returncode:
        raise RuntimeError('bounded command failed: ' + value.stdout[-1500:] + value.stderr[-1200:])
    return value.stdout.strip()


def execute(container, code):
    return json.loads(run(['docker', 'exec', container, '/usr/bin/python3', '-c', code]))


def start(name, profile, code, actor=None, helper=False):
    argv = ['docker', 'run', '-d', '--name', name, '--label', 'io.drop.owner=' + OWNER,
            '--network', 'none', '--read-only', '--user', '0:10001' if helper else '10001:10001',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--security-opt', 'apparmor=' + profile, '--memory', '96m', '--memory-swap', '96m',
            '--cpus', '.15', '--pids-limit', '16', '--tmpfs', '/tmp:rw,noexec,nosuid,size=8m']
    if actor:
        argv += ['--pid', 'container:' + actor]
    if helper:
        for cap in ['SYS_PTRACE', 'DAC_READ_SEARCH', 'KILL']:
            argv += ['--cap-add', cap]
    argv += ['--entrypoint', '/usr/bin/python3', IMAGE, '-c', code]
    run(argv)
    containers.append(name)


assert os.geteuid() == 0
with tempfile.TemporaryDirectory(prefix='drop-peer-check-') as directory:
    base = Path(directory)
    try:
        for name in NAMES:
            original = ROOT / 'deploy/apparmor' / name
            report['profile_sha256'][name] = hashlib.sha256(original.read_bytes()).hexdigest()
            text = original.read_text()
            for peer in NAMES:
                text = text.replace(peer, peer + '-check-' + OWNER)
            path = base / name
            path.write_text(text)
            run(['apparmor_parser', '-Q', '-T', str(path)])
            run(['apparmor_parser', '-r', '-T', str(path)])
            loaded.append(path)
        actor, target, helper = ['drop-peer-' + role + '-' + OWNER for role in ['actor', 'target', 'helper']]
        idle = 'import time; time.sleep(90)'
        start(actor, NAMES[0] + '-check-' + OWNER, idle)
        target_code = 'import signal,time,pathlib; signal.signal(signal.SIGUSR1,lambda *_:None); signal.signal(signal.SIGQUIT,lambda *_:pathlib.Path("/tmp/quit-seen").write_text("yes")); time.sleep(90)'
        start(target, NAMES[1] + '-check-' + OWNER, target_code, actor=actor)
        host = int(run(['docker', 'inspect', '--format', '{{.State.Pid}}', target]))
        pid = int(next(line for line in (Path('/proc') / str(host) / 'status').read_text().splitlines()
                       if line.startswith('NSpid:')).split()[-1])
        assert pid > 1
        time.sleep(.3)
        code = '''import os,pathlib,json,signal
p=pathlib.Path('/proc/PID'); out={'exe_read':bool(p.joinpath('exe').readlink()),'stat_read':bool(p.joinpath('stat').read_text()),'status_read':bool(p.joinpath('status').read_text()),'namespace_read':bool(p.joinpath('ns/pid').readlink())}
try: os.kill(PID,signal.SIGUSR1);out['signal_usr1_denied']=False
except PermissionError: out['signal_usr1_denied']=True
print(json.dumps(out)); assert all(out.values())'''.replace('PID', str(pid))
        report['checks']['controller_read_only'] = execute(actor, code)
        reverse = '''import pathlib,json
try: pathlib.Path('/proc/1/exe').readlink();denied=False
except PermissionError: denied=True
print(json.dumps({'target_cannot_read_controller_exe':denied}));assert denied'''
        report['checks']['target_no_reverse_read'] = execute(target, reverse)
        start(helper, NAMES[2] + '-check-' + OWNER, idle, actor=actor, helper=True)
        helper_code = '''import os,signal,pathlib,time,json,ctypes
p=pathlib.Path('/proc/PID'); out={'target_exe_read':bool(p.joinpath('exe').readlink()),'controller_exe_read':bool(pathlib.Path('/proc/1/exe').readlink())}
caps=int(next(l.split(':',1)[1] for l in pathlib.Path('/proc/self/status').read_text().splitlines() if l.startswith('CapEff:')),16);out['no_sys_admin']=not bool(caps&(1<<21))
os.kill(PID,0); os.kill(PID,signal.SIGQUIT); time.sleep(.1)
os.kill(PID,signal.SIGSTOP);time.sleep(.05);out['stop_received']=p.joinpath('stat').read_text().rsplit(')',1)[1].split()[0]=='T'
os.kill(PID,signal.SIGCONT);time.sleep(.05)
libc=ctypes.CDLL(None,use_errno=True);attached=False
try:
 assert libc.ptrace(16,PID,0,0)==0,ctypes.get_errno();attached=True
 deadline=time.monotonic()+1
 while time.monotonic()<deadline:
  waited,status=os.waitpid(PID,os.WNOHANG)
  if waited:break
  time.sleep(.01)
 else:raise RuntimeError('bounded ptrace stop deadline')
 assert os.WIFSTOPPED(status)
 assert libc.ptrace(17,PID,0,0)==0,ctypes.get_errno();attached=False
 out['ptrace_attach_detach']=True
finally:
 if attached:libc.ptrace(17,PID,0,0)
 os.kill(PID,signal.SIGCONT)
time.sleep(.05);s=dict(l.split(':',1) for l in p.joinpath('status').read_text().splitlines() if ':' in l)
out['restored_without_tracer']=int(s['TracerPid'])==0 and p.joinpath('stat').read_text().rsplit(')',1)[1].split()[0] not in ('T','t','Z','X')
print(json.dumps(out));assert all(out.values())'''.replace('PID', str(pid))
        report['checks']['helper_diagnostics'] = execute(helper, helper_code)
        report['checks']['helper_quit_received'] = execute(target, 'import pathlib,json;v=pathlib.Path("/tmp/quit-seen").read_text()=="yes";print(json.dumps(v));assert v')
        report['passed'] = True
    finally:
        for name in reversed(containers):
            label = subprocess.run(['docker', 'inspect', '--format', '{{index .Config.Labels "io.drop.owner"}}', name], capture_output=True, text=True, timeout=10)
            if label.returncode == 0 and label.stdout.strip() == OWNER:
                run(['docker', 'rm', '-f', name])
        for path in reversed(loaded):
            run(['apparmor_parser', '-R', str(path)])
        report['owned_containers_removed'] = all(subprocess.run(['docker', 'inspect', name], capture_output=True, timeout=10).returncode != 0 for name in containers)
        report['temporary_profiles_unloaded'] = True
        output = ROOT / 'deploy/tests/evidence'
        output.mkdir(exist_ok=True)
        (output / ('apparmor-peer-' + OWNER + '.json')).write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report), flush=True)
