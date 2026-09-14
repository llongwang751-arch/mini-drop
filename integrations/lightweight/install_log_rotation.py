"""Install on the control host as root. Keeps request observations bounded."""
import json
import pathlib
import shutil
import subprocess

def install():
    if shutil.which('logrotate') is None:
        raise RuntimeError('logrotate must be installed before enabling business logs')
    root=pathlib.Path('/var/log/mini-drop-business')
    root.mkdir(mode=0o750,exist_ok=True)
    config=pathlib.Path('/etc/mini-drop-business/requests.logrotate')
    config.parent.mkdir(mode=0o700,exist_ok=True)
    config.write_text('''/var/log/mini-drop-business/*.jsonl {
    size 5M
    rotate 3
    missingok
    notifempty
    compress
    delaycompress
    create 0640 root root
    sharedscripts
    postrotate
        /usr/bin/docker kill --signal=USR1 mini-drop-control-web-1 >/dev/null
    endscript
}
''')
    unit=pathlib.Path('/etc/systemd/system/mini-drop-business-logrotate.service')
    unit.write_text('''[Unit]
Description=Rotate Mini-Drop business request observations
[Service]
Type=oneshot
ExecStart=/usr/sbin/logrotate --state /var/lib/mini-drop-business-logrotate.state /etc/mini-drop-business/requests.logrotate
''')
    timer=unit.with_suffix('.timer')
    timer.write_text('''[Unit]
Description=Check Mini-Drop business observation size every five minutes
[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
AccuracySec=30s
[Install]
WantedBy=timers.target
''')
    subprocess.run(['logrotate','--debug',str(config)],check=True,capture_output=True)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now',timer.name],check=True,capture_output=True)
    subprocess.run(['systemctl','start',unit.name],check=True)
    print(json.dumps({'timer':timer.name,'status':subprocess.check_output(['systemctl','is-active',timer.name],text=True).strip(),
                      'threshold_bytes':5*1024*1024,'archives_per_service':3,'check_seconds':300,
                      'note':'Size threshold checked periodically, not a hard disk quota; current-file API reads remain capped at 2 MiB.'}))

if __name__=='__main__':install()
