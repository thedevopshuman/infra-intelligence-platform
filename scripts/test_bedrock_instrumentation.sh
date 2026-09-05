#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_BEDROCK_COMPATIBILITY_MODE=${IIP_BEDROCK_COMPATIBILITY_MODE:-offline}
IIP_BEDROCK_OPERATION=${IIP_BEDROCK_OPERATION:-converse}
IIP_BEDROCK_COMPATIBILITY_IMAGE=iip-bedrock-compatibility:0.65b0

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
    : "${AWS_ACCESS_KEY_ID:?set short-lived AWS_ACCESS_KEY_ID}"
    : "${AWS_SECRET_ACCESS_KEY:?set short-lived AWS_SECRET_ACCESS_KEY}"
    : "${AWS_SESSION_TOKEN:?set short-lived AWS_SESSION_TOKEN}"
    if [ "$IIP_BEDROCK_LIVE_TEST_ENABLED" != true ]; then
        echo "IIP_BEDROCK_LIVE_TEST_ENABLED must equal true" >&2
        exit 2
    fi
fi

mkdir -p dist
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
    -v "$PWD/dist:/out"

if [ "$IIP_BEDROCK_COMPATIBILITY_MODE" = offline ]; then
    set -- "$@" --network none
else
    set -- "$@" \
        -e IIP_BEDROCK_LIVE_TEST_ENABLED \
        -e IIP_BEDROCK_MODEL_ID \
        -e AWS_REGION \
        -e AWS_ACCESS_KEY_ID \
        -e AWS_SECRET_ACCESS_KEY \
        -e AWS_SESSION_TOKEN
fi

if [ "$IIP_BEDROCK_COMPATIBILITY_MODE" = offline ]; then
    "$IIP_DOCKER_BIN" "$@" "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
        --mode offline --operation converse \
        --report /out/bedrock-instrumentation-offline-report.json
    "$IIP_DOCKER_BIN" "$@" "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
        --mode offline --operation converse-stream \
        --report /out/bedrock-converse-stream-instrumentation-offline-report.json
else
    if [ "$IIP_BEDROCK_OPERATION" = converse-stream ]; then
        IIP_BEDROCK_REPORT=/out/bedrock-converse-stream-instrumentation-live-report.json
    else
        IIP_BEDROCK_REPORT=/out/bedrock-instrumentation-live-report.json
    fi
    "$IIP_DOCKER_BIN" "$@" "$IIP_BEDROCK_COMPATIBILITY_IMAGE" \
        --mode live --operation "$IIP_BEDROCK_OPERATION" \
        --report "$IIP_BEDROCK_REPORT"
fi

echo "Pinned official botocore Bedrock Converse/ConverseStream instrumentation and IIP normalization passed"
