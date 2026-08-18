#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_COMPOSE_FILE=deploy/docker-compose.collector-queue-loss.yml
IIP_COMPOSE_PROJECT=iip-collector-queue-loss-test
IIP_COLLECTOR_QUEUE_LOSS_OTLP_PORT=${IIP_COLLECTOR_QUEUE_LOSS_OTLP_PORT:-24320}
IIP_COLLECTOR_QUEUE_LOSS_PROMETHEUS_PORT=${IIP_COLLECTOR_QUEUE_LOSS_PROMETHEUS_PORT:-19091}
export IIP_COLLECTOR_QUEUE_LOSS_OTLP_PORT
export IIP_COLLECTOR_QUEUE_LOSS_PROMETHEUS_PORT

cleanup() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" down --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the Collector queue/loss integration test" >&2
    exit 2
fi

cleanup
"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --detach --wait

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

IIP_TEST_COLLECTOR_OTLP_ENDPOINT="http://127.0.0.1:$IIP_COLLECTOR_QUEUE_LOSS_OTLP_PORT" \
IIP_TEST_PROMETHEUS_ENDPOINT="http://127.0.0.1:$IIP_COLLECTOR_QUEUE_LOSS_PROMETHEUS_PORT" \
PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest tests.test_collector_queue_loss_integration -v

echo "Collector queue/loss integration test returned a real breached objective from the Collector's own self-metrics"
