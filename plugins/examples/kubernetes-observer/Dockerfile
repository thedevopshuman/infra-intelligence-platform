FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/plugin/src:/sdk

WORKDIR /plugin
COPY sdks/python/src/infra_intelligence_sdk /sdk/infra_intelligence_sdk
COPY plugins/examples/kubernetes-observer/src ./src
COPY plugins/examples/kubernetes-observer/fixtures/kubernetes-list.json ./fixtures/kubernetes-list.json

USER 65532:65532
ENTRYPOINT ["python", "-m", "kubernetes_observer", "--stdio", "--objects", "/plugin/fixtures/kubernetes-list.json"]
