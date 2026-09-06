#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_HELM_BIN=${IIP_HELM_BIN:-helm}
IIP_PROMETHEUS_IMAGE=${IIP_PROMETHEUS_IMAGE:-prom/prometheus@sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893}
IIP_CHART=deploy/helm/infra-intelligence
IIP_ALERT_TEST_DIR=$(mktemp -d)

cleanup() {
    rm -f \
        "$IIP_ALERT_TEST_DIR/core-rendered.yaml" \
        "$IIP_ALERT_TEST_DIR/core-rules.yaml" \
        "$IIP_ALERT_TEST_DIR/ai-finops-rendered.yaml" \
        "$IIP_ALERT_TEST_DIR/ai-finops-rules.yaml"
    rmdir "$IIP_ALERT_TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the Prometheus rule compatibility test" >&2
    exit 2
fi
if ! command -v "$IIP_HELM_BIN" >/dev/null 2>&1; then
    echo "Helm is required for the Prometheus rule compatibility test" >&2
    exit 2
fi

check_profile() {
    profile=$1
    shift
    rendered="$IIP_ALERT_TEST_DIR/$profile-rendered.yaml"
    rules="$IIP_ALERT_TEST_DIR/$profile-rules.yaml"
    "$IIP_HELM_BIN" template iip "$IIP_CHART" \
        --namespace iip-system \
        "$@" \
        --show-only templates/operational-alerts.yaml >"$rendered"
    awk '/^  groups:/{found=1} found{sub(/^  /, ""); print}' \
        "$rendered" >"$rules"
    if ! grep -q '^groups:' "$rules"; then
        echo "Rendered $profile profile contained no Prometheus rule groups" >&2
        exit 1
    fi
    "$IIP_DOCKER_BIN" run --rm \
        --mount "type=bind,src=$IIP_ALERT_TEST_DIR,dst=/work,readonly" \
        --entrypoint=/bin/promtool \
        "$IIP_PROMETHEUS_IMAGE" check rules "/work/$profile-rules.yaml"
    echo "Prometheus accepted the rendered $profile operational alert profile"
}

check_profile core \
    --values "$IIP_CHART/examples/production-core.values.yaml"
check_profile ai-finops \
    --values "$IIP_CHART/examples/production-core.values.yaml" \
    --values "$IIP_CHART/examples/production-ai-finops.values.yaml"
