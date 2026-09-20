# Upgrade an existing, pinned production runtime without rebuilding native tools.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY release-wheels/ /tmp/release-wheels/
RUN python -c "import subprocess,sys,tomllib,json,hashlib; from pathlib import Path; w=Path('/tmp/release-wheels'); hashes=json.loads((w/'sha256.json').read_text()); assert all(hashlib.sha256((w/n).read_bytes()).hexdigest()==h for n,h in hashes.items()); p=tomllib.load(open('pyproject.toml','rb'))['project']; subprocess.check_call([sys.executable,'-m','pip','install','--no-index','--find-links',str(w),'--no-cache-dir',*p['dependencies'],*p['optional-dependencies']['retrieval']])"
COPY server/ ./server/
COPY analyzer/ ./analyzer/
COPY scripts/ ./scripts/
COPY knowledge/ ./knowledge/
COPY skills/ ./skills/
RUN python -c "import chromadb; from server.app.agent_runtime.runtime import *; from pathlib import Path; assert Path('server/app/diagnosis_worker.py').stat().st_size > 1000; print(chromadb.__version__)" && sync
