#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_COMPOSE_FILE=deploy/docker-compose.otlp-receiver.yml
IIP_COMPOSE_PROJECT=iip-otlp-receiver-test
IIP_TEST_OTLP_CHANNEL_TOKEN=otlp-docker-channel-token-0123456789abcdef0123456789abcdef
IIP_TEST_OTLP_CONTROL_TOKEN=otlp-docker-control-token-0123456789abcdef0123456789abcdef
IIP_OTLP_MTLS_FIXTURE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/iip-otlp-mtls.XXXXXX")

export IIP_AUTH_IDENTITIES_JSON='{"identities":[{"tokenSha256":"sha256:8bbd0227c409aa15b863aa1767570d36c38f61a18b66db31dbae32236b216a25","actorId":"docker-test","tenantId":"local","roles":["developer"]}]}'
export IIP_OTLP_RECEIVER_CHANNELS_JSON='{"channels":[{"channelId":"otlp-docker","tokenSha256":"sha256:621d36ae628d62a7ed61739c4ea433a635c099b11eceab14df9abd7dd5a6a41c","tenantId":"local","integrationId":"observability-docker","resourceRefs":["res_e0ae9225a316fce4c97df5c23057b97a"],"metrics":[{"otlpName":"http.server.request.count","metric":"service.request.count","unit":"{request}","attributes":{"deployment.environment.name":"deployment.environment.name","service.name":"service.name"}}],"limits":{"maxRequestBytes":1048576,"maxArtifactBytes":1048576,"maxSeries":10,"maxDataPoints":100,"maxAttributesPerPoint":8,"maxAgeSeconds":3600,"maxClockSkewSeconds":30,"maxProcessingSeconds":10},"handling":{"sensitivity":"internal","retentionClass":"ephemeral"}}]}'
export IIP_OTLP_LOGS_RECEIVER_CHANNELS_JSON='{"channels":[{"channelId":"otlp-logs-docker","tokenSha256":"sha256:621d36ae628d62a7ed61739c4ea433a635c099b11eceab14df9abd7dd5a6a41c","tenantId":"local","integrationId":"observability-docker","resourceRef":"res_e0ae9225a316fce4c97df5c23057b97a","services":[{"otlpName":"checkout","serviceName":"checkout","attributes":{"deployment.environment.name":"deployment.environment.name","k8s.namespace.name":"k8s.namespace.name"}}],"limits":{"maxRequestBytes":1048576,"maxArtifactBytes":1048576,"maxLogRecords":100,"maxAttributesPerRecord":8,"maxBodyBytes":4096,"maxAgeSeconds":3600,"maxClockSkewSeconds":30,"maxProcessingSeconds":10},"handling":{"sensitivity":"confidential","retentionClass":"ephemeral"}}]}'
export IIP_OTLP_MTLS_IDENTITIES_JSON='{"identities":[{"spiffeId":"spiffe://customer.example/observability/collector","channelIds":["otlp-docker","otlp-logs-docker"]}]}'
export IIP_OTLP_MTLS_FIXTURE_DIR

cleanup() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" down --volumes >/dev/null 2>&1 || true
    if [ -d "$IIP_OTLP_MTLS_FIXTURE_DIR" ]; then
        rm -r "$IIP_OTLP_MTLS_FIXTURE_DIR"
    fi
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the OTLP receiver integration test" >&2
    exit 2
fi

cleanup
"$IIP_TEST_PYTHON" scripts/write_otlp_mtls_fixture.py "$IIP_OTLP_MTLS_FIXTURE_DIR"
PYTHONPATH=src:sdks/python/src "$IIP_TEST_PYTHON" -m unittest \
    tests.test_otlp_tls.OtlpTlsConfigurationTests.test_intermediate_client_chain_builds_to_the_root_trust_anchor \
    -v
"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --build --detach --wait

