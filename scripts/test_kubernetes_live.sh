#!/bin/sh
set -eu

IIP_LIVE_CONTEXT=${IIP_KUBE_CONTEXT:-kind-iip-dev}
IIP_LIVE_KUBECONFIG=${IIP_KUBECONFIG:?set IIP_KUBECONFIG to an explicit kubeconfig path}
IIP_LIVE_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_LIVE_RESULT=$(mktemp)
IIP_LIVE_NEXT_REQUEST=$(mktemp)
IIP_LIVE_NEXT_RESULT=$(mktemp)

cleanup() {
    rm -f "$IIP_LIVE_RESULT" "$IIP_LIVE_NEXT_REQUEST" "$IIP_LIVE_NEXT_RESULT"
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

"$IIP_LIVE_PYTHON" - \
    plugins/examples/kubernetes-observer/fixtures/live-collection-request.json \
    "$IIP_LIVE_RESULT" "$IIP_LIVE_NEXT_REQUEST" <<'PY'
import json
import sys
from pathlib import Path

request_path, result_path, output_path = map(Path, sys.argv[1:])
request = json.loads(request_path.read_text(encoding="utf-8"))
result = json.loads(result_path.read_text(encoding="utf-8"))
request["metadata"]["requestId"] = "col_55555555555555555555555555555555"
request["spec"]["snapshotId"] = "snap_55555555555555555555555555555555"
request["spec"]["startSequence"] = result["spec"]["completion"]["nextSequence"]
output_path.write_text(json.dumps(request), encoding="utf-8")
PY

kubectl --kubeconfig "$IIP_LIVE_KUBECONFIG" --context "$IIP_LIVE_CONTEXT" \
    --namespace iip-demo delete configmap reconciliation-probe --ignore-not-found >/dev/null

PYTHONPATH=sdks/python/src:plugins/examples/kubernetes-observer/src \
    "$IIP_LIVE_PYTHON" -m kubernetes_observer \
    --request "$IIP_LIVE_NEXT_REQUEST" \
    --live-context "$IIP_LIVE_CONTEXT" \
    --kubeconfig "$IIP_LIVE_KUBECONFIG" >"$IIP_LIVE_NEXT_RESULT"

PYTHONPATH=src:sdks/python/src "$IIP_LIVE_PYTHON" scripts/run_reference_workflow.py \
    --request plugins/examples/kubernetes-observer/fixtures/live-collection-request.json \
    --result "$IIP_LIVE_RESULT" \
    --next-request "$IIP_LIVE_NEXT_REQUEST" \
    --next-result "$IIP_LIVE_NEXT_RESULT" \
    --plugin-manifest contracts/examples/plugin-manifest.json
