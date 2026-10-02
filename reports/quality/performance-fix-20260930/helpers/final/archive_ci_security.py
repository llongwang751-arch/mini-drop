from pathlib import Path
stage=Path(__file__).resolve().parent
source=(stage/'archive_ci_transport.py').read_text(encoding='utf-8')
source=source.replace("stage/'multi/ci-run.json'", "stage/'web-security/ci-run.json'")
source=source.replace('/transport-ci', '/security-ci')
source=source.replace('52881ec2db0ad3622f00e9e55e5bff1e1fe71391','7403815b3dbab23f7a39c3d1e757195c1e12f269')
exec(compile(source, str(Path(__file__)), 'exec'))
