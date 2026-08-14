FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    IIP_HTTP_HOST=0.0.0.0 \
    IIP_HTTP_PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && addgroup --system --gid 10001 iip \
    && adduser --system --uid 10001 --ingroup iip --home /nonexistent --no-create-home iip

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "iip.surfaces.http"]
