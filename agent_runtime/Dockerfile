FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OTEL_SDK_DISABLED=true
WORKDIR /app
COPY agent_runtime/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes --index-url "$PIP_INDEX_URL" -r requirements.lock \
    && pip check \
    && groupadd --gid 10002 drop-runtime \
    && groupadd --gid 10003 drop-runtime-sockets \
    && useradd --uid 10002 --gid 10002 --groups 10003 --no-create-home --shell /usr/sbin/nologin drop-runtime
COPY --chown=10002:10002 agent_runtime/drop_agent_runtime ./drop_agent_runtime
USER 10002:10002
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-m", "drop_agent_runtime.health"]
ENTRYPOINT ["python", "-m", "drop_agent_runtime"]
