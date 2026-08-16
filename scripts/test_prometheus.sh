#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_COMPOSE_FILE=deploy/docker-compose.prometheus.yml
IIP_COMPOSE_PROJECT=iip-prometheus-test

cleanup() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" down --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the Prometheus integration test" >&2
    exit 2
fi

cleanup
"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --detach --wait

IIP_TEST_PROMETHEUS_ENDPOINT=http://127.0.0.1:19090 \
PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest tests.test_prometheus_integration -v

echo "Prometheus integration test returned normalized metric evidence"
