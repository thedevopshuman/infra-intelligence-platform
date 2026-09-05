FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements/openai-compatibility.txt /tmp/openai-compatibility.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/openai-compatibility.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN addgroup --system --gid 10001 iip \
    && adduser --system --uid 10001 --ingroup iip --home /nonexistent --no-create-home iip

COPY contracts ./contracts
COPY scripts/run_openai_instrumentation_compatibility.py ./scripts/run_openai_instrumentation_compatibility.py

USER 10001:10001
ENTRYPOINT ["python", "scripts/run_openai_instrumentation_compatibility.py"]
