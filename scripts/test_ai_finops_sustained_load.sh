#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
if [ "${IIP_AI_FINOPS_RUNTIME_REPORT+x}" = x ]; then
    IIP_AI_FINOPS_RUNTIME_REPORT_EXPLICIT=true
else
    IIP_AI_FINOPS_RUNTIME_REPORT_EXPLICIT=false
fi
IIP_AI_FINOPS_RUNTIME_REPORT=${IIP_AI_FINOPS_RUNTIME_REPORT:-dist/ai-finops-runtime-compatibility-report.json}
IIP_AI_FINOPS_SUSTAINED_LOAD_PROFILE=${IIP_AI_FINOPS_SUSTAINED_LOAD_PROFILE:-contracts/examples/ai-finops-sustained-load-profile.json}
IIP_AI_FINOPS_SUSTAINED_LOAD_REPORT=${IIP_AI_FINOPS_SUSTAINED_LOAD_REPORT:-dist/ai-finops-sustained-load-qualification-report.json}
IIP_COMPOSE_FILE=deploy/docker-compose.ai-finops.yml
IIP_EXPECTED_COLLECTOR_IMAGE=otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5

if ! command -v "$IIP_DOCKER_BIN" >/dev/null 2>&1; then
    echo "Docker is required for the AI FinOps sustained-load gate" >&2
    exit 2
fi

IIP_RUN_SUFFIX=$(
    "$IIP_TEST_PYTHON" -c 'import secrets; print(secrets.token_hex(8))'
)
IIP_COMPOSE_PROJECT="iip-ai-finops-test-${IIP_RUN_SUFFIX}"
IIP_AI_FINOPS_IMAGE="iip-ai-finops-test:${IIP_RUN_SUFFIX}"
IIP_AI_FINOPS_PENDING_REPORT="${IIP_AI_FINOPS_SUSTAINED_LOAD_REPORT}.pending-${IIP_RUN_SUFFIX}"
if [ "$IIP_AI_FINOPS_RUNTIME_REPORT_EXPLICIT" != true ]; then
    IIP_AI_FINOPS_RUNTIME_REPORT="${IIP_AI_FINOPS_RUNTIME_REPORT}.pending-${IIP_RUN_SUFFIX}"
fi
IIP_AI_FINOPS_ANCHOR=$(date -u '+%Y-%m-%dT%H:%M:00Z')

# A published port of zero asks Docker to allocate a loopback port. Resolve the
# actual bindings only after this invocation's isolated project is running.
IIP_AI_FINOPS_POSTGRES_PORT=0
IIP_AI_FINOPS_API_PORT=0
IIP_AI_FINOPS_RECEIVER_PORT=0
IIP_AI_FINOPS_COLLECTOR_PORT=0
IIP_AI_FINOPS_OPENAI_COLLECTOR_PORT=0
IIP_AI_FINOPS_COLLECTOR_HEALTH_PORT=0
IIP_AI_FINOPS_PROMETHEUS_PORT=0
IIP_AI_FINOPS_LOKI_PORT=0
IIP_AI_FINOPS_GRAFANA_PORT=0
export IIP_AI_FINOPS_IMAGE IIP_AI_FINOPS_ANCHOR
export IIP_AI_FINOPS_POSTGRES_PORT IIP_AI_FINOPS_API_PORT
export IIP_AI_FINOPS_RECEIVER_PORT IIP_AI_FINOPS_COLLECTOR_PORT
export IIP_AI_FINOPS_OPENAI_COLLECTOR_PORT
export IIP_AI_FINOPS_COLLECTOR_HEALTH_PORT IIP_AI_FINOPS_PROMETHEUS_PORT
export IIP_AI_FINOPS_LOKI_PORT IIP_AI_FINOPS_GRAFANA_PORT

if ! "$IIP_TEST_PYTHON" -c '
import os
import sys
from pathlib import Path

profile, final, pending, runtime = (
    Path(value).expanduser().absolute() for value in sys.argv[1:]
)
if final.is_symlink() or pending.exists() or pending.is_symlink() or runtime.is_symlink():
    raise SystemExit("ai-finops-sustained-load.paths.overlap")
resolved = [
    item.resolve(strict=False) for item in (profile, final, pending, runtime)
]
if len(set(resolved)) != len(resolved):
    raise SystemExit("ai-finops-sustained-load.paths.overlap")
