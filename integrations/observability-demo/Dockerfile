# 新演示镜像不覆盖旧Agent、CRM或Saber镜像；只使用本机已缓存Python运行时。
ARG DROP_PYTHON_BASE=drop-agent:20261020.1
FROM ${DROP_PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY server.py load.py /opt/drop-observability-demo/
USER 10001:10001
ENTRYPOINT ["/opt/drop-tools/venv/bin/python", "/opt/drop-observability-demo/server.py"]
