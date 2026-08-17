FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ARG IIP_IMAGE_VERSION=development
ARG IIP_IMAGE_REVISION=development

LABEL org.opencontainers.image.title="Infrastructure Intelligence Platform plugin mediation bridge" \
    org.opencontainers.image.version="$IIP_IMAGE_VERSION" \
    org.opencontainers.image.revision="$IIP_IMAGE_REVISION" \
    iip.platform.component="plugin-mediation-bridge"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY deploy/plugin-mediation-bridge/bridge.py /bridge.py

ENTRYPOINT ["python", "/bridge.py"]
