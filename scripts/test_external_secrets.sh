#!/bin/sh
set -eu

IIP_KUBECTL_BIN=${IIP_KUBECTL_BIN:-kubectl}
IIP_HELM_BIN=${IIP_HELM_BIN:-helm}
IIP_CURL_BIN=${IIP_CURL_BIN:-curl}
IIP_KUBE_CONTEXT=${IIP_KUBE_CONTEXT:-kind-iip-dev}
IIP_ESO_SYSTEM_NAMESPACE=${IIP_ESO_SYSTEM_NAMESPACE:-iip-external-secrets-system}
IIP_ESO_SOURCE_NAMESPACE=${IIP_ESO_SOURCE_NAMESPACE:-iip-external-secrets-source}
IIP_ESO_TARGET_NAMESPACE=${IIP_ESO_TARGET_NAMESPACE:-iip-external-secrets-test}
IIP_ESO_CHART_VERSION=2.10.0
IIP_ESO_CHART_SHA256=b96e948fff3674638b5d3f9e43886f3796e04739c4b4127929aed2ddac7d1418
IIP_ESO_IMAGE_DIGEST=sha256:814117b0fd6d121b03e8ba3b6db1cecbe7449a354fc0fc9c4faf73a37aa221b1
IIP_ESO_CHART_URL=https://github.com/external-secrets/external-secrets/releases/download/helm-chart-2.10.0/external-secrets-2.10.0.tgz
IIP_TEST_TEMP_DIR=$(mktemp -d)
IIP_ESO_CHART=$IIP_TEST_TEMP_DIR/external-secrets-2.10.0.tgz

cleanup_resources() {
    # Let the controller remove its exact finalizers before uninstalling it.
    # If a previous interrupted run already removed the controller, clear only
    # the two disposable objects so the dedicated test namespace can terminate.
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_ESO_TARGET_NAMESPACE" delete externalsecret \
        iip-database iip-auth --ignore-not-found --wait=true \
        --timeout=30s >/dev/null 2>&1 || true
    for external_secret in iip-database iip-auth; do
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_ESO_TARGET_NAMESPACE" patch externalsecret \
            "$external_secret" --type=merge \
            --patch '{"metadata":{"finalizers":[]}}' >/dev/null 2>&1 || true
    done
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_ESO_TARGET_NAMESPACE" delete secretstore \
        iip-source-store --ignore-not-found --wait=false >/dev/null 2>&1 || true
    "$IIP_HELM_BIN" --kube-context "$IIP_KUBE_CONTEXT" uninstall external-secrets \
        --namespace "$IIP_ESO_SYSTEM_NAMESPACE" >/dev/null 2>&1 || true
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" delete clusterrolebinding \
        iip-external-secret-source-review >/dev/null 2>&1 || true
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" delete clusterrole \
        iip-external-secret-source-review >/dev/null 2>&1 || true
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" delete namespace \
        "$IIP_ESO_SOURCE_NAMESPACE" "$IIP_ESO_TARGET_NAMESPACE" \
        "$IIP_ESO_SYSTEM_NAMESPACE" --wait=false >/dev/null 2>&1 || true
}

cleanup() {
    cleanup_resources
    rm -rf "$IIP_TEST_TEMP_DIR"
}

trap cleanup EXIT INT TERM

for binary in "$IIP_KUBECTL_BIN" "$IIP_HELM_BIN" "$IIP_CURL_BIN" jq openssl shasum; do
    if ! command -v "$binary" >/dev/null 2>&1; then
        echo "Required executable not found: $binary" >&2
        exit 127
    fi
done

if [ "$("$IIP_KUBECTL_BIN" config current-context)" != "$IIP_KUBE_CONTEXT" ]; then
    echo "Refusing external-secret test outside context $IIP_KUBE_CONTEXT" >&2
    exit 2
fi
case "$IIP_KUBE_CONTEXT" in
    kind-*) ;;
    *)
        echo "Refusing external-secret test outside a Kind context" >&2
        exit 2
        ;;
esac

cleanup_resources
for attempt in $(seq 1 120); do
    if ! "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get namespace \
        "$IIP_ESO_SOURCE_NAMESPACE" >/dev/null 2>&1 \
        && ! "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get namespace \
        "$IIP_ESO_TARGET_NAMESPACE" >/dev/null 2>&1 \
        && ! "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get namespace \
        "$IIP_ESO_SYSTEM_NAMESPACE" >/dev/null 2>&1; then
        break
    fi
    if [ "$attempt" -eq 120 ]; then
        echo "Timed out waiting for prior disposable namespaces" >&2
        exit 1
    fi
    sleep 1
