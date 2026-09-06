#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_BEDROCK_COMPATIBILITY_MODE=${IIP_BEDROCK_COMPATIBILITY_MODE:-offline}
IIP_BEDROCK_OPERATION=${IIP_BEDROCK_OPERATION:-converse}
IIP_BEDROCK_COMPATIBILITY_IMAGE=iip-bedrock-compatibility:0.65b0
IIP_BEDROCK_OUTPUT_DIR=${IIP_BEDROCK_OUTPUT_DIR:-$PWD/dist}

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the Bedrock instrumentation compatibility gate" >&2
    exit 2
fi
if [ "$IIP_BEDROCK_COMPATIBILITY_MODE" != offline ] \
    && [ "$IIP_BEDROCK_COMPATIBILITY_MODE" != live ]; then
    echo "IIP_BEDROCK_COMPATIBILITY_MODE must be offline or live" >&2
    exit 2
fi
if [ "$IIP_BEDROCK_OPERATION" != converse ] \
    && [ "$IIP_BEDROCK_OPERATION" != converse-stream ]; then
    echo "IIP_BEDROCK_OPERATION must be converse or converse-stream" >&2
    exit 2
fi
if [ "$IIP_BEDROCK_COMPATIBILITY_MODE" = live ]; then
    : "${IIP_BEDROCK_LIVE_TEST_ENABLED:?set IIP_BEDROCK_LIVE_TEST_ENABLED=true}"
    : "${IIP_BEDROCK_MODEL_ID:?set IIP_BEDROCK_MODEL_ID}"
    : "${AWS_REGION:?set AWS_REGION}"
    : "${IIP_BEDROCK_AWS_CREDENTIALS_FILE:?set IIP_BEDROCK_AWS_CREDENTIALS_FILE}"
    : "${IIP_BEDROCK_CREDENTIALS_PROFILE:?set IIP_BEDROCK_CREDENTIALS_PROFILE}"
    : "${IIP_BEDROCK_MAXIMUM_PROVIDER_CALL_MILLISECONDS:?set IIP_BEDROCK_MAXIMUM_PROVIDER_CALL_MILLISECONDS}"
    if [ "$IIP_BEDROCK_LIVE_TEST_ENABLED" != true ]; then
        echo "IIP_BEDROCK_LIVE_TEST_ENABLED must equal true" >&2
        exit 2
    fi
    if [ -n "${AWS_ACCESS_KEY_ID:-}${AWS_SECRET_ACCESS_KEY:-}${AWS_SESSION_TOKEN:-}" ]; then
        echo "ambient AWS credential environment variables are prohibited; use the protected dedicated credentials file" >&2
        exit 2
    fi
    if [ ! -f "$IIP_BEDROCK_AWS_CREDENTIALS_FILE" ] \
        || [ -L "$IIP_BEDROCK_AWS_CREDENTIALS_FILE" ]; then
        echo "IIP_BEDROCK_AWS_CREDENTIALS_FILE must be a regular non-symlink file" >&2
        exit 2
    fi
    if [ -n "${IIP_BEDROCK_OTLP_TRACES_ENDPOINT:-}" ]; then
        : "${IIP_BEDROCK_CORRELATION_BASENAME:?set IIP_BEDROCK_CORRELATION_BASENAME}"
        case "$IIP_BEDROCK_CORRELATION_BASENAME" in
            *[!A-Za-z0-9._-]*)
                echo "IIP_BEDROCK_CORRELATION_BASENAME contains unsupported characters" >&2
                exit 2
                ;;
        esac
        for IIP_BEDROCK_SECRET_FILE in \
            "${IIP_BEDROCK_OTLP_HEADERS_FILE:-}" \
            "${IIP_BEDROCK_OTLP_CA_FILE:-}" \
            "${IIP_BEDROCK_OTLP_CLIENT_CERT_FILE:-}" \
            "${IIP_BEDROCK_OTLP_CLIENT_KEY_FILE:-}"; do
            if [ -n "$IIP_BEDROCK_SECRET_FILE" ] \
                && { [ ! -f "$IIP_BEDROCK_SECRET_FILE" ] \
                    || [ -L "$IIP_BEDROCK_SECRET_FILE" ]; }; then
                echo "Bedrock OTLP trust and credential inputs must be regular non-symlink files" >&2
                exit 2
            fi
        done
        if { [ -n "${IIP_BEDROCK_OTLP_CLIENT_CERT_FILE:-}" ] \
                && [ -z "${IIP_BEDROCK_OTLP_CLIENT_KEY_FILE:-}" ]; } \
            || { [ -z "${IIP_BEDROCK_OTLP_CLIENT_CERT_FILE:-}" ] \
                && [ -n "${IIP_BEDROCK_OTLP_CLIENT_KEY_FILE:-}" ]; }; then
            echo "Bedrock OTLP client certificate and key must be configured together" >&2
            exit 2
        fi
    fi
fi

case "${IIP_BEDROCK_REPORT_BASENAME:-}" in
    *[!A-Za-z0-9._-]*)
        echo "IIP_BEDROCK_REPORT_BASENAME contains unsupported characters" >&2
        exit 2
        ;;
esac
mkdir -p "$IIP_BEDROCK_OUTPUT_DIR"
IIP_BEDROCK_OUTPUT_DIR=$(cd "$IIP_BEDROCK_OUTPUT_DIR" && pwd)
IIP_SOURCE_REVISION=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    IIP_SOURCE_DIRTY=true
else
    IIP_SOURCE_DIRTY=false