paths = (profile, final, pending, runtime)
for index, left in enumerate(paths):
    for right in paths[index + 1:]:
        if left.exists() and right.exists() and os.path.samefile(left, right):
            raise SystemExit("ai-finops-sustained-load.paths.overlap")
' "$IIP_AI_FINOPS_SUSTAINED_LOAD_PROFILE" \
    "$IIP_AI_FINOPS_SUSTAINED_LOAD_REPORT" \
    "$IIP_AI_FINOPS_PENDING_REPORT" \
    "$IIP_AI_FINOPS_RUNTIME_REPORT"; then
    exit 2
fi

export IIP_AI_USAGE_CHANNEL_TOKEN
IIP_AI_USAGE_CHANNEL_TOKEN=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration token \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_OPENAI_USAGE_CHANNEL_TOKEN
IIP_OPENAI_USAGE_CHANNEL_TOKEN=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration openai-token \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_AI_USAGE_RECEIVER_CHANNELS_JSON
IIP_AI_USAGE_RECEIVER_CHANNELS_JSON=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration channel \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_AI_ATTRIBUTION_POLICIES_JSON
IIP_AI_ATTRIBUTION_POLICIES_JSON=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration attribution \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_AI_PRICE_CATALOGS_JSON
IIP_AI_PRICE_CATALOGS_JSON=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration catalog \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)
export IIP_AI_SAVINGS_PROFILES_JSON
IIP_AI_SAVINGS_PROFILES_JSON=$(
    "$IIP_TEST_PYTHON" scripts/ai_finops_fixture.py configuration profiles \
        --anchor "$IIP_AI_FINOPS_ANCHOR"
)

compose_command() {
    "$IIP_DOCKER_BIN" compose --project-name "$IIP_COMPOSE_PROJECT" \
        -f "$IIP_COMPOSE_FILE" "$@"
}

show_logs() {
    compose_command logs --no-color >&2 || true
}

cleanup_best_effort() {
    compose_command down --volumes >/dev/null 2>&1 || true
    "$IIP_DOCKER_BIN" image rm "$IIP_AI_FINOPS_IMAGE" >/dev/null 2>&1 || true
}

IIP_CLEANUP_COMPLETE=false
IIP_REPORT_PUBLISHED=false
on_exit() {
    IIP_EXIT_STATUS=$?
    trap - EXIT INT TERM
    if [ "$IIP_CLEANUP_COMPLETE" != true ]; then
        cleanup_best_effort
    fi
    if [ "$IIP_REPORT_PUBLISHED" != true ]; then
        rm -f -- "$IIP_AI_FINOPS_PENDING_REPORT" >/dev/null 2>&1 || true
    fi
    if [ "$IIP_AI_FINOPS_RUNTIME_REPORT_EXPLICIT" != true ]; then
        rm -f -- "$IIP_AI_FINOPS_RUNTIME_REPORT" >/dev/null 2>&1 || true
    fi
    exit "$IIP_EXIT_STATUS"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail_with_logs() {
    show_logs
    echo "$1" >&2
    exit 1
}

container_id() {
    IIP_CONTAINER_IDS=$(compose_command ps --all --quiet "$1") || return 1
    set -- $IIP_CONTAINER_IDS
    [ "$#" -eq 1 ] || return 1
    printf '%s\n' "$1"
}

container_image_id() {
    IIP_CONTAINER_IMAGE=$(
        "$IIP_DOCKER_BIN" inspect --type container --format '{{.Image}}' "$1"
    ) || return 1
    case "$IIP_CONTAINER_IMAGE" in
        sha256:*) ;;
        *) return 1 ;;
    esac
    IIP_CONTAINER_IMAGE_HASH=${IIP_CONTAINER_IMAGE#sha256:}
    case "$IIP_CONTAINER_IMAGE_HASH" in
        ''|*[!0-9a-f]*) return 1 ;;
    esac
    [ "${#IIP_CONTAINER_IMAGE_HASH}" -eq 64 ] || return 1
    printf '%s\n' "$IIP_CONTAINER_IMAGE"
}

container_image_reference() {
    "$IIP_DOCKER_BIN" inspect --type container --format '{{.Config.Image}}' "$1"
}

