#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_HELM_BIN=${IIP_HELM_BIN:-helm}
IIP_KIND_BIN=${IIP_KIND_BIN:-kind}
IIP_KUBECTL_BIN=${IIP_KUBECTL_BIN:-kubectl}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_KUBE_CONTEXT=${IIP_KUBE_CONTEXT:-kind-iip-dev}
IIP_TEST_NAMESPACE=${IIP_TEST_NAMESPACE:-iip-helm-install-test}
IIP_KEEP_TEST_NAMESPACE=${IIP_KEEP_TEST_NAMESPACE:-false}
IIP_RELEASE_BUNDLE=${IIP_RELEASE_BUNDLE:-}
IIP_RELEASE_QUALIFICATION_REPORT=${IIP_RELEASE_QUALIFICATION_REPORT:-}
IIP_TEST_TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/iip-helm-install.XXXXXX")

case "$IIP_KUBE_CONTEXT" in
    kind-*) ;;
    *)
        echo "Refusing Helm install test outside an explicit kind context" >&2
        exit 2
        ;;
esac

for command in "$IIP_DOCKER_BIN" "$IIP_HELM_BIN" "$IIP_KIND_BIN" "$IIP_KUBECTL_BIN" openssl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required Helm install test command is unavailable: $command" >&2
        exit 2
    fi
done

if ! "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get namespace >/dev/null 2>&1; then
    echo "The explicit local Kubernetes context is unavailable: $IIP_KUBE_CONTEXT" >&2
    exit 2
fi

cleanup_namespace() {
    if [ "$IIP_KEEP_TEST_NAMESPACE" != "true" ]; then
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" delete namespace \
            "$IIP_TEST_NAMESPACE" --ignore-not-found --wait=false >/dev/null 2>&1 || true
    fi
}

