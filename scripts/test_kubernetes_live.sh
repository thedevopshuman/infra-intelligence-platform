#!/bin/sh
set -eu

IIP_LIVE_CONTEXT=${IIP_KUBE_CONTEXT:-kind-iip-dev}
IIP_LIVE_KUBECONFIG=${IIP_KUBECONFIG:?set IIP_KUBECONFIG to an explicit kubeconfig path}
IIP_LIVE_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_LIVE_RESULT=$(mktemp)

cleanup() {
    rm -f "$IIP_LIVE_RESULT"
}
trap cleanup EXIT INT TERM

kubectl --kubeconfig "$IIP_LIVE_KUBECONFIG" --context "$IIP_LIVE_CONTEXT" \
    apply -f deploy/kubernetes/dev/seed-incident.yaml >/dev/null

PYTHONPATH=sdks/python/src:plugins/examples/kubernetes-observer/src \
    "$IIP_LIVE_PYTHON" -m kubernetes_observer \
    --request plugins/examples/kubernetes-observer/fixtures/live-collection-request.json \
    --live-context "$IIP_LIVE_CONTEXT" \
    --kubeconfig "$IIP_LIVE_KUBECONFIG" >"$IIP_LIVE_RESULT"

PYTHONPATH=scripts "$IIP_LIVE_PYTHON" - "$IIP_LIVE_RESULT" <<'PY'
import json
import sys
from pathlib import Path

import validate_schemas

path = Path(sys.argv[1])
document = json.loads(path.read_text(encoding="utf-8"))
schema = json.loads(
    Path("contracts/schemas/resource-collection-result.schema.json").read_text(
        encoding="utf-8"
    )
)
errors = validate_schemas.instance_validation_errors(
    schema, document, label="live Kubernetes collection"
)
if errors:
    raise SystemExit("\n".join(errors))
observations = document["spec"]["observations"]
failed_pods = [
    item
    for item in observations
    if item["spec"]["type"] == "core/pod"
    and item["spec"]["attributes"].get("waitingReason")
    in ("ErrImagePull", "ImagePullBackOff")
    and item["status"]["health"] == "unhealthy"
]
if document["spec"]["completion"]["status"] != "complete" or not failed_pods:
    raise SystemExit("live collection did not expose the seeded image-pull failure")
print(
    f"live Kubernetes collection passed: {len(observations)} resources, "
    f"{len(failed_pods)} unhealthy image-pull pod(s)"
)
PY

PYTHONPATH=src:sdks/python/src "$IIP_LIVE_PYTHON" scripts/run_reference_workflow.py \
    --request plugins/examples/kubernetes-observer/fixtures/live-collection-request.json \
    --result "$IIP_LIVE_RESULT" \
    --plugin-manifest contracts/examples/plugin-manifest.json
