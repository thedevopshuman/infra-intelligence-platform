#!/bin/sh
set -eu

IIP_COSIGN_DOCKER_BIN=${IIP_COSIGN_DOCKER_BIN:-docker}
IIP_COSIGN_IMAGE=ghcr.io/sigstore/cosign/cosign@sha256:d91bc4e7e95e8d2f549c747a72dc174f90579e410a1695f57f686674f84ce849
IIP_COSIGN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
IIP_COSIGN_CONFIG=${DOCKER_CONFIG:-${HOME:?HOME is required}/.docker}

if ! command -v "$IIP_COSIGN_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Required executable not found: $IIP_COSIGN_DOCKER_BIN" >&2
    exit 127
fi

mkdir -p "$IIP_COSIGN_ROOT/dist"

run_cosign() {
    "$IIP_COSIGN_DOCKER_BIN" run --rm --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --pids-limit 128 \
        --memory 256m \
        --memory-swap 256m \
        --cpus 1 \
        --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
        -e TUF_ROOT=/tmp/sigstore \
        --network bridge \
        --user "$(id -u):$(id -g)" \
        --workdir /workspace \
        --volume "$IIP_COSIGN_ROOT/dist:/workspace/dist:rw" \
        -e ACTIONS_ID_TOKEN_REQUEST_TOKEN \
        -e ACTIONS_ID_TOKEN_REQUEST_URL \
        -e GITHUB_ACTIONS \
        "$@"
}

if [ -d "$IIP_COSIGN_CONFIG" ]; then
    run_cosign \
        --volume "$IIP_COSIGN_CONFIG:/docker-config:ro" \
        -e DOCKER_CONFIG=/docker-config \
        "$IIP_COSIGN_IMAGE" "$@"
else
    run_cosign "$IIP_COSIGN_IMAGE" "$@"
fi
