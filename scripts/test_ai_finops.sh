#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_COMPOSE_FILE=deploy/docker-compose.ai-finops.yml
IIP_COMPOSE_PROJECT=iip-ai-finops-test
IIP_AI_FINOPS_ANCHOR=$(date -u '+%Y-%m-%dT%H:%M:00Z')
IIP_AI_FINOPS_POSTGRES_PORT=${IIP_AI_FINOPS_TEST_POSTGRES_PORT:-25435}
IIP_AI_FINOPS_API_PORT=${IIP_AI_FINOPS_TEST_API_PORT:-28082}
IIP_AI_FINOPS_RECEIVER_PORT=${IIP_AI_FINOPS_TEST_RECEIVER_PORT:-24320}
IIP_AI_FINOPS_COLLECTOR_PORT=${IIP_AI_FINOPS_TEST_COLLECTOR_PORT:-24319}
IIP_AI_FINOPS_COLLECTOR_HEALTH_PORT=${IIP_AI_FINOPS_TEST_COLLECTOR_HEALTH_PORT:-23134}
IIP_AI_FINOPS_PROMETHEUS_PORT=${IIP_AI_FINOPS_TEST_PROMETHEUS_PORT:-29091}
IIP_AI_FINOPS_LOKI_PORT=${IIP_AI_FINOPS_TEST_LOKI_PORT:-23101}
IIP_AI_FINOPS_GRAFANA_PORT=${IIP_AI_FINOPS_TEST_GRAFANA_PORT:-23000}
export IIP_AI_FINOPS_ANCHOR IIP_AI_FINOPS_POSTGRES_PORT IIP_AI_FINOPS_API_PORT
export IIP_AI_FINOPS_RECEIVER_PORT IIP_AI_FINOPS_COLLECTOR_PORT
export IIP_AI_FINOPS_COLLECTOR_HEALTH_PORT IIP_AI_FINOPS_PROMETHEUS_PORT
export IIP_AI_FINOPS_LOKI_PORT IIP_AI_FINOPS_GRAFANA_PORT

export IIP_AI_USAGE_CHANNEL_TOKEN
IIP_AI_USAGE_CHANNEL_TOKEN=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration token \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_AI_USAGE_RECEIVER_CHANNELS_JSON
IIP_AI_USAGE_RECEIVER_CHANNELS_JSON=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration channel \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_AI_ATTRIBUTION_POLICIES_JSON
IIP_AI_ATTRIBUTION_POLICIES_JSON=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration attribution \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_AI_PRICE_CATALOGS_JSON
IIP_AI_PRICE_CATALOGS_JSON=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration catalog \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_AI_SAVINGS_PROFILES_JSON
IIP_AI_SAVINGS_PROFILES_JSON=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration profiles \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)

cleanup() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" down --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the AI FinOps vertical-slice gate" >&2
    exit 2
fi

cleanup
"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" config --quiet
"$IIP_DOCKER_BIN" run --rm --network none --read-only \
    -e IIP_AI_USAGE_CHANNEL_TOKEN="$IIP_AI_USAGE_CHANNEL_TOKEN" \
    -v "$PWD/deploy/otel/ai-finops-collector.yaml:/etc/otelcol-contrib/config.yaml:ro" \
    otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5 \
    validate --config=/etc/otelcol-contrib/config.yaml
"$IIP_DOCKER_BIN" run --rm --network none --read-only \
    --entrypoint /bin/promtool \
    -v "$PWD/deploy/prometheus/ai-finops-prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
    prom/prometheus:v3.13.1 \
    check config /etc/prometheus/prometheus.yml

"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --build --detach --wait

IIP_AI_FINOPS_READY=false
IIP_AI_FINOPS_READY_ATTEMPT=0
while [ "$IIP_AI_FINOPS_READY_ATTEMPT" -lt 30 ]; do
    if "$IIP_TEST_PYTHON" -c \
        "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['IIP_AI_FINOPS_COLLECTOR_HEALTH_PORT'] + '/', timeout=2)" \
        >/dev/null 2>&1; then
        IIP_AI_FINOPS_READY=true
        break
    fi
    IIP_AI_FINOPS_READY_ATTEMPT=$((IIP_AI_FINOPS_READY_ATTEMPT + 1))
    sleep 1
done
if [ "$IIP_AI_FINOPS_READY" != true ]; then
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" logs --no-color >&2
    echo "AI FinOps Collector did not become ready" >&2
    exit 1
fi

PYTHONPATH=src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/ai_finops_fixture.py send \
    --anchor "$IIP_AI_FINOPS_ANCHOR" \
    --collector "http://127.0.0.1:${IIP_AI_FINOPS_COLLECTOR_PORT}" \
    --receiver "http://127.0.0.1:${IIP_AI_FINOPS_RECEIVER_PORT}"

if ! PYTHONPATH=src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/ai_finops_fixture.py verify \
    --database "postgresql://iip@127.0.0.1:${IIP_AI_FINOPS_POSTGRES_PORT}/iip" \
    --prometheus "http://127.0.0.1:${IIP_AI_FINOPS_PROMETHEUS_PORT}" \
    --grafana "http://127.0.0.1:${IIP_AI_FINOPS_GRAFANA_PORT}" \
    --loki "http://127.0.0.1:${IIP_AI_FINOPS_LOKI_PORT}" \
    --timeout-seconds 60; then
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" logs --no-color >&2
    exit 1
fi

echo "Bedrock-shaped OTLP usage, protected attribution, exact cost, visible unpriced/unallocated coverage, evidence-backed saving, and Grafana dashboard passed"
