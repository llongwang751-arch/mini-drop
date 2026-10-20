FROM drop-agent:20261020.1
USER root
RUN /opt/drop-tools/venv/bin/pip install --disable-pip-version-check --index-url https://mirrors.aliyun.com/pypi/simple/ pytest==9.1.1
COPY source /opt/drop-saber
COPY harness /harness
ENV PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["/opt/drop-tools/venv/bin/python", "/harness/saber_workload.py"]
