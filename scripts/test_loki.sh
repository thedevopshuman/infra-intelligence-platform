#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_COMPOSE_FILE=deploy/docker-compose.loki.yml
IIP_COMPOSE_PROJECT=iip-loki-test

cleanup() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" down --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the Loki integration test" >&2
    exit 2
fi

cleanup
"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --detach

IIP_TEST_LOKI_ENDPOINT=http://127.0.0.1:13100 \
PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest tests.test_loki_integration -v

echo "Loki integration test returned normalized log evidence"