IIP_TEST_CONTROL_ENDPOINT=http://127.0.0.1:18080 \
IIP_TEST_OTLP_RECEIVER_ENDPOINT=https://127.0.0.1:18081 \
IIP_TEST_OTLP_CHANNEL_TOKEN="$IIP_TEST_OTLP_CHANNEL_TOKEN" \
IIP_TEST_OTLP_CONTROL_TOKEN="$IIP_TEST_OTLP_CONTROL_TOKEN" \
IIP_TEST_OTLP_CA_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/ca.crt" \
IIP_TEST_OTLP_CLIENT_CA_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/client-ca.crt" \
IIP_TEST_OTLP_SERVER_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/server.crt" \
IIP_TEST_OTLP_SERVER_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/server.key" \
IIP_TEST_OTLP_EXPIRED_CRL_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/expired-ca.crl" \
IIP_TEST_OTLP_CLIENT_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-a.crt" \
IIP_TEST_OTLP_CLIENT_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-a.key" \
IIP_TEST_OTLP_ROTATED_CLIENT_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-b.crt" \
IIP_TEST_OTLP_ROTATED_CLIENT_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-b.key" \
IIP_TEST_OTLP_OTHER_CLIENT_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/other-workload.crt" \
IIP_TEST_OTLP_OTHER_CLIENT_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/other-workload.key" \
IIP_TEST_OTLP_UNTRUSTED_CLIENT_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/untrusted/collector.crt" \
IIP_TEST_OTLP_UNTRUSTED_CLIENT_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/untrusted/collector.key" \
IIP_TEST_OTLP_EXPIRED_CLIENT_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-expired.crt" \
IIP_TEST_OTLP_EXPIRED_CLIENT_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-expired.key" \
IIP_TEST_OTLP_REVOKED_CLIENT_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-revoked.crt" \
IIP_TEST_OTLP_REVOKED_CLIENT_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-revoked.key" \
IIP_TEST_OTLP_DATABASE_URL=postgresql://iip@127.0.0.1:15434/iip \
PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest tests.test_otlp_receiver_integration -v

"$IIP_TEST_PYTHON" scripts/write_otlp_mtls_fixture.py \
    --activate-rotated-crl "$IIP_OTLP_MTLS_FIXTURE_DIR"
"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --detach --wait --no-deps --force-recreate receiver

IIP_TEST_OTLP_RECEIVER_ENDPOINT=https://127.0.0.1:18081 \
IIP_TEST_OTLP_ROTATED_CRL_ACTIVE=true \
IIP_TEST_OTLP_CHANNEL_TOKEN="$IIP_TEST_OTLP_CHANNEL_TOKEN" \
IIP_TEST_OTLP_CA_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/ca.crt" \
IIP_TEST_OTLP_CLIENT_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-a.crt" \
IIP_TEST_OTLP_CLIENT_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-a.key" \
IIP_TEST_OTLP_ROTATED_CLIENT_CERT_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-b.crt" \
IIP_TEST_OTLP_ROTATED_CLIENT_KEY_FILE="$IIP_OTLP_MTLS_FIXTURE_DIR/collector-b.key" \
PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest \
    tests.test_otlp_receiver_integration.OtlpReceiverCrlRotationDockerIntegrationTests \
    -v

IIP_RECEIVER_METRIC_FOUND=false
IIP_RECEIVER_METRIC_ATTEMPT=0
while [ "$IIP_RECEIVER_METRIC_ATTEMPT" -lt 20 ]; do
    if "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" logs otel-collector 2>&1 \
        | grep -q 'iip.otlp.receiver.requests'; then
        IIP_RECEIVER_METRIC_FOUND=true
        break
    fi
    IIP_RECEIVER_METRIC_ATTEMPT=$((IIP_RECEIVER_METRIC_ATTEMPT + 1))
    sleep 1
done
if [ "$IIP_RECEIVER_METRIC_FOUND" != true ]; then
    echo "Receiver availability metric did not reach the separate Collector" >&2
    exit 1
fi

"$IIP_DOCKER_BIN" run --rm --network none --read-only \
    -e IIP_OTLP_RECEIVER_ENDPOINT=https://receiver.example.test:4318 \
    -e IIP_OTLP_CHANNEL_TOKEN=validation-only-not-a-secret \
    -v "$PWD/deploy/otel/collector-to-iip.example.yaml:/etc/otelcol-contrib/config.yaml:ro" \
    otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5 \
    validate --config=/etc/otelcol-contrib/config.yaml

IIP_DOCKER_BIN="$IIP_DOCKER_BIN" PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" scripts/write_otlp_receiver_compatibility_report.py \
    --report dist/otlp-receiver-compatibility-report.json

echo "Official OTLP/HTTP exporters, an intermediate client CA, and CRL rollout passed across isolated boundaries"
