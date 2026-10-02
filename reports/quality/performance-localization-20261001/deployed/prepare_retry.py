from pathlib import Path

stage = Path(__file__).resolve().parent
old = stage / 'release'
new = stage / 'release-r2'
new.mkdir(exist_ok=False)
for name in ('activate_platform.py', 'ci_status.py', 'prepare_release.py'):
    (new / name).write_bytes((old / name).read_bytes())
deploy = (old / 'deploy_runtime.py').read_text(encoding='utf-8')
begin = deploy.index("        labels = obj['Config']['Labels']")
end = deploy.index("        for key in ('build'", begin)
existing = deploy[begin:end]
replacement = """        if service == 'go-hotspot':
            # This older container's Compose env-file labels no longer resolve.
            # Recover its bounded runtime from inspect, without new privileges.
            host = obj['HostConfig']
            assert not obj['Mounts'] and not host['Privileged'] and not host['PidMode']
            assert not host.get('Sysctls') and not host.get('CapAdd') and not host.get('Ulimits')
            assert host['IpcMode'] == 'private' and not host['CpuQuota'] and not host['CpuPeriod']
            assert len(obj['NetworkSettings']['Networks']) == 1
            check = obj['Config']['Healthcheck']
            spec = {'restart': host['RestartPolicy']['Name'], 'read_only': host['ReadonlyRootfs'],
                    'tmpfs': [path + ':' + options for path, options in host['Tmpfs'].items()],
                    'cap_drop': host['CapDrop'], 'security_opt': host['SecurityOpt'],
                    'mem_limit': host['Memory'], 'cpus': host['NanoCpus'] / 1e9,
                    'pids_limit': host['PidsLimit'], 'working_dir': obj['Config']['WorkingDir'],
                    'healthcheck': {'test': check['Test'], 'interval': str(check['Interval']) + 'ns',
                                    'timeout': str(check['Timeout']) + 'ns', 'retries': check['Retries']},
                    'ports': [binding['HostIp'] + ':' + binding['HostPort'] + ':' + port
                              for port, bindings in host['PortBindings'].items() for binding in bindings],
                    'logging': {'driver': host['LogConfig']['Type'], 'options': host['LogConfig']['Config']}}
        else:
""" + ''.join('    ' + line + '\n' for line in existing.splitlines())
deploy = deploy[:begin] + replacement + deploy[end:]
(new / 'deploy_runtime.py').write_text(deploy, encoding='utf-8')
for name in ('verify_runtime.py', 'verify_go.py', 'live_acceptance.py', 'collect_ci.py'):
    path = stage / name
    text = path.read_text(encoding='utf-8').replace('release/', 'release-r2/')
    path.write_text(text, encoding='utf-8')
