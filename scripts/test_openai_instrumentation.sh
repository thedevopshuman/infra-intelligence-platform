#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_OPENAI_COMPATIBILITY_MODE=${IIP_OPENAI_COMPATIBILITY_MODE:-offline}
IIP_OPENAI_COMPATIBILITY_IMAGE=iip-openai-compatibility:1.1b0

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the OpenAI instrumentation compatibility gate" >&2
    exit 2
fi
if [ "$IIP_OPENAI_COMPATIBILITY_MODE" != offline ] \
    && [ "$IIP_OPENAI_COMPATIBILITY_MODE" != live ]; then
    echo "IIP_OPENAI_COMPATIBILITY_MODE must be offline or live" >&2
    exit 2
fi
if [ "$IIP_OPENAI_COMPATIBILITY_MODE" = live ]; then
    : "${IIP_OPENAI_LIVE_TEST_ENABLED:?set IIP_OPENAI_LIVE_TEST_ENABLED=true}"
    : "${IIP_OPENAI_MODEL:?set IIP_OPENAI_MODEL}"
    : "${OPENAI_API_KEY:?set a short-lived OPENAI_API_KEY}"
    if [ "$IIP_OPENAI_LIVE_TEST_ENABLED" != true ]; then
        echo "IIP_OPENAI_LIVE_TEST_ENABLED must equal true" >&2
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
    --file deploy/openai-compatibility/Dockerfile \
    --tag "$IIP_OPENAI_COMPATIBILITY_IMAGE" \
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

if [ "$IIP_OPENAI_COMPATIBILITY_MODE" = offline ]; then
    set -- "$@" --network none
else
    set -- "$@" \
        -e IIP_OPENAI_LIVE_TEST_ENABLED \
        -e IIP_OPENAI_MODEL \
        -e OPENAI_API_KEY
fi

"$IIP_DOCKER_BIN" "$@" "$IIP_OPENAI_COMPATIBILITY_IMAGE" \
    --mode "$IIP_OPENAI_COMPATIBILITY_MODE" \
    --report "/out/openai-instrumentation-${IIP_OPENAI_COMPATIBILITY_MODE}-report.json"

echo "Pinned official OpenAI instrumentation and IIP normalization passed"
