FROM alpine:3.24.1 AS cpp-demo-symbols
ARG ALPINE_MIRROR=https://mirrors.aliyun.com/alpine
RUN sed -i "s|https://dl-cdn.alpinelinux.org/alpine|${ALPINE_MIRROR}|g" /etc/apk/repositories \
    && apk add --no-cache g++
WORKDIR /src
COPY demo/cpp-hotspot/main.cpp demo/cpp-hotspot/build.sh ./
RUN chmod 0755 build.sh && ./build.sh /out/cpp-hotspot

FROM python:3.11-slim

ARG DEBIAN_MIRROR=""
ARG DEBIAN_SECURITY_MIRROR=""
ARG PIP_INDEX_URL=""

RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) \
          -exec sed -ri \
            -e "s@https?://deb.debian.org/debian@${DEBIAN_MIRROR}@g" \
            -e "s@https?://security.debian.org/debian-security@${DEBIAN_SECURITY_MIRROR:-$DEBIAN_MIRROR}@g" \
            {} +; \
    fi

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
COPY skills/ ./skills/
COPY knowledge/ ./knowledge/
RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

# `perf.data` records the target path and build ID, not the ELF symbol table.
# The controlled C++ lab binary is copied into the Analyzer image at the same
# path so the page can render function-level C++ frames.  Arbitrary production
# targets still require their own symbol/debug-image distribution workflow.
COPY --from=cpp-demo-symbols /out/cpp-hotspot /usr/local/bin/cpp-hotspot

COPY deploy/scripts/python-worker-entrypoint.sh /usr/local/bin/python-worker-entrypoint
RUN chmod 0755 /usr/local/bin/python-worker-entrypoint

ENTRYPOINT ["python-worker-entrypoint"]
CMD ["python", "-m", "server.app.analysis_jobs"]