done

"$IIP_CURL_BIN" --fail --silent --show-error --location \
    --output "$IIP_ESO_CHART" "$IIP_ESO_CHART_URL"
IIP_ACTUAL_CHART_SHA256=$(shasum -a 256 "$IIP_ESO_CHART" | awk '{print $1}')
if [ "$IIP_ACTUAL_CHART_SHA256" != "$IIP_ESO_CHART_SHA256" ]; then
    echo "External Secrets Operator chart digest mismatch" >&2
    exit 1
fi

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" create namespace \
    "$IIP_ESO_SYSTEM_NAMESPACE" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" create namespace \
    "$IIP_ESO_SOURCE_NAMESPACE" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" create namespace \
    "$IIP_ESO_TARGET_NAMESPACE" >/dev/null

IIP_ESO_IMAGE_TAG="v${IIP_ESO_CHART_VERSION}"
IIP_ESO_IMAGE_REFERENCE="$IIP_ESO_IMAGE_TAG@$IIP_ESO_IMAGE_DIGEST"
"$IIP_HELM_BIN" --kube-context "$IIP_KUBE_CONTEXT" upgrade --install \
    external-secrets "$IIP_ESO_CHART" \
    --namespace "$IIP_ESO_SYSTEM_NAMESPACE" \
    --set installCRDs=true \
    --set bitwarden-sdk-server.enabled=false \
    --set-string image.tag="$IIP_ESO_IMAGE_REFERENCE" \
    --set-string webhook.image.tag="$IIP_ESO_IMAGE_REFERENCE" \
    --set-string certController.image.tag="$IIP_ESO_IMAGE_REFERENCE" \
    --wait --timeout 600s >/dev/null

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: iip-external-secret-reader
automountServiceAccountToken: false
EOF

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_SOURCE_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: iip-external-secret-source-read
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["iip-upstream-core"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: iip-external-secret-source-read
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: iip-external-secret-source-read
subjects:
  - kind: ServiceAccount
    name: iip-external-secret-reader
    namespace: $IIP_ESO_TARGET_NAMESPACE
EOF

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" apply -f - >/dev/null <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: iip-external-secret-source-review
rules:
  - apiGroups: ["authorization.k8s.io"]
    resources: ["selfsubjectrulesreviews", "selfsubjectaccessreviews"]
    verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: iip-external-secret-source-review
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: iip-external-secret-source-review
subjects:
  - kind: ServiceAccount
    name: iip-external-secret-reader
    namespace: $IIP_ESO_TARGET_NAMESPACE
EOF

IIP_DATABASE_PASSWORD=$(openssl rand -hex 24)
IIP_AUTH_TOKEN=$(openssl rand -hex 32)
IIP_AUTH_DIGEST=$(printf '%s' "$IIP_AUTH_TOKEN" | shasum -a 256 | awk '{print $1}')
IIP_DATABASE_URL="postgresql://iip:$IIP_DATABASE_PASSWORD@postgresql.database.svc:5432/iip?sslmode=require"
IIP_AUTH_DOCUMENT="{\"verifiers\":[{\"tokenSha256\":\"sha256:$IIP_AUTH_DIGEST\",\"actorId\":\"local-secret-test\",\"tenantId\":\"local-secret-test\",\"roles\":[\"developer\"]}]}"
IIP_DATABASE_URL_B64=$(printf '%s' "$IIP_DATABASE_URL" | base64 | tr -d '\n')
IIP_AUTH_DOCUMENT_B64=$(printf '%s' "$IIP_AUTH_DOCUMENT" | base64 | tr -d '\n')

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_SOURCE_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: iip-upstream-core
type: Opaque
data:
  database-url: $IIP_DATABASE_URL_B64
  identities-json: $IIP_AUTH_DOCUMENT_B64
EOF

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: iip-source-store
spec:
  provider:
    kubernetes:
      remoteNamespace: $IIP_ESO_SOURCE_NAMESPACE
      server:
        caProvider:
          type: ConfigMap
          name: kube-root-ca.crt
          key: ca.crt
      auth:
        serviceAccount:
          name: iip-external-secret-reader
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: iip-database
spec:
  refreshPolicy: Periodic
  refreshInterval: 5s
  secretStoreRef:
    kind: SecretStore
    name: iip-source-store
  target:
    name: iip-database
    creationPolicy: Owner
    deletionPolicy: Retain
  data:
    - secretKey: database-url
      remoteRef:
        key: iip-upstream-core
        property: database-url
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: iip-auth
spec:
  refreshPolicy: Periodic
  refreshInterval: 5s
  secretStoreRef:
    kind: SecretStore
    name: iip-source-store
  target:
    name: iip-auth
    creationPolicy: Owner
    deletionPolicy: Retain
  data:
    - secretKey: identities-json
      remoteRef:
        key: iip-upstream-core
        property: identities-json
EOF

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" wait \
    --for=condition=Ready secretstore/iip-source-store --timeout=90s >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" wait \
    --for=condition=Ready externalsecret/iip-database \
    externalsecret/iip-auth --timeout=90s >/dev/null

IIP_SERVICE_ACCOUNT="system:serviceaccount:$IIP_ESO_TARGET_NAMESPACE:iip-external-secret-reader"
if [ "$("$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" auth can-i \
    --as="$IIP_SERVICE_ACCOUNT" get secret/iip-upstream-core \
    --namespace "$IIP_ESO_SOURCE_NAMESPACE")" != yes ]; then
    echo "External-secret reader lacks its exact source grant" >&2
    exit 1
fi
if [ "$("$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" auth can-i \
    --as="$IIP_SERVICE_ACCOUNT" list secrets \
    --namespace "$IIP_ESO_SOURCE_NAMESPACE")" != no ]; then
    echo "External-secret reader can list source secrets" >&2
    exit 1
fi
if [ "$("$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" auth can-i \
    --as="$IIP_SERVICE_ACCOUNT" get secret/not-authorized \
    --namespace "$IIP_ESO_SOURCE_NAMESPACE")" != no ]; then
    echo "External-secret reader can read an unrelated source secret" >&2
    exit 1
fi

IIP_TARGET_DATABASE_KEYS=$("$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" get secret iip-database -o json | \
    jq -r '.data | keys | join(",")')
IIP_TARGET_AUTH_KEYS=$("$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" get secret iip-auth -o json | \
    jq -r '.data | keys | join(",")')
if [ "$IIP_TARGET_DATABASE_KEYS" != database-url ] \
    || [ "$IIP_TARGET_AUTH_KEYS" != identities-json ]; then
    echo "External-secret target contains unexpected keys" >&2
    exit 1
fi

IIP_INITIAL_TARGET_AUTH=$("$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" get secret iip-auth \
    -o jsonpath='{.data.identities-json}')
IIP_ROTATED_AUTH_TOKEN=$(openssl rand -hex 32)
IIP_ROTATED_AUTH_DIGEST=$(printf '%s' "$IIP_ROTATED_AUTH_TOKEN" | \
    shasum -a 256 | awk '{print $1}')
IIP_ROTATED_AUTH_DOCUMENT="{\"verifiers\":[{\"tokenSha256\":\"sha256:$IIP_ROTATED_AUTH_DIGEST\",\"actorId\":\"local-secret-test\",\"tenantId\":\"local-secret-test\",\"roles\":[\"developer\"]}]}"
IIP_ROTATED_AUTH_B64=$(printf '%s' "$IIP_ROTATED_AUTH_DOCUMENT" | base64 | tr -d '\n')
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_SOURCE_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: iip-upstream-core
type: Opaque
data:
  database-url: $IIP_DATABASE_URL_B64
  identities-json: $IIP_ROTATED_AUTH_B64
EOF
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" annotate externalsecret iip-auth \
    force-sync="$(date +%s)" --overwrite >/dev/null

for attempt in $(seq 1 60); do
    IIP_ROTATED_TARGET_AUTH=$("$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_ESO_TARGET_NAMESPACE" get secret iip-auth \
        -o jsonpath='{.data.identities-json}')
    if [ "$IIP_ROTATED_TARGET_AUTH" = "$IIP_ROTATED_AUTH_B64" ] \
        && [ "$IIP_ROTATED_TARGET_AUTH" != "$IIP_INITIAL_TARGET_AUTH" ]; then
        break
    fi
    if [ "$attempt" -eq 60 ]; then
        echo "External-secret rotation did not reach the target" >&2
        exit 1
    fi
    sleep 1
done

"$IIP_HELM_BIN" template iip deploy/helm/infra-intelligence \
    --namespace "$IIP_ESO_TARGET_NAMESPACE" \
    --set database.existingSecret=iip-database \
    --set auth.existingSecret=iip-auth >/dev/null

echo "External Secrets Operator compatibility passed: verified chart -> pinned controller -> exact-key materialization -> least authority -> rotation -> IIP render"
