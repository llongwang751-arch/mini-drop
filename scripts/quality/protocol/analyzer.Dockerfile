# 只服务 folded 采样分析门禁，沿用生产 Python 基础镜像与完整哈希依赖锁。
# 不安装本门禁不会调用的 JDK/BOLT/perf，不能作为生产 Analyzer 发布镜像。
FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PSYCOPG_IMPL=python
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5=17.11-0+deb13u1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY analyzer/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock && pip check && groupadd --gid 10001 drop && useradd --uid 10001 --gid drop --no-create-home drop
COPY --chown=10001:10001 analyzer/drop_analyzer ./drop_analyzer
USER 10001:10001
