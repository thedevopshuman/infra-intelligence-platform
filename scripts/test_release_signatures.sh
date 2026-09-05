#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_COSIGN_IMAGE=ghcr.io/sigstore/cosign/cosign@sha256:d91bc4e7e95e8d2f549c747a72dc174f90579e410a1695f57f686674f84ce849
IIP_SIGNATURE_TEMP_DIR=$(mktemp -d)

cleanup() {
    rm -rf "$IIP_SIGNATURE_TEMP_DIR"
}
trap cleanup EXIT INT TERM

for executable in "$IIP_DOCKER_BIN" openssl; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        echo "Required executable not found: $executable" >&2
        exit 127
    fi
done

COSIGN_PASSWORD=$(openssl rand -hex 24)
export COSIGN_PASSWORD
printf '%s\n' 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
    >"$IIP_SIGNATURE_TEMP_DIR/artifact"

run_cosign() {
    "$IIP_DOCKER_BIN" run --rm --network none --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --pids-limit 64 \
        --memory 192m \
        --cpus 1 \
        --tmpfs /tmp:rw,noexec,nosuid,size=8m \
        --user "$(id -u):$(id -g)" \
        -e COSIGN_PASSWORD \
        -v "$IIP_SIGNATURE_TEMP_DIR:/work" \
        "$IIP_COSIGN_IMAGE" "$@"
}

run_cosign generate-key-pair --output-key-prefix /work/cosign >/dev/null
run_cosign signing-config create \
    --no-default-fulcio --no-default-oidc \
    --no-default-rekor --no-default-tsa \
    --out /work/local-signing-config.json
run_cosign sign-blob --yes \
    --key /work/cosign.key \
    --signing-config /work/local-signing-config.json \
    --bundle /work/artifact.sigstore.json \
    /work/artifact >/dev/null
run_cosign verify-blob --insecure-ignore-tlog=true \
    --key /work/cosign.pub \
    --bundle /work/artifact.sigstore.json \
    /work/artifact >/dev/null

printf '%s\n' tampered >"$IIP_SIGNATURE_TEMP_DIR/artifact"
if run_cosign verify-blob --insecure-ignore-tlog=true \
    --key /work/cosign.pub \
    --bundle /work/artifact.sigstore.json \
    /work/artifact >/dev/null 2>&1; then
    echo "Cosign accepted tampered release material" >&2
    exit 1
fi

echo "Pinned Cosign signed exact material and rejected tampering in the isolated local profile"
