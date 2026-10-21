# 新协议镜像单独发布，不覆盖旧 CRM/Saber runner 镜像或冻结业务源码。
ARG DROP_PYTHON_BASE=drop-agent:20261020.1
FROM ${DROP_PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY harness /harness
USER 10001:10001
ENTRYPOINT ["/opt/drop-tools/venv/bin/python", "/harness/run_job.py"]