fi
IIP_CONTAINER_RUNTIME_VERSION=$(
    "$IIP_DOCKER_BIN" version --format '{{.Server.Version}}'
)
export IIP_SOURCE_REVISION IIP_SOURCE_DIRTY IIP_CONTAINER_RUNTIME_VERSION

"$IIP_DOCKER_BIN" build \
    --file deploy/bedrock-compatibility/Dockerfile \
    --tag "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
    .

set -- run --rm --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 64 \
    --memory 384m \
    --cpus 1 \
    --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --user "$(id -u):$(id -g)" \
    -e IIP_SOURCE_REVISION \
    -e IIP_SOURCE_DIRTY \
    -e IIP_CONTAINER_RUNTIME_VERSION \
    -v "$IIP_BEDROCK_OUTPUT_DIR:/out"

if [ "$IIP_BEDROCK_COMPATIBILITY_MODE" = offline ]; then
    set -- "$@" --network none
else
    set -- "$@" \
        --add-host host.docker.internal:host-gateway \
        -e IIP_BEDROCK_LIVE_TEST_ENABLED \
        -e IIP_BEDROCK_MODEL_ID \
        -e IIP_BEDROCK_MAXIMUM_PROVIDER_CALL_MILLISECONDS \
        -e AWS_REGION \
        -e AWS_PROFILE="$IIP_BEDROCK_CREDENTIALS_PROFILE" \
        -e AWS_SHARED_CREDENTIALS_FILE=/run/secrets/aws/credentials \
        -e AWS_CONFIG_FILE=/run/secrets/aws/no-config \
        -e AWS_EC2_METADATA_DISABLED=true \
        -e AWS_IGNORE_CONFIGURED_ENDPOINT_URLS=true \
        -e HTTP_PROXY= \
        -e HTTPS_PROXY= \
        -e ALL_PROXY= \
        -e NO_PROXY= \
        -v "$IIP_BEDROCK_AWS_CREDENTIALS_FILE:/run/secrets/aws/credentials:ro"
    if [ -n "${IIP_BEDROCK_OTLP_TRACES_ENDPOINT:-}" ]; then
        set -- "$@" -e IIP_BEDROCK_OTLP_TRACES_ENDPOINT
        if [ "${IIP_BEDROCK_OTLP_ALLOW_INSECURE:-false}" = true ]; then
            set -- "$@" -e IIP_BEDROCK_OTLP_ALLOW_INSECURE=true
        fi
        if [ -n "${IIP_BEDROCK_OTLP_HEADERS_FILE:-}" ]; then
            set -- "$@" \
                -e IIP_BEDROCK_OTLP_HEADERS_FILE=/run/secrets/otel/headers.json \
                -v "$IIP_BEDROCK_OTLP_HEADERS_FILE:/run/secrets/otel/headers.json:ro"
        fi
        if [ -n "${IIP_BEDROCK_OTLP_CA_FILE:-}" ]; then
            set -- "$@" \
                -e IIP_BEDROCK_OTLP_CA_FILE=/run/secrets/otel/ca.crt \
                -v "$IIP_BEDROCK_OTLP_CA_FILE:/run/secrets/otel/ca.crt:ro"
        fi
        if [ -n "${IIP_BEDROCK_OTLP_CLIENT_CERT_FILE:-}" ]; then
            set -- "$@" \
                -e IIP_BEDROCK_OTLP_CLIENT_CERT_FILE=/run/secrets/otel/tls.crt \
                -e IIP_BEDROCK_OTLP_CLIENT_KEY_FILE=/run/secrets/otel/tls.key \
                -v "$IIP_BEDROCK_OTLP_CLIENT_CERT_FILE:/run/secrets/otel/tls.crt:ro" \
                -v "$IIP_BEDROCK_OTLP_CLIENT_KEY_FILE:/run/secrets/otel/tls.key:ro"
        fi
    fi
fi

if [ "$IIP_BEDROCK_COMPATIBILITY_MODE" = offline ]; then
    "$IIP_DOCKER_BIN" "$@" "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
        --mode offline --operation converse \
        --report /out/bedrock-instrumentation-offline-report.json
    "$IIP_DOCKER_BIN" "$@" "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
        --mode offline --operation converse-stream \
        --report /out/bedrock-converse-stream-instrumentation-offline-report.json
    "$IIP_DOCKER_BIN" "$@" --entrypoint python \
        "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
        scripts/run_bedrock_delivery_self_test.py
else
    if [ -n "${IIP_BEDROCK_REPORT_BASENAME:-}" ]; then
        IIP_BEDROCK_REPORT="/out/$IIP_BEDROCK_REPORT_BASENAME"
    elif [ "$IIP_BEDROCK_OPERATION" = converse-stream ]; then
        IIP_BEDROCK_REPORT=/out/bedrock-converse-stream-instrumentation-live-report.json
    else
        IIP_BEDROCK_REPORT=/out/bedrock-instrumentation-live-report.json
    fi
    if [ -n "${IIP_BEDROCK_OTLP_TRACES_ENDPOINT:-}" ]; then
        "$IIP_DOCKER_BIN" "$@" "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
            --mode live --operation "$IIP_BEDROCK_OPERATION" \
            --report "$IIP_BEDROCK_REPORT" \
            --correlation-output "/out/$IIP_BEDROCK_CORRELATION_BASENAME"
    else
        "$IIP_DOCKER_BIN" "$@" "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
            --mode live --operation "$IIP_BEDROCK_OPERATION" \
            --report "$IIP_BEDROCK_REPORT"
    fi
fi

echo "Pinned official botocore Bedrock Converse/ConverseStream instrumentation and IIP normalization passed"
