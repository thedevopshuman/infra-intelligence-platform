#!/bin/sh
set -eu

IIP_ACTION_CONTEXT=${IIP_KUBE_CONTEXT:-kind-iip-dev}
IIP_ACTION_KUBECONFIG=${IIP_KUBECONFIG:?set IIP_KUBECONFIG to an explicit kubeconfig path}
IIP_ACTION_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_ACTION_NAMESPACE=iip-action-test
IIP_ACTION_CLUSTER_ID=cluster-action-live-test
IIP_ACTION_CONTEXT_DOCUMENT=$(mktemp)
IIP_ACTION_CA_BUNDLE=$(mktemp)
IIP_ACTION_ENDPOINT_FILE=$(mktemp)

cleanup() {
    kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
        delete namespace "$IIP_ACTION_NAMESPACE" --ignore-not-found \
        --wait=true --timeout=30s >/dev/null 2>&1 || true
    rm -f "$IIP_ACTION_CONTEXT_DOCUMENT" "$IIP_ACTION_CA_BUNDLE" \
        "$IIP_ACTION_ENDPOINT_FILE"
}
trap cleanup EXIT INT TERM

if ! command -v kubectl >/dev/null 2>&1; then
    echo "kubectl is required for the Kubernetes action integration test" >&2
    exit 2
fi

cleanup
kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
    apply -f deploy/kubernetes/dev/action-executor-fixture.yaml >/dev/null
kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
    --namespace "$IIP_ACTION_NAMESPACE" rollout status deployment/restart-probe \
    --timeout=120s >/dev/null

IIP_ACTION_PROVIDER_UID=$(
    kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
        --namespace "$IIP_ACTION_NAMESPACE" get deployment restart-probe \
        -o jsonpath='{.metadata.uid}'
)
IIP_ACTION_TOKEN=$(
    kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
        --namespace "$IIP_ACTION_NAMESPACE" create token iip-action-executor --duration=10m
)
kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
    config view --raw --minify -o json >"$IIP_ACTION_CONTEXT_DOCUMENT"

"$IIP_ACTION_PYTHON" - "$IIP_ACTION_CONTEXT_DOCUMENT" "$IIP_ACTION_CA_BUNDLE" \
    "$IIP_ACTION_ENDPOINT_FILE" <<'PY'
import base64
import json
import sys
from pathlib import Path

context_path, ca_path, endpoint_path = map(Path, sys.argv[1:])
document = json.loads(context_path.read_text(encoding="utf-8"))
cluster = document["clusters"][0]["cluster"]
server = cluster["server"]
encoded = cluster.get("certificate-authority-data")
source = cluster.get("certificate-authority")
if not isinstance(server, str) or not server.startswith("https://"):
    raise SystemExit("selected context does not expose an HTTPS Kubernetes API")
if isinstance(encoded, str) and encoded:
    certificate = base64.b64decode(encoded, validate=True)
elif isinstance(source, str) and source:
    certificate = Path(source).read_bytes()
else:
    raise SystemExit("selected context does not expose a trusted CA bundle")
ca_path.write_bytes(certificate)
endpoint_path.write_text(server, encoding="utf-8")
PY

IIP_ACTION_SUBJECT="system:serviceaccount:$IIP_ACTION_NAMESPACE:iip-action-executor"
kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
    auth can-i get deployment/restart-probe --namespace "$IIP_ACTION_NAMESPACE" \
    --as "$IIP_ACTION_SUBJECT" | grep -qx yes
kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
    auth can-i patch deployment/restart-probe --namespace "$IIP_ACTION_NAMESPACE" \
    --as "$IIP_ACTION_SUBJECT" | grep -qx yes
kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
    auth can-i update deployment/restart-probe --namespace "$IIP_ACTION_NAMESPACE" \
    --as "$IIP_ACTION_SUBJECT" | grep -qx no
kubectl --kubeconfig "$IIP_ACTION_KUBECONFIG" --context "$IIP_ACTION_CONTEXT" \
    auth can-i get secrets --namespace "$IIP_ACTION_NAMESPACE" \
    --as "$IIP_ACTION_SUBJECT" | grep -qx no

IIP_ACTION_ENDPOINT=$(tr -d '\n' <"$IIP_ACTION_ENDPOINT_FILE")
IIP_TEST_KUBERNETES_ACTIONS_ENDPOINT="$IIP_ACTION_ENDPOINT" \
IIP_TEST_KUBERNETES_ACTIONS_CA_BUNDLE="$IIP_ACTION_CA_BUNDLE" \
IIP_TEST_KUBERNETES_ACTIONS_TOKEN="$IIP_ACTION_TOKEN" \
IIP_TEST_KUBERNETES_ACTIONS_CLUSTER_ID="$IIP_ACTION_CLUSTER_ID" \
IIP_TEST_KUBERNETES_ACTIONS_NAMESPACE="$IIP_ACTION_NAMESPACE" \
IIP_TEST_KUBERNETES_ACTIONS_PROVIDER_UID="$IIP_ACTION_PROVIDER_UID" \
PYTHONPATH=src:sdks/python/src \
    "$IIP_ACTION_PYTHON" -m unittest tests.test_kubernetes_actions_integration -v

echo "Kubernetes action integration passed with server dry-run, live verification, and exact RBAC"
