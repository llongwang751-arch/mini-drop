FROM drop-agent:20261020.1
USER root
COPY requirements.lock /tmp/crm-requirements.lock
RUN /opt/drop-tools/venv/bin/pip install --disable-pip-version-check --index-url https://mirrors.aliyun.com/pypi/simple/ -r /tmp/crm-requirements.lock pytest==9.1.1 && rm /tmp/crm-requirements.lock
COPY source /opt/drop-crm
COPY harness /harness
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/drop-crm
USER 10001:10001
ENTRYPOINT ["/opt/drop-tools/venv/bin/python","/harness/crm_workload.py"]
