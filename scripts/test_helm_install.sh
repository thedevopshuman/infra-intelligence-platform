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

IIP_APP_VERSION=$(
    "$IIP_TEST_PYTHON" -c \
        'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
)
IIP_TEST_IMAGE="iip-local-platform:$IIP_APP_VERSION"
IIP_KIND_CLUSTER=${IIP_KUBE_CONTEXT#kind-}
IIP_DB_PASSWORD=$(openssl rand -hex 24)
IIP_AUTH_VERIFIER="sha256:$(openssl rand -hex 32)"
IIP_AUTH_IDENTITIES_JSON=$(printf '%s' \
    "{\"identities\":[{\"tokenSha256\":\"$IIP_AUTH_VERIFIER\",\"actorId\":\"helm-test-operator\",\"tenantId\":\"helm-test\",\"roles\":[\"developer\"]}]}"
)

"$IIP_DOCKER_BIN" build --tag "$IIP_TEST_IMAGE" . >/dev/null
"$IIP_KIND_BIN" load docker-image "$IIP_TEST_IMAGE" \
    --name "$IIP_KIND_CLUSTER" >/dev/null

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" create namespace \
    "$IIP_TEST_NAMESPACE" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create secret generic iip-database \
    --from-literal="database-url=postgresql://iip:$IIP_DB_PASSWORD@iip-postgres:5432/iip" \
    >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create secret generic iip-auth \
    --from-literal="identities-json=$IIP_AUTH_IDENTITIES_JSON" >/dev/null
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
          image: postgres:18.4-alpine
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
EOF

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    rollout status deployment/iip-postgres --timeout=180s >/dev/null

"$IIP_HELM_BIN" upgrade --install iip deploy/helm/infra-intelligence \
    --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" \
    --set image.repository=iip-local-platform \
    --set "image.tag=$IIP_APP_VERSION" \
    --set image.pullPolicy=Never \
    --set database.existingSecret=iip-database \
    --set database.migrations.enabled=true \
    --set auth.existingSecret=iip-auth \
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

IIP_EXPECTED_MIGRATION_COUNT=$(
    rg --files src/iip/adapters/postgres/migrations -g '*.sql' | wc -l | tr -d ' '
)

"$IIP_HELM_BIN" upgrade --install iip deploy/helm/infra-intelligence \
    --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" \
    --set image.repository=iip-local-platform \
    --set "image.tag=$IIP_APP_VERSION" \
    --set image.pullPolicy=Never \
    --set replicaCount=2 \
    --set database.existingSecret=iip-database \
    --set database.migrations.enabled=true \
    --set auth.existingSecret=iip-auth \
    --set ingress.enabled=true \
    --set ingress.className=iip-conformance \
    --set ingress.host=iip.helm.test \
    --set ingress.tls.existingSecret=iip-tls \
    --set ingress.tlsRedirectAnnotation=example.test/force-tls \
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
"$IIP_HELM_BIN" history iip \
    --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" --output json |
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; rows=json.load(sys.stdin); assert len(rows) == 2 and str(rows[-1]["revision"]) == "2" and rows[-1]["status"] == "deployed"'

echo "Helm install/upgrade test passed: migration hook -> readiness -> TLS ingress binding -> idempotent upgrade"
