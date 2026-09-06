#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_COMPOSE_FILE=deploy/docker-compose.test.yml

cleanup() {
    "$IIP_DOCKER_BIN" compose -f "$IIP_COMPOSE_FILE" down --volumes >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM
if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker CLI not found; install and start Docker Desktop" >&2
    exit 127
fi
"$IIP_DOCKER_BIN" compose -f "$IIP_COMPOSE_FILE" up --detach --wait
IIP_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/iip_test \
    PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest discover -s tests -p 'test_postgres_store.py' -v
IIP_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/iip_test \
    PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest \
    tests.test_ai_attribution.AiAttributionPostgresTests -v
IIP_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/iip_test \
    PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest \
    tests.test_ai_cost_engine.AiCostPostgresTests -v
IIP_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/iip_test \
    PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest \
    tests.test_ai_savings_engine.AiSavingsPostgresTests -v
IIP_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/iip_test \
    PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest \
    tests.test_ai_allocation_queries.AiAllocationPostgresTests -v
IIP_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/iip_test \
    PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest \
    tests.test_ai_invocation_observation.AiInvocationObservationPostgresTests -v
IIP_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/iip_test \
    PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest \
    tests.test_ai_savings_queries.AiSavingsFindingPostgresTests -v