published_port() {
    IIP_PORT_BINDING=$(compose_command port "$1" "$2") || return 1
    IIP_PORT_VALUE=${IIP_PORT_BINDING##*:}
    case "$IIP_PORT_VALUE" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$IIP_PORT_VALUE" -ge 1 ] && [ "$IIP_PORT_VALUE" -le 65535 ] || return 1
    printf '%s\n' "$IIP_PORT_VALUE"
}

expected_stack_healthy() {
    IIP_STACK_FAILURE=false
    for IIP_SERVICE in \
        postgres api ai-usage-receiver workflow-worker otel-collector \
        prometheus loki grafana
    do
        if ! IIP_CONTAINER_ID=$(container_id "$IIP_SERVICE"); then
            echo "Expected container is missing: $IIP_SERVICE" >&2
            IIP_STACK_FAILURE=true
            continue
        fi
        if ! IIP_CONTAINER_STATE=$(
            "$IIP_DOCKER_BIN" inspect --type container \
                --format '{{.State.Running}}|{{.RestartCount}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
                "$IIP_CONTAINER_ID"
        ); then
            echo "Container state is unavailable: $IIP_SERVICE" >&2
            IIP_STACK_FAILURE=true
            continue
        fi
        case "$IIP_CONTAINER_STATE" in
            true\|0\|healthy|true\|0\|none) ;;
            *)
                echo "Container state is not qualified: $IIP_SERVICE ($IIP_CONTAINER_STATE)" >&2
                IIP_STACK_FAILURE=true
                ;;
        esac
    done
    [ "$IIP_STACK_FAILURE" = false ]
}

checked_cleanup() {
    if ! compose_command down --volumes; then
        return 1
    fi
    if ! "$IIP_DOCKER_BIN" image rm "$IIP_AI_FINOPS_IMAGE" >/dev/null; then
        return 1
    fi
    IIP_CLEANUP_COMPLETE=true
}

IIP_AI_FINOPS_SOURCE_REVISION=$(git rev-parse HEAD)
IIP_AI_FINOPS_SOURCE_DIRTY=false
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    IIP_AI_FINOPS_SOURCE_DIRTY=true
fi
IIP_AI_FINOPS_PLATFORM=$(
    "$IIP_DOCKER_BIN" info --format '{{.OSType}}/{{.Architecture}}'
)
IIP_AI_FINOPS_CONTAINER_RUNTIME_VERSION=$(
    "$IIP_DOCKER_BIN" version --format '{{.Server.Version}}'
)
IIP_AI_FINOPS_APPLICATION_VERSION=$(
    "$IIP_TEST_PYTHON" -c \
        'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
)

compose_command config --quiet
"$IIP_DOCKER_BIN" run --rm --network none --read-only \
    -e IIP_AI_USAGE_CHANNEL_TOKEN \
    -e IIP_OPENAI_USAGE_CHANNEL_TOKEN \
    -v "$PWD/deploy/otel/ai-finops-collector.yaml:/etc/otelcol-contrib/config.yaml:ro" \
    otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5 \
    validate --config=/etc/otelcol-contrib/config.yaml
"$IIP_DOCKER_BIN" run --rm --network none --read-only \
    --entrypoint /bin/promtool \
    -v "$PWD/deploy/prometheus/ai-finops-prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
    prom/prometheus:v3.13.1 \
    check config /etc/prometheus/prometheus.yml

compose_command up --build --detach --wait
if ! expected_stack_healthy; then
    fail_with_logs "AI FinOps containers did not start without restarts"
fi

if ! IIP_API_CONTAINER_ID=$(container_id api) \
    || ! IIP_WORKER_CONTAINER_ID=$(container_id workflow-worker) \
    || ! IIP_RECEIVER_CONTAINER_ID=$(container_id ai-usage-receiver) \
    || ! IIP_COLLECTOR_CONTAINER_ID=$(container_id otel-collector) \
    || ! IIP_API_IMAGE_DIGEST=$(container_image_id "$IIP_API_CONTAINER_ID") \
    || ! IIP_WORKER_IMAGE_DIGEST=$(container_image_id "$IIP_WORKER_CONTAINER_ID") \
    || ! IIP_RECEIVER_IMAGE_DIGEST=$(container_image_id "$IIP_RECEIVER_CONTAINER_ID") \
    || ! IIP_COLLECTOR_IMAGE_REFERENCE=$(container_image_reference "$IIP_COLLECTOR_CONTAINER_ID"); then
    fail_with_logs "AI FinOps runtime image identity could not be inspected"
