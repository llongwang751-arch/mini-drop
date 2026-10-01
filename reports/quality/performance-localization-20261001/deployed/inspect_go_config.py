import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('p', Path('output/acceptance/deployment-20260930/run_strict.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
print(p.remote('''import json,subprocess
o=json.loads(subprocess.check_output(['docker','inspect','mini-drop-control-go-hotspot-1']))[0]
print(json.dumps({'config':{k:o['Config'].get(k) for k in ['User','WorkingDir','Healthcheck','Entrypoint','Cmd','ExposedPorts']},
'host':{k:o['HostConfig'].get(k) for k in ['RestartPolicy','PortBindings','ReadonlyRootfs','Tmpfs','CapAdd','CapDrop','SecurityOpt','Sysctls','Privileged','PidMode','IpcMode','Memory','NanoCpus','CpuQuota','CpuPeriod','PidsLimit','Ulimits','Init','LogConfig']},
'mounts':o['Mounts'],'networks':{k:v.get('Aliases') for k,v in o['NetworkSettings']['Networks'].items()}}))
'''))
