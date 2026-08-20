# Mini-Drop Server Dockerfile
FROM python:3.11-slim

ARG DEBIAN_MIRROR=""

# 海外环境默认使用 Debian 官方源；国内云主机可通过构建参数传入
# https://mirrors.aliyun.com，避免首次安装 perf 等系统依赖耗时过长。
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    gosu \
    linux-perf \
    perl \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户运行服务
RUN useradd --create-home --shell /bin/bash mini-drop

WORKDIR /app

COPY pyproject.toml ./
RUN python -c "import subprocess,sys,tomllib; data=tomllib.load(open('pyproject.toml','rb')); subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*data['project']['dependencies'],'grpcio-tools>=1.80,<1.81'])"

COPY README.md ./
COPY alembic.ini ./
COPY server/ ./server/
COPY agent/ ./agent/
COPY analyzer/ ./analyzer/

RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

COPY proto/ ./proto/
RUN cd proto && bash compile.sh

# AI 诊断离线质量门禁需要随镜像携带版本化 Golden 数据集。
COPY golden_scenarios/ ./golden_scenarios/
# 所有 AI 策略共享的统一诊断测试集目录。
COPY benchmarks/ ./benchmarks/
# 在线诊断与 Golden 回归共用的可追溯领域知识库。
COPY knowledge/ ./knowledge/

COPY deploy/scripts/server-entrypoint.sh /usr/local/bin/server-entrypoint
RUN chmod 0755 /usr/local/bin/server-entrypoint

EXPOSE 8191 50051

ENTRYPOINT ["server-entrypoint"]
CMD ["python", "-m", "server.app.main"]
