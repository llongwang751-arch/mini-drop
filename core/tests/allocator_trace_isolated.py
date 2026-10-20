#!/usr/bin/env python3
"""Real malloc/free helper regression; all processes and artifacts are isolated.

Uses a freshly built helper and the explicitly synthetic UID10001 I/O workload.
RAW stays in the bounded private spool. The receipt contains counts and digests.
Load the dedicated Helper and target profiles before running on an AppArmor host.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path(os.environ['DROP_ALLOCATOR_BUILD']).resolve(strict=True)
SPOOL = Path(os.environ['DROP_HELPER_SPOOL']).resolve(strict=True)
TOOLS = os.environ.get('DROP_ALLOCATOR_TOOLS_IMAGE', 'drop-agent:20261020.1')
TOY = os.environ.get('DROP_ALLOCATOR_TOY_IMAGE', 'drop-demo-workloads:20261020.1')
owner = secrets.token_hex(8)
name = 'drop-allocator-' + owner
receipt = ROOT / 'core/evidence' / (name + '.json')
receipt.parent.mkdir(exist_ok=True)
root = SPOOL / name
root.mkdir(mode=0o700)
cache, incoming, socket = [root / p for p in ('cache', 'incoming', 'socket')]
for p in (cache, incoming, socket):
    p.mkdir(mode=0o700)
os.chown(incoming, 0, 10001)
incoming.chmod(0o1770)
os.chown(socket, 0, 10001)
socket.chmod(0o750)
capacity = os.statvfs(root)
if capacity.f_frsize * capacity.f_blocks > 2147483648:
    raise RuntimeError('Requires a dedicated physical filesystem of at most 2 GiB')
eventfile = Path('/sys/kernel/tracing/uprobe_events')
before_events = set(eventfile.read_text().splitlines())
reports = []
started = []


def command(args, timeout=30, check=True):
    r = subprocess.run(list(map(str, args)), capture_output=True, text=True,
                       timeout=timeout)
    if check and r.returncode:
        raise RuntimeError('Owned allocator fixture command failed; see private diagnostics')
    return r


def wait_for(predicate, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = predicate()
            if result:
                return result
        except (ValueError, FileNotFoundError):
            pass
        time.sleep(.2)
    raise RuntimeError('Allocator fixture readiness deadline exceeded')


def execute(args, **kw):
    return command(['docker', 'exec', name, *args], **kw)


def snapshot(pid):
    code = "import pathlib,json; p=pathlib.Path('/proc/PID'); s=(p/'stat').read_text().rsplit(')',1)[1].split(); io=dict(l.split(':',1) for l in (p/'io').read_text().splitlines()); status=dict(l.split(':',1) for l in (p/'status').read_text().splitlines() if ':' in l); print(json.dumps({'state':s[0],'start':s[19],'uid':int(status['Uid'].split()[0]),'writes':int(io['syscw']),'tracer':int(status['TracerPid'])}))".replace('PID', str(pid))
    return json.loads(execute(['/usr/bin/python3', '-c', code]).stdout)


try:
    args = ['docker', 'run', '-d', '--init', '--name', name, '--label',
            'io.drop.owner=' + owner, '--network', 'none', '--read-only',
            '--user', '0:10001', '--cap-drop', 'ALL', '--cap-add', 'PERFMON',
            '--cap-add', 'BPF', '--cap-add', 'SYS_PTRACE', '--cap-add',
            'DAC_READ_SEARCH', '--cap-add', 'KILL', '--security-opt',
            'no-new-privileges:true', '--security-opt', 'apparmor=drop-collector-helper',
            '--security-opt', 'seccomp=' + str(ROOT / 'deploy/seccomp/agent-ebpf.json'),
            '--memory', '384m', '--memory-swap', '384m', '--cpus', '.5',
            '--pids-limit', '64', '--ulimit', 'memlock=134217728:134217728',
            '--tmpfs', '/tmp:rw,size=32m,mode=1777',
            '-e', 'COLLECTOR_HELPER_SOCKET=/run/drop-helper/control.sock',
            '-e', 'COLLECTOR_HELPER_WORK_DIR=/var/lib/drop-helper',
            '-e', 'COLLECTOR_HELPER_INCOMING_DIR=/run/drop-helper-incoming',
            '-e', 'COLLECTOR_HELPER_REQUIRE_BOUNDED_FS=true',
            '-e', 'ALLOW_HOST_PROCESSES=true', '-e', 'ALLOWED_TARGET_UIDS=10001']
    for source, target, ro in [
        (cache, '/var/lib/drop-helper', False),
        (incoming, '/run/drop-helper-incoming', False),
        (socket, '/run/drop-helper', False),
        (BUILD / 'drop_collector_helper', '/fixture/drop_collector_helper', True),
        (BUILD / 'drop_helper_probe', '/fixture/drop_helper_probe', True),
        (ROOT / 'contracts/task-kinds.json', '/catalog.json', True),
        (Path('/sys/kernel/tracing'), '/sys/kernel/tracing', True),
        (eventfile, str(eventfile), False),
    ]:
        args += ['--mount', 'type=bind,src=' + str(source) + ',dst=' + target +
                 (',readonly' if ro else '')]
    args += ['-e', 'TASK_KINDS_FILE=/catalog.json', '--entrypoint',
             '/fixture/drop_collector_helper', TOOLS]
    command(args)
    started.append(name)
    wait_for(lambda: (socket / 'control.sock').is_socket())
    toy = name + '-toy'
    command(['docker', 'run', '-d', '--name', toy, '--label', 'io.drop.owner=' + owner,
             '--pid', 'container:' + name, '--network', 'none', '--read-only',
             '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt',
             'no-new-privileges:true', '--security-opt', 'apparmor=drop-diagnostic-target',
             '--memory', '64m', '--memory-swap', '64m',
             '--cpus', '.05', '--pids-limit', '16', '--tmpfs',
             '/tmp:rw,size=8m,mode=1777', '--entrypoint', '/demo/bin/drop_io_demo', TOY])
    started.append(toy)
    discover = "import pathlib,json;print(json.dumps([int(p.name) for p in pathlib.Path('/proc').iterdir() if p.name.isdigit() and (p/'comm').exists() and (p/'comm').read_text().strip()=='drop_io_demo']))"
    pid = wait_for(lambda: next(iter(json.loads(execute(['/usr/bin/python3', '-c', discover]).stdout)), 0))
    original = snapshot(pid)
    assert original['uid'] == 10001 and original['tracer'] == 0
    for mode, maximum in [('duration', 200), ('event_budget', 9), ('cancel', 200)]:
        token = secrets.token_hex(32)
        request = {'op': 'collect', 'token': token, 'kind': 'MEMORY_TRACE',
                   'parameters': {'pid': pid, 'duration_seconds': 2,
                                  'options': {'category': 'alloc', 'max_events': maximum,
                                              'stack_depth': 8}}}
        if mode == 'cancel':
            request['cancel_after_ms'] = 500
        request_code = "import pathlib,json; p=pathlib.Path('/tmp/request.json'); p.write_text(" + repr(json.dumps(request)) + ");p.chmod(0o644)"
        execute(['/usr/bin/python3', '-c', request_code])
        begin = time.monotonic()
        response = command(['docker', 'exec', '--user', '10001:10001', name,
                            '/fixture/drop_helper_probe', '/catalog.json',
                            '/run/drop-helper/control.sock', '/tmp/request.json',
                            '/tmp/client-' + mode], timeout=90, check=False)
        result = json.loads(response.stdout)
        item = {'case': mode, 'wall_seconds': round(time.monotonic() - begin, 3),
                'result': result}
        if mode != 'cancel':
            assert result['ok'] and result['metadata']['event_count'] <= maximum
            inspect = "import pathlib,json,hashlib,collections; p=pathlib.Path('/tmp/client-MODE/NAME'); d=p.read_bytes(); rows=[json.loads(l) for l in d.splitlines()]; print(json.dumps({'size':len(d),'sha256':hashlib.sha256(d).hexdigest(),'events':dict(collections.Counter(r['event'] for r in rows)),'pids':sorted(set(r['pid'] for r in rows)),'all_stacks_present':all(isinstance(r.get('stack'),list) and r['stack'] for r in rows),'native_allocations':all(r['event'] in ('alloc','free') and r.get('address',0)>0 and (r['event']!='alloc' or r.get('size',0)>0) for r in rows)}))".replace('MODE', mode).replace('NAME', result['name'])
            detail = json.loads(execute(['/usr/bin/python3', '-c', inspect]).stdout)
            assert detail['sha256'] == result['sha256'] and detail['size'] == result['size']
            assert detail['pids'] == [pid] and detail['all_stacks_present'] and detail['native_allocations']
            assert detail['events'].get('alloc', 0) and detail['events'].get('free', 0)
            item['raw_verification'] = detail
        else:
            assert not result['ok'] and result.get('error_code') == 'TASK_CANCELED'
        time.sleep(.1)
        state = snapshot(pid)
        time.sleep(.2)
        resumed = snapshot(pid)
        assert state['start'] == resumed['start'] == original['start']
        assert resumed['writes'] > state['writes'] and resumed['tracer'] == 0
        assert resumed['state'] not in ('T', 't', 'Z', 'X')
        check = "import pathlib,json; print(json.dumps([p.name for p in pathlib.Path('/proc/PID/root/tmp').glob('u.*') if p.is_dir() and p.stat().st_uid==0]))".replace('PID', str(pid))
        wait_for(lambda: not json.loads(execute(['/usr/bin/python3', '-c', check]).stdout)
                 and set(eventfile.read_text().splitlines()) == before_events, timeout=8)
        item.update(target_resumed=True, root_aliases_removed=True, probes_restored=True)
        reports.append(item)
        receipt.write_text(json.dumps({'cases': reports, 'scope': 'isolated_actual_helper'}, indent=2) + '\n')
        print(json.dumps({'case': mode, 'passed': True, 'wall_seconds': item['wall_seconds']}), flush=True)
finally:
    # Leave private RAW for receipt auditing; remove only our labelled containers.
    cleanup = []
    if name in started:
        label = command(['docker', 'inspect', '--format', '{{index .Config.Labels "io.drop.owner"}}', name], check=False)
        if label.returncode == 0 and label.stdout.strip() == owner:
            # Keep the target alive until the Helper has canceled and detached.
            command(['docker', 'stop', '--time', '8', name], timeout=15, check=False)
    for container in reversed(started):
        label = command(['docker', 'inspect', '--format', '{{index .Config.Labels "io.drop.owner"}}', container], check=False)
        if label.returncode == 0 and label.stdout.strip() == owner:
            cleanup.append(command(['docker', 'rm', '-f', container], check=False).returncode == 0)
        else:
            cleanup.append(label.returncode != 0)
    if not all(cleanup) or set(eventfile.read_text().splitlines()) != before_events:
        raise RuntimeError('Owned allocator fixture cleanup incomplete')
