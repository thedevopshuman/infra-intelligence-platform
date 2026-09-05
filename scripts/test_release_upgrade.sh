#!/bin/sh
set -eu

IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_HELM_BIN=${IIP_HELM_BIN:-helm}
IIP_KIND_BIN=${IIP_KIND_BIN:-kind}
IIP_KUBECTL_BIN=${IIP_KUBECTL_BIN:-kubectl}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_KUBE_CONTEXT=${IIP_KUBE_CONTEXT:-kind-iip-dev}
IIP_TEST_NAMESPACE=${IIP_TEST_NAMESPACE:-iip-release-upgrade-test}
IIP_KEEP_TEST_NAMESPACE=${IIP_KEEP_TEST_NAMESPACE:-false}
IIP_RELEASE_BUNDLE=${IIP_RELEASE_BUNDLE:-}
IIP_UPGRADE_FROM_REVISION=${IIP_UPGRADE_FROM_REVISION:-}
IIP_TEST_TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/iip-release-upgrade.XXXXXX")

case "$IIP_KUBE_CONTEXT" in
    kind-*) ;;
    *)
        echo "Refusing release upgrade test outside an explicit kind context" >&2
        exit 2
        ;;
esac

case "$IIP_RELEASE_BUNDLE" in
    /*) ;;
    *)
        echo "IIP_RELEASE_BUNDLE must be an absolute directory" >&2
        exit 2
        ;;
esac

if [ -z "$IIP_UPGRADE_FROM_REVISION" ]; then
    echo "IIP_UPGRADE_FROM_REVISION is required" >&2
    exit 2
fi

for command in "$IIP_DOCKER_BIN" "$IIP_HELM_BIN" "$IIP_KIND_BIN" \
    "$IIP_KUBECTL_BIN" git openssl rg tar; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required release upgrade command is unavailable: $command" >&2
        exit 2
    fi
done

if ! "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get namespace \
    >/dev/null 2>&1; then
    echo "The explicit local Kubernetes context is unavailable: $IIP_KUBE_CONTEXT" >&2
    exit 2
fi

IIP_BASE_REVISION=$(git rev-parse --verify "$IIP_UPGRADE_FROM_REVISION^{commit}")
IIP_TARGET_REVISION=$(git rev-parse HEAD)
if [ "$IIP_BASE_REVISION" = "$IIP_TARGET_REVISION" ] || \
    ! git merge-base --is-ancestor "$IIP_BASE_REVISION" "$IIP_TARGET_REVISION"; then
    echo "Upgrade source must be a strict ancestor of the target revision" >&2
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
    rm -rf "$IIP_TEST_TEMP_DIR"
}
trap cleanup EXIT INT TERM

cleanup_namespace
attempt=0
while "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get namespace \
    "$IIP_TEST_NAMESPACE" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Prior release upgrade namespace did not terminate" >&2
        exit 1
    fi
    sleep 1
done

"$IIP_TEST_PYTHON" scripts/release_bundle.py verify "$IIP_RELEASE_BUNDLE"
IIP_RELEASE_MANIFEST="$IIP_RELEASE_BUNDLE/release-manifest.json"
IIP_TARGET_VERSION=$(
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["version"])' \
        "$IIP_RELEASE_MANIFEST"
)
IIP_TARGET_CHART_VERSION=$(
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["chartVersion"])' \
        "$IIP_RELEASE_MANIFEST"
)
IIP_MANIFEST_REVISION=$(
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["revision"])' \
        "$IIP_RELEASE_MANIFEST"
)
IIP_TARGET_IMAGE_DIGEST=$(
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["spec"]["image"]["indexDigest"])' \
        "$IIP_RELEASE_MANIFEST"
)
if [ "$IIP_MANIFEST_REVISION" != "$IIP_TARGET_REVISION" ]; then
    echo "Release bundle revision does not match the checked-out upgrade gate" >&2
    exit 1
fi

IIP_BASE_ROOT="$IIP_TEST_TEMP_DIR/base"
mkdir "$IIP_BASE_ROOT"
git archive "$IIP_BASE_REVISION" | tar -x -C "$IIP_BASE_ROOT"
IIP_BASE_VERSION=$(
    "$IIP_TEST_PYTHON" -c \
        'import sys,tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' \
        "$IIP_BASE_ROOT/pyproject.toml"
)
IIP_BASE_CHART_VERSION=$(awk '$1 == "version:" {print $2; exit}' \
    "$IIP_BASE_ROOT/deploy/helm/infra-intelligence/Chart.yaml")
if [ "$IIP_BASE_VERSION" = "$IIP_TARGET_VERSION" ]; then
    echo "Upgrade source and target must have different application versions" >&2
    exit 2
fi

IIP_BASE_MIGRATION=$(
    git ls-tree -r --name-only "$IIP_BASE_REVISION" \
        src/iip/adapters/postgres/migrations | sort | tail -n 1
)
IIP_BASE_MIGRATION=$(basename "$IIP_BASE_MIGRATION")
IIP_TARGET_MIGRATION=$(rg --files src/iip/adapters/postgres/migrations -g '*.sql' | \
    sort | tail -n 1)
IIP_TARGET_MIGRATION=$(basename "$IIP_TARGET_MIGRATION")
IIP_TARGET_MIGRATION_COUNT=$(rg --files src/iip/adapters/postgres/migrations \
    -g '*.sql' | wc -l | tr -d ' ')
if [ "$IIP_BASE_MIGRATION" = "$IIP_TARGET_MIGRATION" ]; then
    echo "Upgrade gate requires a target with a newer schema migration" >&2
    exit 2
fi

IIP_BASE_REPOSITORY=iip-upgrade-base
IIP_BASE_IMAGE="$IIP_BASE_REPOSITORY:$IIP_BASE_VERSION-$(printf '%s' "$IIP_BASE_REVISION" | cut -c1-12)"
"$IIP_DOCKER_BIN" build --provenance=false \
    --build-arg "IIP_IMAGE_VERSION=$IIP_BASE_VERSION" \
    --build-arg "IIP_IMAGE_REVISION=$IIP_BASE_REVISION" \
    --tag "$IIP_BASE_IMAGE" "$IIP_BASE_ROOT" >/dev/null
IIP_BASE_IMAGE_REFERENCE=$(
    "$IIP_DOCKER_BIN" image inspect "$IIP_BASE_IMAGE" \
        --format '{{index .RepoDigests 0}}'
)
IIP_BASE_IMAGE_DIGEST=${IIP_BASE_IMAGE_REFERENCE#*@}

IIP_TARGET_REPOSITORY=iip-release-control-plane
IIP_TARGET_IMAGE="$IIP_TARGET_REPOSITORY:$IIP_TARGET_VERSION-$(printf '%s' "$IIP_TARGET_REVISION" | cut -c1-12)"
"$IIP_DOCKER_BIN" load --input \
    "$IIP_RELEASE_BUNDLE/infra-intelligence-control-plane-$IIP_TARGET_VERSION.oci.tar" \
    >/dev/null
"$IIP_DOCKER_BIN" tag "$IIP_TARGET_IMAGE_DIGEST" "$IIP_TARGET_IMAGE"

for digest in "$IIP_BASE_IMAGE_DIGEST" "$IIP_TARGET_IMAGE_DIGEST"; do
    if ! printf '%s\n' "$digest" | rg -q '^sha256:[a-f0-9]{64}$'; then
        echo "Release upgrade image did not resolve to an immutable digest" >&2
        exit 1
    fi
done

IIP_KIND_CLUSTER=${IIP_KUBE_CONTEXT#kind-}
load_kind_image() {
    image=$1
    repository=$2
    digest=$3
    "$IIP_KIND_BIN" load docker-image "$image" --name "$IIP_KIND_CLUSTER" >/dev/null
    for node in $("$IIP_KIND_BIN" get nodes --name "$IIP_KIND_CLUSTER"); do
        "$IIP_DOCKER_BIN" exec "$node" ctr -n k8s.io images tag --force \
            "docker.io/library/$image" \
            "docker.io/library/$repository@$digest" >/dev/null
    done
}
load_kind_image "$IIP_BASE_IMAGE" "$IIP_BASE_REPOSITORY" "$IIP_BASE_IMAGE_DIGEST"
load_kind_image "$IIP_TARGET_IMAGE" "$IIP_TARGET_REPOSITORY" "$IIP_TARGET_IMAGE_DIGEST"

IIP_DB_PASSWORD=$(openssl rand -hex 24)
IIP_AUTH_BEARER_TOKEN=$(openssl rand -hex 32)
IIP_AUTH_VERIFIER="sha256:$(printf '%s' "$IIP_AUTH_BEARER_TOKEN" | openssl dgst -sha256 -hex | awk '{print $NF}')"
IIP_AUTH_IDENTITIES_JSON=$(printf '%s' \
    "{\"identities\":[{\"tokenSha256\":\"$IIP_AUTH_VERIFIER\",\"actorId\":\"upgrade-test-operator\",\"tenantId\":\"upgrade-test\",\"roles\":[\"developer\",\"platform-admin\"]}]}"
)

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" create namespace \
    "$IIP_TEST_NAMESPACE" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create secret generic iip-database \
    --from-literal="database-url=postgresql://iip:$IIP_DB_PASSWORD@iip-postgres:5432/iip" \
    --from-literal="password=$IIP_DB_PASSWORD" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" --namespace "$IIP_TEST_NAMESPACE" \
    create secret generic iip-auth \
    --from-literal="identities-json=$IIP_AUTH_IDENTITIES_JSON" >/dev/null

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

install_revision() {
    chart=$1
    repository=$2
    version=$3
    digest=$4
    "$IIP_HELM_BIN" upgrade --install iip "$chart" \
        --kube-context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_TEST_NAMESPACE" \
        --set "image.repository=$repository" \
        --set "image.tag=$version" \
        --set-string "image.digest=$digest" \
        --set image.pullPolicy=Never \
        --set replicaCount=2 \
        --set database.existingSecret=iip-database \
        --set database.migrations.enabled=true \
        --set auth.existingSecret=iip-auth \
        --wait --timeout 180s >/dev/null
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_TEST_NAMESPACE" rollout status \
        deployment/iip-infra-intelligence --timeout=120s >/dev/null
}

assert_runtime() {
    expected_version=$1
    expected_chart=$2
    expected_digest=$3
    expected_revision=$4
    expected_migration=$5
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); version,chart,digest,revision,migration=sys.argv[1:]; request=urllib.request.Request("http://127.0.0.1:8080/v1/system/version", headers={"Authorization":"Bearer "+token}); document=json.load(urllib.request.urlopen(request, timeout=5)); spec=document["spec"]; assert spec["application"]["version"] == version; assert spec["storage"]["requiredMigration"] == migration; assert spec["build"] == {"mode":"release","revision":revision}; assert spec["deployment"] == {"helmChartVersion":chart,"imageDigest":digest}' \
            "$expected_version" "$expected_chart" "$expected_digest" \
            "$expected_revision" "$expected_migration"
}

assert_migration() {
    expected=$1
    actual=$(
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec deployment/iip-postgres -- \
            psql -U iip -d iip -Atc 'SELECT max(version) FROM iip.schema_migrations'
    )
    if [ "$actual" != "$expected" ]; then
        echo "Release upgrade did not apply the expected schema generation" >&2
        exit 1
    fi
}

assert_seed_resource() {
    printf '%s' "$IIP_AUTH_BEARER_TOKEN" | \
        "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
            --namespace "$IIP_TEST_NAMESPACE" exec -i \
            deployment/iip-infra-intelligence -- python -c \
            'import json,sys,urllib.request; token=sys.stdin.read(); request=urllib.request.Request("http://127.0.0.1:8080/v1/resources", headers={"Authorization":"Bearer "+token}); document=json.load(urllib.request.urlopen(request, timeout=5)); matches=[item for item in document["items"] if item["spec"]["externalId"] == "cluster-upgrade/default/api"]; assert len(matches) == 1; assert matches[0]["metadata"]["tenantId"] == "upgrade-test"'
}

IIP_BASE_CHART="$IIP_BASE_ROOT/deploy/helm/infra-intelligence"
IIP_TARGET_CHART="$IIP_RELEASE_BUNDLE/infra-intelligence-$IIP_TARGET_CHART_VERSION.tgz"
install_revision "$IIP_BASE_CHART" "$IIP_BASE_REPOSITORY" \
    "$IIP_BASE_VERSION" "$IIP_BASE_IMAGE_DIGEST"
assert_runtime "$IIP_BASE_VERSION" "$IIP_BASE_CHART_VERSION" \
    "$IIP_BASE_IMAGE_DIGEST" "$IIP_BASE_REVISION" "$IIP_BASE_MIGRATION"
assert_migration "$IIP_BASE_MIGRATION"

IIP_SEED_RESOURCE=$(
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; document=json.load(open(sys.argv[1])); document["metadata"]["tenantId"]="upgrade-test"; document["spec"]["externalId"]="cluster-upgrade/default/api"; document["metadata"]["observation"]["sourceId"]="upgrade-test-source"; print(json.dumps(document, separators=(",", ":")))' \
        "$IIP_BASE_ROOT/contracts/examples/resource.json"
)
{
    printf '%s\n' "$IIP_AUTH_BEARER_TOKEN"
    printf '%s' "$IIP_SEED_RESOURCE"
} | "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" exec -i \
    deployment/iip-infra-intelligence -- python -c \
    'import json,sys,urllib.request; token=sys.stdin.readline().rstrip("\n"); payload=sys.stdin.read().encode(); request=urllib.request.Request("http://127.0.0.1:8080/v1/resources", data=payload, method="POST", headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"}); response=urllib.request.urlopen(request, timeout=5); document=json.load(response); assert response.status == 202; assert document["metadata"]["tenantId"] == "upgrade-test"; assert document["spec"]["externalId"] == "cluster-upgrade/default/api"'
assert_seed_resource

install_revision "$IIP_TARGET_CHART" "$IIP_TARGET_REPOSITORY" \
    "$IIP_TARGET_VERSION" "$IIP_TARGET_IMAGE_DIGEST"
assert_runtime "$IIP_TARGET_VERSION" "$IIP_TARGET_CHART_VERSION" \
    "$IIP_TARGET_IMAGE_DIGEST" "$IIP_TARGET_REVISION" "$IIP_TARGET_MIGRATION"
assert_migration "$IIP_TARGET_MIGRATION"
assert_seed_resource

"$IIP_HELM_BIN" rollback iip 1 \
    --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" \
    --wait --timeout 180s >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" rollout status \
    deployment/iip-infra-intelligence --timeout=120s >/dev/null
assert_runtime "$IIP_BASE_VERSION" "$IIP_BASE_CHART_VERSION" \
    "$IIP_BASE_IMAGE_DIGEST" "$IIP_BASE_REVISION" "$IIP_BASE_MIGRATION"
assert_migration "$IIP_TARGET_MIGRATION"
assert_seed_resource

install_revision "$IIP_TARGET_CHART" "$IIP_TARGET_REPOSITORY" \
    "$IIP_TARGET_VERSION" "$IIP_TARGET_IMAGE_DIGEST"
assert_runtime "$IIP_TARGET_VERSION" "$IIP_TARGET_CHART_VERSION" \
    "$IIP_TARGET_IMAGE_DIGEST" "$IIP_TARGET_REVISION" "$IIP_TARGET_MIGRATION"
assert_migration "$IIP_TARGET_MIGRATION"
assert_seed_resource

IIP_APPLIED_MIGRATION_COUNT=$(
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_TEST_NAMESPACE" exec deployment/iip-postgres -- \
        psql -U iip -d iip -Atc 'SELECT count(*) FROM iip.schema_migrations'
)
if [ "$IIP_APPLIED_MIGRATION_COUNT" != "$IIP_TARGET_MIGRATION_COUNT" ]; then
    echo "Release re-upgrade duplicated or omitted schema migrations" >&2
    exit 1
fi

"$IIP_HELM_BIN" history iip --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_TEST_NAMESPACE" --output json | \
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; rows=json.load(sys.stdin); assert len(rows) == 4; assert [row["status"] for row in rows] == ["superseded","superseded","superseded","deployed"]'

echo "Packaged N-1 upgrade passed: release identity -> preserved tenant data -> forward migration -> application rollback -> idempotent re-upgrade"
