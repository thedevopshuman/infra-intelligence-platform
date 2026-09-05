#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_COMPOSE_FILE=deploy/docker-compose.otel.yml
IIP_COMPOSE_PROJECT=iip-otel-test
IIP_OTEL_TEST_PORT=${IIP_OTEL_TEST_PORT:-24318}
export IIP_OTEL_TEST_PORT

cleanup() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" down --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the OTLP integration test" >&2
    exit 2
fi

cleanup
"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --detach

attempt=0
until "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" logs --no-color collector 2>&1 \
    | rg -q "Everything is ready"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 15 ]; then
        "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
            -f "$IIP_COMPOSE_FILE" logs --no-color collector >&2
        echo "OpenTelemetry Collector did not become ready" >&2
        exit 1
    fi
    sleep 1
done

IIP_TEST_OTEL_ENDPOINT="http://127.0.0.1:$IIP_OTEL_TEST_PORT/v1/metrics" \
IIP_TEST_OTEL_TRACES_ENDPOINT="http://127.0.0.1:$IIP_OTEL_TEST_PORT/v1/traces" \
PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest tests.test_otel_collector -v

attempt=0
until "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" logs --no-color collector 2>&1 \
    | rg -q "iip.ingestion.checkpoint.age"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 15 ]; then
        "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
            -f "$IIP_COMPOSE_FILE" logs --no-color collector >&2
        echo "Collector did not receive the expected IIP metric" >&2
        exit 1
    fi
    sleep 1
done

attempt=0
until "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" logs --no-color collector 2>&1 \
    | rg -q "iip.ai.cost.amount"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 15 ]; then
        "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
            -f "$IIP_COMPOSE_FILE" logs --no-color collector >&2
        echo "Collector did not receive the expected IIP AI economics metric" >&2
        exit 1
    fi
    sleep 1
done

attempt=0
until "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" logs --no-color collector 2>&1 \
    | rg -q "iip.query.requests"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 15 ]; then
        "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
            -f "$IIP_COMPOSE_FILE" logs --no-color collector >&2
        echo "Collector did not receive the expected IIP query metric" >&2
        exit 1
    fi
    sleep 1
done

attempt=0
until "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" logs --no-color collector 2>&1 \
    | rg -q "iip.investigation.execute"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 15 ]; then
        "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
            -f "$IIP_COMPOSE_FILE" logs --no-color collector >&2
        echo "Collector did not receive the expected IIP trace" >&2
        exit 1
    fi
    sleep 1
done

IIP_DOCKER_BIN="$IIP_DOCKER_BIN" \
IIP_OTEL_TEST_PORT="$IIP_OTEL_TEST_PORT" \
PYTHONPATH=.:src:sdks/python/src \
    "$IIP_TEST_PYTHON" scripts/test_otel_export_health.py

echo "OTLP integration test received IIP signals and proved delivery-health recovery"
