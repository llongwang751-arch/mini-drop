# Public repository build: official PyPI wheels are hash checked online.
# Validated release archives use the same versions with preverified offline wheels.
FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PSYCOPG_IMPL=python
WORKDIR /app
COPY ops/apt-bounds.conf /etc/apt/apt.conf.d/80drop-bounds
RUN sed -i 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g; s|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g; s|http://security.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' /etc/apt/sources.list.d/debian.sources \
    && grep -F 'Suites: trixie trixie-updates' /etc/apt/sources.list.d/debian.sources \
    && grep -F 'Suites: trixie-security' /etc/apt/sources.list.d/debian.sources \
    && grep -F 'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends "libpq5=17.11-0+deb13u1" \
    && rm -rf /var/lib/apt/lists/*
COPY ops/requirements.txt ops/requirements.lock ./
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes --index-url https://pypi.org/simple -r requirements.lock && pip check && groupadd --gid 10001 drop && useradd --uid 10001 --gid drop --no-create-home --shell /usr/sbin/nologin drop
COPY --chown=10001:10001 ops/maintenance.py ops/reconcile.py ops/encrypt_existing.py ops/ops_runtime.py ./
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD ["python","ops_runtime.py","--health"]
CMD ["python","maintenance.py"]