fi
if [ "$IIP_API_IMAGE_DIGEST" != "$IIP_WORKER_IMAGE_DIGEST" ] \
    || [ "$IIP_API_IMAGE_DIGEST" != "$IIP_RECEIVER_IMAGE_DIGEST" ]; then
    fail_with_logs "API, worker, and receiver are not running the same application image"
fi
IIP_AI_FINOPS_IMAGE_DIGEST=$IIP_API_IMAGE_DIGEST
if [ "$IIP_COLLECTOR_IMAGE_REFERENCE" != "$IIP_EXPECTED_COLLECTOR_IMAGE" ]; then
    fail_with_logs "Collector is not running the exact pinned image reference"
fi
IIP_AI_FINOPS_COLLECTOR_IMAGE_DIGEST=${IIP_COLLECTOR_IMAGE_REFERENCE#*@}

if ! IIP_DISCOVERED_POSTGRES_PORT=$(published_port postgres 5432) \
    || ! IIP_DISCOVERED_API_PORT=$(published_port api 8080) \
    || ! IIP_DISCOVERED_RECEIVER_PORT=$(published_port ai-usage-receiver 4318) \
    || ! IIP_DISCOVERED_COLLECTOR_PORT=$(published_port otel-collector 4318) \
    || ! IIP_DISCOVERED_OPENAI_COLLECTOR_PORT=$(published_port otel-collector 4320) \
    || ! IIP_DISCOVERED_COLLECTOR_HEALTH_PORT=$(published_port otel-collector 13133) \
    || ! IIP_DISCOVERED_PROMETHEUS_PORT=$(published_port prometheus 9090) \
    || ! IIP_DISCOVERED_LOKI_PORT=$(published_port loki 3100) \
    || ! IIP_DISCOVERED_GRAFANA_PORT=$(published_port grafana 3000); then
    fail_with_logs "AI FinOps ephemeral port bindings could not be resolved"
fi
IIP_AI_FINOPS_POSTGRES_PORT=$IIP_DISCOVERED_POSTGRES_PORT
IIP_AI_FINOPS_API_PORT=$IIP_DISCOVERED_API_PORT
IIP_AI_FINOPS_RECEIVER_PORT=$IIP_DISCOVERED_RECEIVER_PORT
IIP_AI_FINOPS_COLLECTOR_PORT=$IIP_DISCOVERED_COLLECTOR_PORT
IIP_AI_FINOPS_OPENAI_COLLECTOR_PORT=$IIP_DISCOVERED_OPENAI_COLLECTOR_PORT
IIP_AI_FINOPS_COLLECTOR_HEALTH_PORT=$IIP_DISCOVERED_COLLECTOR_HEALTH_PORT
IIP_AI_FINOPS_PROMETHEUS_PORT=$IIP_DISCOVERED_PROMETHEUS_PORT
IIP_AI_FINOPS_LOKI_PORT=$IIP_DISCOVERED_LOKI_PORT
IIP_AI_FINOPS_GRAFANA_PORT=$IIP_DISCOVERED_GRAFANA_PORT
export IIP_AI_FINOPS_POSTGRES_PORT IIP_AI_FINOPS_API_PORT
export IIP_AI_FINOPS_RECEIVER_PORT IIP_AI_FINOPS_COLLECTOR_PORT
export IIP_AI_FINOPS_OPENAI_COLLECTOR_PORT
export IIP_AI_FINOPS_COLLECTOR_HEALTH_PORT IIP_AI_FINOPS_PROMETHEUS_PORT
export IIP_AI_FINOPS_LOKI_PORT IIP_AI_FINOPS_GRAFANA_PORT

IIP_AI_FINOPS_READY=false
IIP_AI_FINOPS_READY_ATTEMPT=0
while [ "$IIP_AI_FINOPS_READY_ATTEMPT" -lt 30 ]; do
    if "$IIP_TEST_PYTHON" -c \
        "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['IIP_AI_FINOPS_COLLECTOR_HEALTH_PORT'] + '/', timeout=2)" \
        >/dev/null 2>&1; then
        IIP_AI_FINOPS_READY=true
        break
    fi
    IIP_AI_FINOPS_READY_ATTEMPT=$((IIP_AI_FINOPS_READY_ATTEMPT + 1))
    sleep 1
done
if [ "$IIP_AI_FINOPS_READY" != true ]; then
    fail_with_logs "AI FinOps Collector did not become ready"
fi

if ! PYTHONPATH=src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/ai_finops_fixture.py send \
    --anchor "$IIP_AI_FINOPS_ANCHOR" \
    --collector "http://127.0.0.1:${IIP_AI_FINOPS_COLLECTOR_PORT}" \
    --openai-collector "http://127.0.0.1:${IIP_AI_FINOPS_OPENAI_COLLECTOR_PORT}" \
    --receiver "http://127.0.0.1:${IIP_AI_FINOPS_RECEIVER_PORT}"; then
    fail_with_logs "AI FinOps functional fixture delivery failed"
fi

if ! PYTHONPATH=src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/ai_finops_fixture.py verify \
    --anchor "$IIP_AI_FINOPS_ANCHOR" \
    --database "postgresql://iip@127.0.0.1:${IIP_AI_FINOPS_POSTGRES_PORT}/iip" \
    --api "http://127.0.0.1:${IIP_AI_FINOPS_API_PORT}" \
    --prometheus "http://127.0.0.1:${IIP_AI_FINOPS_PROMETHEUS_PORT}" \
    --grafana "http://127.0.0.1:${IIP_AI_FINOPS_GRAFANA_PORT}" \
    --loki "http://127.0.0.1:${IIP_AI_FINOPS_LOKI_PORT}" \
    --timeout-seconds 60 \
    --report "$IIP_AI_FINOPS_RUNTIME_REPORT" \
    --source-revision "$IIP_AI_FINOPS_SOURCE_REVISION" \
    --source-dirty "$IIP_AI_FINOPS_SOURCE_DIRTY" \
    --platform "$IIP_AI_FINOPS_PLATFORM" \
    --container-runtime-version "$IIP_AI_FINOPS_CONTAINER_RUNTIME_VERSION" \
    --application-version "$IIP_AI_FINOPS_APPLICATION_VERSION"; then
    fail_with_logs "AI FinOps functional compatibility verification failed"
fi

if ! PYTHONPATH=scripts:src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/qualify_ai_finops_sustained_load.py run \
    --profile "$IIP_AI_FINOPS_SUSTAINED_LOAD_PROFILE" \
    --database "postgresql://iip@127.0.0.1:${IIP_AI_FINOPS_POSTGRES_PORT}/iip" \
    --bedrock-collector "http://127.0.0.1:${IIP_AI_FINOPS_COLLECTOR_PORT}" \
    --openai-collector "http://127.0.0.1:${IIP_AI_FINOPS_OPENAI_COLLECTOR_PORT}" \
    --receiver "http://127.0.0.1:${IIP_AI_FINOPS_RECEIVER_PORT}" \
    --prometheus "http://127.0.0.1:${IIP_AI_FINOPS_PROMETHEUS_PORT}" \
    --grafana "http://127.0.0.1:${IIP_AI_FINOPS_GRAFANA_PORT}" \
    --report "$IIP_AI_FINOPS_PENDING_REPORT" \
    --source-revision "$IIP_AI_FINOPS_SOURCE_REVISION" \
    --source-dirty "$IIP_AI_FINOPS_SOURCE_DIRTY" \
    --platform "$IIP_AI_FINOPS_PLATFORM" \
    --container-runtime-version "$IIP_AI_FINOPS_CONTAINER_RUNTIME_VERSION" \
    --application-version "$IIP_AI_FINOPS_APPLICATION_VERSION" \
    --image-digest "$IIP_AI_FINOPS_IMAGE_DIGEST" \
    --collector-image-digest "$IIP_AI_FINOPS_COLLECTOR_IMAGE_DIGEST" \
    --compose-configuration-valid \
    --all-components-healthy \
    --allow-traffic; then
    fail_with_logs "AI FinOps sustained-load qualification failed"
fi

if ! expected_stack_healthy; then
    fail_with_logs "AI FinOps containers did not remain healthy without restarts"
fi
if ! checked_cleanup; then
    show_logs
    echo "AI FinOps isolated Docker teardown failed" >&2
    exit 1
fi
if ! mv -f -- "$IIP_AI_FINOPS_PENDING_REPORT" \
    "$IIP_AI_FINOPS_SUSTAINED_LOAD_REPORT"; then
    echo "AI FinOps qualification report could not be published" >&2
    exit 1
fi
IIP_REPORT_PUBLISHED=true

echo "Bounded Bedrock- and OpenAI-shaped AI FinOps sustained load passed"
