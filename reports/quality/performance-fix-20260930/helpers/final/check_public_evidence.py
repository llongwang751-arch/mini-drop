"""Check known credentials in public artifacts, including ZIP members, without printing secrets."""
from pathlib import Path
import importlib.util
import io
import json
import zipfile


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


provider = module('provider', 'output/acceptance/deployment-20260930/run_strict.py')
ci = module('ci', 'output/quality/ci-validation-20260927/github_ci.py')
client = provider.authenticated_client()
github = ci.session()
secrets = [client._key.encode(), github.auth[1].encode()]
assert all(len(secret) >= 16 for secret in secrets)
matches = []
checked = 0


def scan(raw, path, depth=0):
    global checked
    checked += 1
    if any(secret in raw for secret in secrets):
        matches.append(path)
    if raw.startswith(b'PK\x03\x04') and depth < 2:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                if not name.endswith('/'):
                    scan(archive.read(name), path + '::' + name, depth + 1)


for file in sorted(Path('reports/quality/performance-fix-20260930').rglob('*')):
    if file.is_file():
        scan(file.read_bytes(), file.as_posix())
assert not matches, 'known credential found in public evidence paths: ' + json.dumps(matches)
print(json.dumps({'known_credentials_absent': True, 'files_and_zip_members_checked': checked}))
