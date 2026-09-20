FROM mini-drop-apiserver:latest
USER root
COPY mini-drop-apiserver /usr/local/bin/mini-drop-apiserver
RUN chmod 755 /usr/local/bin/mini-drop-apiserver
USER 65532:65532
