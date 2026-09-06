FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements/bedrock-compatibility.txt /tmp/bedrock-compatibility.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/bedrock-compatibility.txt

COPY instrumentation/python/aws-bedrock ./instrumentation/python/aws-bedrock
RUN python -m pip install --no-cache-dir --no-deps ./instrumentation/python/aws-bedrock

COPY pyproject.toml README.md ./
COPY src ./src
RUN addgroup --system --gid 10001 iip \
    && adduser --system --uid 10001 --ingroup iip --home /nonexistent --no-create-home iip

COPY contracts ./contracts
COPY scripts/run_bedrock_instrumentation_compatibility.py ./scripts/run_bedrock_instrumentation_compatibility.py
COPY scripts/run_bedrock_delivery_self_test.py ./scripts/run_bedrock_delivery_self_test.py

USER 10001:10001
ENTRYPOINT ["python", "scripts/run_bedrock_instrumentation_compatibility.py"]
