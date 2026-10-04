#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_TEST_POSTGRES_TLS_PORT=${IIP_TEST_POSTGRES_TLS_PORT:-55434}
IIP_TEST_POSTGRES_PLAINTEXT_PORT=${IIP_TEST_POSTGRES_PLAINTEXT_PORT:-55435}
IIP_REPOSITORY_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
IIP_COMPOSE_FILE="$IIP_REPOSITORY_ROOT/deploy/docker-compose.postgres-tls.test.yml"
IIP_COMPOSE_PROJECT="iip-postgres-tls-test-$$"
IIP_TEST_TEMP_DIR=

cleanup() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" down --volumes --remove-orphans \
        >/dev/null 2>&1 || true
    if [ -n "$IIP_TEST_TEMP_DIR" ]; then
        case "$IIP_TEST_TEMP_DIR" in
            "${TMPDIR:-/tmp}"/iip-postgres-tls.*)
                rm -f \
                    "$IIP_TEST_TEMP_DIR/ca.crt" \
                    "$IIP_TEST_TEMP_DIR/client-ca.crt" \
                    "$IIP_TEST_TEMP_DIR/server.crt" \
                    "$IIP_TEST_TEMP_DIR/server.key" \
                    "$IIP_TEST_TEMP_DIR/untrusted/ca.crt" \
                    "$IIP_TEST_TEMP_DIR/untrusted/client-ca.crt" \
                    "$IIP_TEST_TEMP_DIR/untrusted/server.crt" \
                    "$IIP_TEST_TEMP_DIR/untrusted/server.key"
                rmdir "$IIP_TEST_TEMP_DIR/untrusted" \
                    "$IIP_TEST_TEMP_DIR" >/dev/null 2>&1 || true
                ;;
            *)
                echo "Refusing to remove unexpected PostgreSQL TLS fixture path" >&2
                ;;
        esac
    fi
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker CLI not found; install and start Docker Desktop" >&2
    exit 127
fi
if ! command -v "$IIP_TEST_PYTHON" >/dev/null 2>&1; then
    echo "Python interpreter not found: $IIP_TEST_PYTHON" >&2
    exit 127
fi
"$IIP_DOCKER_BIN" info >/dev/null

IIP_TEST_TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/iip-postgres-tls.XXXXXX")
"$IIP_TEST_PYTHON" "$IIP_REPOSITORY_ROOT/scripts/write_postgres_tls_fixture.py" \
    "$IIP_TEST_TEMP_DIR"

export IIP_REPOSITORY_ROOT
export IIP_TEST_POSTGRES_TLS_FIXTURE_DIR="$IIP_TEST_TEMP_DIR"
export IIP_TEST_POSTGRES_TLS_PORT
export IIP_TEST_POSTGRES_PLAINTEXT_PORT

if ! "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --detach --wait; then
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" logs --no-color --tail=80 \
        postgres-tls-test >&2 || true
    exit 1
fi

IIP_TEST_POSTGRES_TLS_URL="postgresql://postgres@localhost:$IIP_TEST_POSTGRES_TLS_PORT/iip_tls_test" \
IIP_TEST_POSTGRES_TLS_WRONG_HOST_URL="postgresql://postgres@127.0.0.1:$IIP_TEST_POSTGRES_TLS_PORT/iip_tls_test" \
IIP_TEST_POSTGRES_PLAINTEXT_URL="postgresql://postgres@localhost:$IIP_TEST_POSTGRES_PLAINTEXT_PORT/iip_plaintext_test" \
IIP_TEST_POSTGRES_TLS_CA_FILE="$IIP_TEST_TEMP_DIR/ca.crt" \
IIP_TEST_POSTGRES_TLS_WRONG_CA_FILE="$IIP_TEST_TEMP_DIR/untrusted/ca.crt" \
PYTHONPATH="$IIP_REPOSITORY_ROOT/src:$IIP_REPOSITORY_ROOT/sdks/python/src" \
    "$IIP_TEST_PYTHON" -m unittest \
    tests.test_postgres_transport_tls_integration -v

echo "PostgreSQL transport gate passed: trusted verify-full -> wrong CA/hostname/plaintext denied -> explicit insecure-local plaintext"
