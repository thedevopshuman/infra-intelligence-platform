#!/bin/sh
set -eu

IIP_EVENT_CONTEXT=${IIP_KUBE_CONTEXT:-kind-iip-dev}
IIP_EVENT_KUBECONFIG=${IIP_KUBECONFIG:?set IIP_KUBECONFIG to an explicit kubeconfig path}
IIP_EVENT_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_EVENT_NAMESPACE=iip-event-test
IIP_EVENT_CLUSTER_ID=cluster-live-test
IIP_EVENT_DOCUMENT=$(mktemp)
IIP_EVENT_CONTEXT_DOCUMENT=$(mktemp)
IIP_EVENT_CA_BUNDLE=$(mktemp)
IIP_EVENT_ENDPOINT_FILE=$(mktemp)

cleanup() {
    kubectl --kubeconfig "$IIP_EVENT_KUBECONFIG" --context "$IIP_EVENT_CONTEXT" \
        delete namespace "$IIP_EVENT_NAMESPACE" --ignore-not-found \
        --wait=true --timeout=20s >/dev/null 2>&1 || true
    rm -f "$IIP_EVENT_DOCUMENT" "$IIP_EVENT_CONTEXT_DOCUMENT" \
        "$IIP_EVENT_CA_BUNDLE" "$IIP_EVENT_ENDPOINT_FILE"
}
trap cleanup EXIT INT TERM

if ! command -v kubectl >/dev/null 2>&1; then
    echo "kubectl is required for the Kubernetes Event integration test" >&2
    exit 2
fi

cleanup
kubectl --kubeconfig "$IIP_EVENT_KUBECONFIG" --context "$IIP_EVENT_CONTEXT" \
    apply -f deploy/kubernetes/dev/event-evidence-fixture.yaml >/dev/null

IIP_EVENT_RESOURCE_UID=$(
    kubectl --kubeconfig "$IIP_EVENT_KUBECONFIG" --context "$IIP_EVENT_CONTEXT" \
        --namespace "$IIP_EVENT_NAMESPACE" get deployment event-probe \
        -o jsonpath='{.metadata.uid}'
)

"$IIP_EVENT_PYTHON" - "$IIP_EVENT_DOCUMENT" "$IIP_EVENT_RESOURCE_UID" \
    "$IIP_EVENT_NAMESPACE" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
resource_uid = sys.argv[2]
namespace = sys.argv[3]
now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
document = {
    "apiVersion": "v1",
    "kind": "Event",
    "metadata": {"name": "event-probe-progress-deadline", "namespace": namespace},
    "involvedObject": {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "namespace": namespace,
        "name": "event-probe",
        "uid": resource_uid,
    },
    "reason": "ProgressDeadlineExceeded",
    "type": "Warning",
    "firstTimestamp": now,
    "lastTimestamp": now,
    "count": 1,
    "source": {"component": "integration-test"},
    "message": "The integration-test Deployment exceeded its progress deadline.",
}
path.write_text(json.dumps(document), encoding="utf-8")
PY

kubectl --kubeconfig "$IIP_EVENT_KUBECONFIG" --context "$IIP_EVENT_CONTEXT" \
    create -f "$IIP_EVENT_DOCUMENT" >/dev/null

IIP_EVENT_TOKEN=$(
    kubectl --kubeconfig "$IIP_EVENT_KUBECONFIG" --context "$IIP_EVENT_CONTEXT" \
        --namespace "$IIP_EVENT_NAMESPACE" create token iip-event-reader --duration=10m
)
kubectl --kubeconfig "$IIP_EVENT_KUBECONFIG" --context "$IIP_EVENT_CONTEXT" \
    config view --raw --minify -o json >"$IIP_EVENT_CONTEXT_DOCUMENT"

"$IIP_EVENT_PYTHON" - "$IIP_EVENT_CONTEXT_DOCUMENT" "$IIP_EVENT_CA_BUNDLE" \
    "$IIP_EVENT_ENDPOINT_FILE" <<'PY'
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

IIP_EVENT_ENDPOINT=$(tr -d '\n' <"$IIP_EVENT_ENDPOINT_FILE")
IIP_TEST_KUBERNETES_EVENTS_ENDPOINT="$IIP_EVENT_ENDPOINT" \
IIP_TEST_KUBERNETES_EVENTS_CA_BUNDLE="$IIP_EVENT_CA_BUNDLE" \
IIP_TEST_KUBERNETES_EVENTS_TOKEN="$IIP_EVENT_TOKEN" \
IIP_TEST_KUBERNETES_EVENTS_CLUSTER_ID="$IIP_EVENT_CLUSTER_ID" \
IIP_TEST_KUBERNETES_EVENTS_NAMESPACE="$IIP_EVENT_NAMESPACE" \
PYTHONPATH=src:sdks/python/src \
    "$IIP_EVENT_PYTHON" -m unittest tests.test_kubernetes_events_integration -v

echo "Kubernetes API Event evidence integration test passed with read-only adapter access"
