FROM python:3.11-slim

ARG PIP_INDEX_URL=""

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash gosu linux-perf perl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash mini-drop

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
RUN if [ -n "$PIP_INDEX_URL" ]; then export PIP_INDEX_URL; fi; \
    python -c "import subprocess,sys,tomllib; data=tomllib.load(open('pyproject.toml','rb')); subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*data['project']['dependencies']])"

COPY server/ ./server/
COPY analyzer/ ./analyzer/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

COPY deploy/scripts/python-worker-entrypoint.sh /usr/local/bin/python-worker-entrypoint
RUN chmod 0755 /usr/local/bin/python-worker-entrypoint

ENTRYPOINT ["python-worker-entrypoint"]
CMD ["python", "-m", "server.app.analysis_jobs"]
