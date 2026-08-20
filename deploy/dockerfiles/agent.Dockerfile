# Mini-Drop Agent Dockerfile
#
# 安全说明：Agent 需要运行 perf / bpftrace / py-spy 等内核级工具，
# 这些工具依赖 CAP_SYS_PTRACE 和 CAP_PERFMON capability。
# 因此 Agent 容器以 root 运行（Docker compose 中通过 cap_add 限制权限）。
# 生产环境应评估是否可使用 ambient capabilities 替代 root。
FROM python:3.11-slim

ARG PIP_INDEX_URL=""

RUN sed -i \
    -e 's|deb.debian.org/debian|mirrors.aliyun.com/debian|g' \
    -e 's|security.debian.org/debian-security|mirrors.aliyun.com/debian-security|g' \
    /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    bpftrace \
    curl \
    linux-perf \
    perl \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/vendor/async-profiler-4.4-linux-x64.tar.gz /tmp/async-profiler.tar.gz
RUN set -eux; \
    echo "1233f26fc95753e75ce32733bbcaf8f0bedc2c098b0e798af87935b08a63b24e  /tmp/async-profiler.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/async-profiler; \
    tar -xzf /tmp/async-profiler.tar.gz --strip-components=1 -C /opt/async-profiler; \
    ln -s /opt/async-profiler/bin/asprof /usr/local/bin/asprof; \
    rm /tmp/async-profiler.tar.gz; \
    asprof --version

ENV ASYNC_PROFILER_HOME=/opt/async-profiler

WORKDIR /app
COPY pyproject.toml ./
RUN if [ -n "$PIP_INDEX_URL" ]; then export PIP_INDEX_URL; fi; \
    python -c "import subprocess,sys,tomllib; data=tomllib.load(open('pyproject.toml','rb')); subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*data['project']['dependencies'],'grpcio-tools>=1.80,<1.81'])"

COPY README.md ./
COPY server/ ./server/
COPY agent/ ./agent/
COPY analyzer/ ./analyzer/
RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

COPY proto/ ./proto/
RUN cd proto && bash compile.sh

CMD ["python", "-m", "agent.mini_drop_agent.main"]
