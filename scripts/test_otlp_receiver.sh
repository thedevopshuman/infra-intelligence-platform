#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_COMPOSE_FILE=deploy/docker-compose.otlp-receiver.yml
IIP_COMPOSE_PROJECT=iip-otlp-receiver-test
IIP_TEST_OTLP_CHANNEL_TOKEN=otlp-docker-channel-token-0123456789abcdef0123456789abcdef
IIP_TEST_OTLP_CONTROL_TOKEN=otlp-docker-control-token-0123456789abcdef0123456789abcdef

export IIP_AUTH_IDENTITIES_JSON='{"identities":[{"tokenSha256":"sha256:8bbd0227c409aa15b863aa1767570d36c38f61a18b66db31dbae32236b216a25","actorId":"docker-test","tenantId":"local","roles":["developer"]}]}'
export IIP_OTLP_RECEIVER_CHANNELS_JSON='{"channels":[{"channelId":"otlp-docker","tokenSha256":"sha256:621d36ae628d62a7ed61739c4ea433a635c099b11eceab14df9abd7dd5a6a41c","tenantId":"local","integrationId":"observability-docker","resourceRefs":["res_e0ae9225a316fce4c97df5c23057b97a"],"metrics":[{"otlpName":"http.server.request.count","metric":"service.request.count","unit":"{request}","attributes":{"deployment.environment.name":"deployment.environment.name","service.name":"service.name"}}],"limits":{"maxRequestBytes":1048576,"maxArtifactBytes":1048576,"maxSeries":10,"maxDataPoints":100,"maxAttributesPerPoint":8,"maxAgeSeconds":3600,"maxClockSkewSeconds":30,"maxProcessingSeconds":10},"handling":{"sensitivity":"internal","retentionClass":"ephemeral"}}]}'
export IIP_OTLP_LOGS_RECEIVER_CHANNELS_JSON='{"channels":[{"channelId":"otlp-logs-docker","tokenSha256":"sha256:621d36ae628d62a7ed61739c4ea433a635c099b11eceab14df9abd7dd5a6a41c","tenantId":"local","integrationId":"observability-docker","resourceRef":"res_e0ae9225a316fce4c97df5c23057b97a","services":[{"otlpName":"checkout","serviceName":"checkout","attributes":{"deployment.environment.name":"deployment.environment.name","k8s.namespace.name":"k8s.namespace.name"}}],"limits":{"maxRequestBytes":1048576,"maxArtifactBytes":1048576,"maxLogRecords":100,"maxAttributesPerRecord":8,"maxBodyBytes":4096,"maxAgeSeconds":3600,"maxClockSkewSeconds":30,"maxProcessingSeconds":10},"handling":{"sensitivity":"confidential","retentionClass":"ephemeral"}}]}'

cleanup() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" down --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the OTLP receiver integration test" >&2
    exit 2
fi

cleanup
"$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
    -f "$IIP_COMPOSE_FILE" up --build --detach --wait

IIP_TEST_OTLP_RECEIVER_ENDPOINT=http://127.0.0.1:18080 \
IIP_TEST_OTLP_CHANNEL_TOKEN="$IIP_TEST_OTLP_CHANNEL_TOKEN" \
IIP_TEST_OTLP_CONTROL_TOKEN="$IIP_TEST_OTLP_CONTROL_TOKEN" \
PYTHONPATH=src:sdks/python/src \
    "$IIP_TEST_PYTHON" -m unittest tests.test_otlp_receiver_integration -v

echo "Official OTLP/HTTP exporters delivered metrics and logs to the built API image"
