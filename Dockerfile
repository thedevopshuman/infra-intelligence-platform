FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ARG IIP_IMAGE_VERSION=development
ARG IIP_IMAGE_REVISION=development

LABEL org.opencontainers.image.title="Infrastructure Intelligence Platform" \
    org.opencontainers.image.version="$IIP_IMAGE_VERSION" \
    org.opencontainers.image.revision="$IIP_IMAGE_REVISION"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    IIP_BUILD_REVISION="$IIP_IMAGE_REVISION" \
    IIP_HTTP_HOST=0.0.0.0 \
    IIP_HTTP_PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir . \
    && python -c "import psycopg; assert psycopg.pq.__impl__ == 'python'" \
    && addgroup --system --gid 10001 iip \
    && adduser --system --uid 10001 --ingroup iip --home /nonexistent --no-create-home iip

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "iip.surfaces.http"]
