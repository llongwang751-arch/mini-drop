# Fast, reproducible source-only release layer.
#
# The base image already contains the dependency set built by
# python-worker.Dockerfile.  Use this overlay when a release changes only
# Python application code, so deployment does not depend on package mirrors
# being reachable at that moment.
ARG BASE_IMAGE=mini-drop-python-worker:local
FROM ${BASE_IMAGE}

COPY server/ /app/server/