cleanup() {
    cleanup_namespace
    rm -f "$IIP_TEST_TEMP_DIR/tls.crt" "$IIP_TEST_TEMP_DIR/tls.key"
    rmdir "$IIP_TEST_TEMP_DIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

cleanup_namespace
attempt=0
while "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get namespace \
    "$IIP_TEST_NAMESPACE" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Prior Helm install test namespace did not terminate" >&2
        exit 1
    fi
    sleep 1
done

IIP_KIND_CLUSTER=${IIP_KUBE_CONTEXT#kind-}
IIP_DB_PASSWORD=$(openssl rand -hex 24)
IIP_AUTH_BEARER_TOKEN=$(openssl rand -hex 32)
IIP_AUTH_VERIFIER="sha256:$(printf '%s' "$IIP_AUTH_BEARER_TOKEN" | openssl dgst -sha256 -hex | awk '{print $NF}')"
IIP_AUTH_IDENTITIES_JSON=$(printf '%s' \
    "{\"identities\":[{\"tokenSha256\":\"$IIP_AUTH_VERIFIER\",\"actorId\":\"helm-test-operator\",\"tenantId\":\"helm-test\",\"roles\":[\"developer\",\"platform-admin\"]}]}"
)

IIP_EXPECTED_BUILD_MODE=development
IIP_EXPECTED_BUILD_REVISION=
if [ -n "$IIP_RELEASE_BUNDLE" ]; then
    case "$IIP_RELEASE_BUNDLE" in
        /*) ;;
        *)
            echo "IIP_RELEASE_BUNDLE must be an absolute directory" >&2
            exit 2
            ;;
    esac
    if [ -z "$IIP_RELEASE_QUALIFICATION_REPORT" ]; then
        IIP_RELEASE_QUALIFICATION_REPORT="$IIP_RELEASE_BUNDLE.qualification.json"
    fi
    case "$IIP_RELEASE_QUALIFICATION_REPORT" in
        /*) ;;
        *)
            echo "IIP_RELEASE_QUALIFICATION_REPORT must be an absolute path" >&2
            exit 2
            ;;
    esac
    "$IIP_TEST_PYTHON" scripts/release_bundle.py verify "$IIP_RELEASE_BUNDLE"
    IIP_RELEASE_MANIFEST="$IIP_RELEASE_BUNDLE/release-manifest.json"
    IIP_APP_VERSION=$(
        "$IIP_TEST_PYTHON" -c \
            'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["version"])' \
            "$IIP_RELEASE_MANIFEST"
    )
    IIP_CHART_VERSION=$(
        "$IIP_TEST_PYTHON" -c \
            'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["chartVersion"])' \
            "$IIP_RELEASE_MANIFEST"
    )
    IIP_EXPECTED_BUILD_REVISION=$(
        "$IIP_TEST_PYTHON" -c \
            'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["revision"])' \
            "$IIP_RELEASE_MANIFEST"
    )
    IIP_TEST_IMAGE_DIGEST=$(
        "$IIP_TEST_PYTHON" -c \
            'import json,sys; print(json.load(open(sys.argv[1]))["spec"]["image"]["indexDigest"])' \
            "$IIP_RELEASE_MANIFEST"
    )
    if [ "$IIP_EXPECTED_BUILD_REVISION" != "$(git rev-parse HEAD)" ]; then
        echo "Release bundle revision does not match the checked-out install gate" >&2
        exit 1
    fi
    IIP_EXPECTED_BUILD_MODE=release
    IIP_TEST_CHART="$IIP_RELEASE_BUNDLE/infra-intelligence-$IIP_CHART_VERSION.tgz"
    IIP_TEST_IMAGE_REPOSITORY=iip-release-control-plane
    IIP_TEST_IMAGE="$IIP_TEST_IMAGE_REPOSITORY:$IIP_APP_VERSION-$(printf '%s' "$IIP_EXPECTED_BUILD_REVISION" | cut -c1-12)"
    "$IIP_DOCKER_BIN" load --input \
        "$IIP_RELEASE_BUNDLE/infra-intelligence-control-plane-$IIP_APP_VERSION.oci.tar" \
        >/dev/null
    "$IIP_DOCKER_BIN" tag "$IIP_TEST_IMAGE_DIGEST" "$IIP_TEST_IMAGE"
else
    IIP_APP_VERSION=$(
        "$IIP_TEST_PYTHON" -c \
            'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
    )
    IIP_CHART_VERSION=$(awk '$1 == "version:" {print $2; exit}' deploy/helm/infra-intelligence/Chart.yaml)
    IIP_TEST_CHART=deploy/helm/infra-intelligence
    IIP_TEST_IMAGE_REPOSITORY=iip-local-platform
    IIP_TEST_IMAGE="$IIP_TEST_IMAGE_REPOSITORY:$IIP_APP_VERSION"
    "$IIP_DOCKER_BIN" build --provenance=false \
        --build-arg "IIP_IMAGE_VERSION=$IIP_APP_VERSION" \
        --build-arg IIP_IMAGE_REVISION=development \
        --tag "$IIP_TEST_IMAGE" . >/dev/null
    IIP_TEST_IMAGE_REFERENCE=$(
        "$IIP_DOCKER_BIN" image inspect "$IIP_TEST_IMAGE" \
            --format '{{index .RepoDigests 0}}'
    )
    IIP_TEST_IMAGE_DIGEST=${IIP_TEST_IMAGE_REFERENCE#*@}
fi
if ! printf '%s\n' "$IIP_TEST_IMAGE_DIGEST" | rg -q '^sha256:[a-f0-9]{64}$'; then
    echo "Helm install test image did not produce an immutable digest" >&2
    exit 1
fi
"$IIP_KIND_BIN" load docker-image "$IIP_TEST_IMAGE" \
    --name "$IIP_KIND_CLUSTER" >/dev/null
for IIP_KIND_NODE in $("$IIP_KIND_BIN" get nodes --name "$IIP_KIND_CLUSTER"); do
    "$IIP_DOCKER_BIN" exec "$IIP_KIND_NODE" ctr -n k8s.io images tag --force \
        "docker.io/library/$IIP_TEST_IMAGE" \
        "docker.io/library/$IIP_TEST_IMAGE_REPOSITORY@$IIP_TEST_IMAGE_DIGEST" \
        >/dev/null
done

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" create namespace \
    "$IIP_TEST_NAMESPACE" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create secret generic iip-database \
    --from-literal="database-url=postgresql://iip:$IIP_DB_PASSWORD@iip-postgres:5432/iip" \
    --from-literal="password=$IIP_DB_PASSWORD" \
    >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create secret generic iip-auth \
    --from-literal="identities-json=$IIP_AUTH_IDENTITIES_JSON" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create secret generic iip-investigation-signal-catalog \
    --from-file="investigation-signal-catalog-json=contracts/examples/investigation-signal-catalog.json" \
    >/dev/null
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
    -subj "/CN=iip.helm.test" \
    -addext "subjectAltName=DNS:iip.helm.test" \
    -keyout "$IIP_TEST_TEMP_DIR/tls.key" \
    -out "$IIP_TEST_TEMP_DIR/tls.crt" >/dev/null 2>&1
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create secret tls iip-tls \
    --cert="$IIP_TEST_TEMP_DIR/tls.crt" \
    --key="$IIP_TEST_TEMP_DIR/tls.key" >/dev/null

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    apply -f - >/dev/null <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: iip-postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: postgresql
  template:
    metadata:
      labels:
        app.kubernetes.io/name: postgresql
    spec:
      containers:
        - name: postgres
          image: postgres:18.4-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15
          env:
            - name: POSTGRES_USER
              value: iip
            - name: POSTGRES_PASSWORD
              value: "$IIP_DB_PASSWORD"
            - name: POSTGRES_DB
              value: iip
          ports:
            - name: postgres
              containerPort: 5432
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "iip", "-d", "iip"]
            periodSeconds: 2
---
apiVersion: v1
kind: Service
metadata:
  name: iip-postgres
spec:
  selector:
    app.kubernetes.io/name: postgresql
  ports:
    - name: postgres
      port: 5432
      targetPort: postgres
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: iip-backups
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 64Mi
EOF

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    rollout status deployment/iip-postgres --timeout=180s >/dev/null

"$IIP_HELM_BIN" upgrade --install iip "$IIP_TEST_CHART" \
    --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" \
    --set "image.repository=$IIP_TEST_IMAGE_REPOSITORY" \
    --set "image.tag=$IIP_APP_VERSION" \
    --set-string "image.digest=$IIP_TEST_IMAGE_DIGEST" \
    --set image.pullPolicy=Never \
    --set database.existingSecret=iip-database \
    --set database.migrations.enabled=true \
    --set auth.existingSecret=iip-auth \
    --set investigationSignalCatalog.existingSecret=iip-investigation-signal-catalog \
    --wait --timeout 180s >/dev/null

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    wait --for=condition=complete job/iip-infra-intelligence-migrate \
    --timeout=30s >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    logs job/iip-infra-intelligence-migrate | rg -q \
    '^PostgreSQL migrations are current$'
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    rollout status deployment/iip-infra-intelligence --timeout=120s >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    exec deployment/iip-infra-intelligence -- python -c \
    'import json,urllib.request; result=json.load(urllib.request.urlopen("http://127.0.0.1:8080/readyz", timeout=5)); assert result == {"status":"ok"}'

IIP_CONSOLE_AUTHENTICATION_JSON=$(
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_TEST_NAMESPACE" exec \
        deployment/iip-infra-intelligence -- python -c \
        'import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen("http://127.0.0.1:8080/v1/authentication/console", timeout=5)), separators=(",", ":")))'
)
printf '%s' "$IIP_CONSOLE_AUTHENTICATION_JSON" | "$IIP_TEST_PYTHON" -c \
    'import json,sys; document=json.load(sys.stdin); assert document == {"apiVersion":"iip.platform/v1alpha1","kind":"ConsoleAuthenticationConfiguration","spec":{"mode":"local-token"}}'

IIP_EXPECTED_MIGRATION=$(rg --files src/iip/adapters/postgres/migrations -g '*.sql' | sort | tail -n 1)
IIP_EXPECTED_MIGRATION=$(basename "$IIP_EXPECTED_MIGRATION")
IIP_APPLIED_MIGRATION=$(
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_TEST_NAMESPACE" exec deployment/iip-postgres -- \
        psql -U iip -d iip -Atc 'SELECT max(version) FROM iip.schema_migrations'
)
if [ "$IIP_APPLIED_MIGRATION" != "$IIP_EXPECTED_MIGRATION" ]; then
    echo "Helm migration hook did not apply the latest packaged schema" >&2
    exit 1
fi

IIP_RUNTIME_VERSION_JSON=$(
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); request=urllib.request.Request("http://127.0.0.1:8080/v1/system/version", headers={"Authorization": "Bearer " + token}); print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5)), separators=(",", ":")))'
)
printf '%s' "$IIP_RUNTIME_VERSION_JSON" | "$IIP_TEST_PYTHON" -c \
    'import json,sys; document=json.load(sys.stdin); app,chart,digest,migration,mode,revision=sys.argv[1:]; spec=document["spec"]; expected_build={"mode":mode}; expected_build.update({"revision":revision} if revision else {}); assert document["kind"] == "RuntimeVersionReport"; assert document["metadata"]["tenantId"] == "helm-test"; assert spec["application"]["version"] == app; assert spec["contracts"]["apiVersion"] == "iip.platform/v1alpha1"; assert spec["storage"]["requiredMigration"] == migration; assert spec["build"] == expected_build; assert spec["deployment"] == {"helmChartVersion":chart,"imageDigest":digest}' \
    "$IIP_APP_VERSION" "$IIP_CHART_VERSION" "$IIP_TEST_IMAGE_DIGEST" \
    "$IIP_EXPECTED_MIGRATION" "$IIP_EXPECTED_BUILD_MODE" \
    "$IIP_EXPECTED_BUILD_REVISION"
printf '%s\n' "$IIP_RUNTIME_VERSION_JSON" > \
    "$IIP_TEST_TEMP_DIR/runtime-version-report.json"

IIP_TELEMETRY_DEPLOYMENT_HEALTH_JSON=$(
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); request=urllib.request.Request("http://127.0.0.1:8080/v1/operations/telemetry/deployment-export-health", headers={"Authorization": "Bearer " + token}); print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5)), separators=(",", ":")))'
)
printf '%s' "$IIP_TELEMETRY_DEPLOYMENT_HEALTH_JSON" | "$IIP_TEST_PYTHON" -c \
    'import json,sys; document=json.load(sys.stdin); spec=document["spec"]; assert document["kind"] == "TelemetryDeploymentExportHealthReport"; assert spec["status"] == "disabled"; assert spec["summary"] == {"includedInstances":0,"currentInstances":0,"staleInstances":0,"truncated":False}; assert spec["instances"] == []'

IIP_TELEMETRY_EXPORT_SLO_JSON=$(
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); request=urllib.request.Request("http://127.0.0.1:8080/v1/operations/telemetry/export-slo", headers={"Authorization": "Bearer " + token}); print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5)), separators=(",", ":")))'
)
printf '%s' "$IIP_TELEMETRY_EXPORT_SLO_JSON" | "$IIP_TEST_PYTHON" -c \
    'import json,sys; document=json.load(sys.stdin); spec=document["spec"]; assert document["kind"] == "TelemetryExportSloReport"; assert spec["status"] == "disabled"; assert spec["observation"] == {"observedInstances":0,"observedSamples":0}; assert [item["status"] for item in spec["signals"]] == ["disabled","disabled"]'

IIP_EVENT_DELIVERY_HEALTH_JSON=$(
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); request=urllib.request.Request("http://127.0.0.1:8080/v1/operations/events/delivery-health?limit=10", headers={"Authorization": "Bearer " + token}); print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5)), separators=(",", ":")))'
)
printf '%s' "$IIP_EVENT_DELIVERY_HEALTH_JSON" | "$IIP_TEST_PYTHON" -c \
    'import json,sys; document=json.load(sys.stdin); spec=document["spec"]; assert document["kind"] == "EventDeliveryHealthReport"; assert document["metadata"]["tenantId"] == "helm-test"; assert spec["status"] == "healthy"; assert spec["delivery"] == {"pendingEvents":0,"inFlightEvents":0,"retryingEvents":0,"quarantinedEvents":0}; assert spec["quarantine"] == {"limit":10,"hasMore":False,"items":[]}'

IIP_EVENT_DELIVERY_SLO_JSON=$(
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); request=urllib.request.Request("http://127.0.0.1:8080/v1/operations/events/delivery-slo", headers={"Authorization": "Bearer " + token}); print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5)), separators=(",", ":")))'
)
printf '%s' "$IIP_EVENT_DELIVERY_SLO_JSON" | "$IIP_TEST_PYTHON" -c \
    'import json,sys; document=json.load(sys.stdin); spec=document["spec"]; assert document["kind"] == "EventDeliverySloReport"; assert document["metadata"]["tenantId"] == "helm-test"; assert spec["status"] == "no-data"; assert spec["objective"] == {"maximumDeliveryLatencySeconds":60,"minimumAttainmentBasisPoints":9900,"minimumEligibleEvents":20}; assert spec["measurement"] == {"createdEvents":0,"immatureEvents":0,"eligibleEvents":0,"withinObjectiveEvents":0,"lateDeliveredEvents":0,"undeliveredEvents":0,"quarantinedEvents":0,"attainmentBasisPoints":None}'

IIP_INVESTIGATION_COMPLETION_SLO_JSON=$(
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); request=urllib.request.Request("http://127.0.0.1:8080/v1/operations/investigations/completion-slo", headers={"Authorization": "Bearer " + token}); print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5)), separators=(",", ":")))'
)
printf '%s' "$IIP_INVESTIGATION_COMPLETION_SLO_JSON" | "$IIP_TEST_PYTHON" -c \
    'import json,sys; document=json.load(sys.stdin); spec=document["spec"]; assert document["kind"] == "InvestigationCompletionSloReport"; assert document["metadata"]["tenantId"] == "helm-test"; assert spec["status"] == "no-data"; assert spec["objective"] == {"maximumCompletionSeconds":300,"minimumAttainmentBasisPoints":9900,"minimumEligibleJobs":20}; assert spec["measurement"] == {"acceptedJobs":0,"immatureJobs":0,"eligibleJobs":0,"withinObjectiveJobs":0,"lateCompletedJobs":0,"failedJobs":0,"cancelledJobs":0,"unfinishedJobs":0,"attainmentBasisPoints":None}'

IIP_EVIDENCE_RETENTION_JSON=$(
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); request=urllib.request.Request("http://127.0.0.1:8080/v1/operations/evidence/retention", headers={"Authorization": "Bearer " + token}); print(json.dumps(json.load(urllib.request.urlopen(request, timeout=5)), separators=(",", ":")))'
)
printf '%s' "$IIP_EVIDENCE_RETENTION_JSON" | "$IIP_TEST_PYTHON" -c \
    'import json,sys; document=json.load(sys.stdin); spec=document["spec"]; assert document["kind"] == "EvidenceRetentionReport"; assert document["metadata"]["tenantId"] == "helm-test"; assert spec["status"] == "disabled"; assert spec["mode"] == "observe"; assert spec["policy"]["enabled"] is False; assert spec["artifacts"] == {"storedBefore":0,"eligible":0,"expired":0,"remainingEligible":0,"legalHold":0}'

IIP_EXPECTED_MIGRATION_COUNT=$(
    rg --files src/iip/adapters/postgres/migrations -g '*.sql' | wc -l | tr -d ' '
)

"$IIP_HELM_BIN" upgrade --install iip "$IIP_TEST_CHART" \
    --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" \
    --set "image.repository=$IIP_TEST_IMAGE_REPOSITORY" \
    --set "image.tag=$IIP_APP_VERSION" \
    --set-string "image.digest=$IIP_TEST_IMAGE_DIGEST" \
    --set image.pullPolicy=Never \
    --set replicaCount=2 \
    --set database.existingSecret=iip-database \
    --set database.migrations.enabled=true \
    --set auth.existingSecret=iip-auth \
    --set investigationSignalCatalog.existingSecret=iip-investigation-signal-catalog \
    --set ingress.enabled=true \
    --set ingress.className=iip-conformance \
    --set ingress.host=iip.helm.test \
    --set ingress.tls.existingSecret=iip-tls \
    --set ingress.tlsRedirectAnnotation=example.test/force-tls \
    --set backup.enabled=true \
    --set backup.destination.existingClaim=iip-backups \
    --set networkPolicy.enabled=true \
    --set networkPolicy.databaseEgress.enabled=true \
    --set-string "networkPolicy.databaseEgress.namespaceSelector.kubernetes\\.io/metadata\\.name=$IIP_TEST_NAMESPACE" \
    --set networkPolicy.ingressController.enabled=true \
    --wait --timeout 180s >/dev/null

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    wait --for=condition=complete job/iip-infra-intelligence-migrate \
    --timeout=30s >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    rollout status deployment/iip-infra-intelligence --timeout=120s >/dev/null
IIP_APPLIED_MIGRATION_COUNT=$(
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_TEST_NAMESPACE" exec deployment/iip-postgres -- \
        psql -U iip -d iip -Atc 'SELECT count(*) FROM iip.schema_migrations'
)
if [ "$IIP_APPLIED_MIGRATION_COUNT" != "$IIP_EXPECTED_MIGRATION_COUNT" ]; then
    echo "Helm upgrade duplicated or omitted schema migrations" >&2
    exit 1
fi
IIP_INGRESS_BINDING=$(
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_TEST_NAMESPACE" get ingress/iip-infra-intelligence \
        -o 'jsonpath={.spec.ingressClassName}|{.spec.tls[0].hosts[0]}|{.spec.tls[0].secretName}|{.metadata.annotations.example\.test/force-tls}'
)
if [ "$IIP_INGRESS_BINDING" != "iip-conformance|iip.helm.test|iip-tls|true" ]; then
    echo "Helm upgrade did not preserve the explicit TLS ingress binding" >&2
    exit 1
fi
IIP_DEPLOYED_IMAGE=$(
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_TEST_NAMESPACE" get deployment/iip-infra-intelligence \
        -o 'jsonpath={.spec.template.spec.containers[0].image}'
)
if [ "$IIP_DEPLOYED_IMAGE" != "$IIP_TEST_IMAGE_REPOSITORY@$IIP_TEST_IMAGE_DIGEST" ]; then
    echo "Helm rollout did not preserve the immutable application image digest" >&2
    exit 1
fi

IIP_DEPLOYMENT_DIAGNOSTIC_REPORT="$IIP_TEST_TEMP_DIR/deployment-diagnostic-report.json"
PYTHONPATH=scripts:src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/deployment_diagnostics.py generate \
    --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" \
    --release-name iip \
    --image-digest "$IIP_TEST_IMAGE_DIGEST" \
    --kubectl "$IIP_KUBECTL_BIN" \
    --output "$IIP_DEPLOYMENT_DIAGNOSTIC_REPORT"
PYTHONPATH=scripts:src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/deployment_diagnostics.py verify \
    --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" \
    --release-name iip \
    --image-digest "$IIP_TEST_IMAGE_DIGEST" \
    --report "$IIP_DEPLOYMENT_DIAGNOSTIC_REPORT"
"$IIP_TEST_PYTHON" -c \
    'import json,sys; report=json.load(open(sys.argv[1])); spec=report["spec"]; assert spec["status"] in ("healthy", "attention-required"); assert [item["state"] for item in spec["components"]] == ["healthy", "not-observed", "not-observed"]; assert all(item["status"] == "passed" for item in spec["checks"][1:])' \
    "$IIP_DEPLOYMENT_DIAGNOSTIC_REPORT"

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create job --from=cronjob/iip-infra-intelligence-backup \
    iip-backup-conformance >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    wait --for=condition=complete job/iip-backup-conformance \
    --timeout=180s >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    logs job/iip-backup-conformance | rg -q '^backup complete:'

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    apply -f - >/dev/null <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: iip-backup-restore-conformance
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 180
  template:
    metadata:
      labels:
        app.kubernetes.io/name: infra-intelligence
        app.kubernetes.io/instance: iip
        app.kubernetes.io/component: database-backup
    spec:
      automountServiceAccountToken: false
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 70
        runAsGroup: 70
        fsGroup: 70
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: restore-check
          image: postgres@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15
          command: ["/bin/sh", "-ec"]
          args:
            - |
              cd /var/lib/iip-backups
              set -- *.dump.sha256
              [ "\$#" -eq 1 ]
              sha256sum -c "\$1"
              dump="\${1%.sha256}"
              pg_restore --list "\$dump" >/dev/null
              trap 'dropdb --if-exists iip_backup_restore_check >/dev/null 2>&1 || true' EXIT
              dropdb --if-exists iip_backup_restore_check >/dev/null 2>&1 || true
              createdb iip_backup_restore_check
              pg_restore --no-owner --no-acl --dbname=iip_backup_restore_check "\$dump"
              migration_count=\$(psql --dbname=iip_backup_restore_check -Atc 'SELECT count(*) FROM iip.schema_migrations')
              [ "\$migration_count" = "$IIP_EXPECTED_MIGRATION_COUNT" ]
              echo "restore verified: migrations=\$migration_count"
          env:
            - name: PGHOST
              value: iip-postgres
            - name: PGUSER
              value: iip
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: iip-database
                  key: password
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: backup
              mountPath: /var/lib/iip-backups
              readOnly: true
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: backup
          persistentVolumeClaim:
            claimName: iip-backups
        - name: tmp
          emptyDir: {}
EOF
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    wait --for=condition=complete job/iip-backup-restore-conformance \
    --timeout=180s >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    logs job/iip-backup-restore-conformance | rg -q \
    "^restore verified: migrations=$IIP_EXPECTED_MIGRATION_COUNT$"

"$IIP_HELM_BIN" history iip \
    --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" --output json |
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; rows=json.load(sys.stdin); assert len(rows) == 2 and str(rows[-1]["revision"]) == "2" and rows[-1]["status"] == "deployed"'

if [ "$IIP_EXPECTED_BUILD_MODE" = "release" ]; then
    IIP_QUALIFICATION_ARCHITECTURE=$(
        "$IIP_DOCKER_BIN" info --format '{{.Architecture}}'
    )
    case "$IIP_QUALIFICATION_ARCHITECTURE" in
        aarch64) IIP_QUALIFICATION_ARCHITECTURE=arm64 ;;
        x86_64) IIP_QUALIFICATION_ARCHITECTURE=amd64 ;;
    esac
    IIP_QUALIFICATION_KUBERNETES_VERSION=$(
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get nodes \
            -o 'jsonpath={.items[0].status.nodeInfo.kubeletVersion}'
    )
    IIP_QUALIFICATION_DOCKER_VERSION=$(
        "$IIP_DOCKER_BIN" version --format '{{.Server.Version}}'
    )
    "$IIP_TEST_PYTHON" scripts/release_qualification.py record-install \
        "$IIP_RELEASE_BUNDLE" "$IIP_RELEASE_QUALIFICATION_REPORT" \
        --runtime-report "$IIP_TEST_TEMP_DIR/runtime-version-report.json" \
        --required-migration "$IIP_EXPECTED_MIGRATION" \
        --applied-migration-count "$IIP_APPLIED_MIGRATION_COUNT" \
        --final-helm-revision 2 \
        --platform "linux/$IIP_QUALIFICATION_ARCHITECTURE" \
        --kubernetes-version "$IIP_QUALIFICATION_KUBERNETES_VERSION" \
        --container-runtime-version "$IIP_QUALIFICATION_DOCKER_VERSION"
    "$IIP_TEST_PYTHON" scripts/release_qualification.py verify \
        "$IIP_RELEASE_BUNDLE" "$IIP_RELEASE_QUALIFICATION_REPORT"
    echo "Packaged release install/upgrade test passed: verified bundle -> immutable image -> release identity -> migrations -> TLS ingress -> backup/restore -> qualification evidence=$IIP_RELEASE_QUALIFICATION_REPORT"
else
    echo "Helm install/upgrade test passed: immutable image -> runtime identity -> delivery/SLO/retention operations -> migrations -> TLS ingress -> backup/restore"
fi
