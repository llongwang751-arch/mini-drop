ARG BASE_IMAGE=mini-drop-python-worker:local
FROM ${BASE_IMAGE}
USER root
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
RUN python -c "import subprocess,sys,tomllib; p=tomllib.load(open('pyproject.toml','rb'))['project']; subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir','--force-reinstall',*p['dependencies'],*p['optional-dependencies']['retrieval']])"
COPY server/ ./server/
COPY analyzer/ ./analyzer/
COPY scripts/ ./scripts/
COPY skills/ ./skills/
COPY knowledge/ ./knowledge/
COPY deploy/scripts/python-worker-entrypoint.sh /usr/local/bin/python-worker-entrypoint
RUN python -c "from pathlib import Path; p=Path('/usr/local/bin/python-worker-entrypoint'); b=p.read_bytes().replace(b'\r\n',b'\n'); assert b.startswith(b'#!/bin/sh'); p.write_bytes(b); p.chmod(0o755)"
RUN python -c "from pathlib import Path; import chromadb,sqlalchemy; assert Path('/app/server/app/diagnosis_worker.py').stat().st_size > 1000; print(chromadb.__version__,sqlalchemy.__version__)" && sync
