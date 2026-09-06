#!/bin/sh
set -eu

# This gate always owns a newly created cluster. It never accepts an existing
# context, so cleanup cannot mutate a developer or customer cluster.
IIP_DOCKER_BIN=${IIP_DOCKER_BIN:-docker}
IIP_KIND_BIN=${IIP_KIND_BIN:-kind}
IIP_KUBECTL_BIN=${IIP_KUBECTL_BIN:-kubectl}
IIP_HELM_BIN=${IIP_HELM_BIN:-helm}
IIP_TEST_PYTHON=${IIP_TEST_PYTHON:-python3}
IIP_AVAILABILITY_REPORT=${IIP_AVAILABILITY_REPORT:-dist/kubernetes-availability-qualification-report.json}
IIP_AVAILABILITY_KEEP_CLUSTER=${IIP_AVAILABILITY_KEEP_CLUSTER:-false}
IIP_AVAILABILITY_REQUIRE_CLEAN=${IIP_AVAILABILITY_REQUIRE_CLEAN:-true}
IIP_AVAILABILITY_KIND_NODE_IMAGE=${IIP_AVAILABILITY_KIND_NODE_IMAGE:-kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5}
IIP_AVAILABILITY_POSTGRES_IMAGE=${IIP_AVAILABILITY_POSTGRES_IMAGE:-postgres:18.4-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15}
IIP_AVAILABILITY_CLUSTER=${IIP_AVAILABILITY_CLUSTER:-iip-availability-$(date -u +%Y%m%d%H%M%S)-$$}
IIP_AVAILABILITY_NAMESPACE=${IIP_AVAILABILITY_NAMESPACE:-iip-availability}
IIP_AVAILABILITY_CLUSTER_CREATED=false
IIP_AVAILABILITY_TEMP_DIR=$(mktemp -d)

cleanup() {
    if [ "$IIP_AVAILABILITY_CLUSTER_CREATED" = true ] && \
       [ "$IIP_AVAILABILITY_KEEP_CLUSTER" != true ]; then
        "$IIP_KIND_BIN" delete cluster --name "$IIP_AVAILABILITY_CLUSTER" >/dev/null 2>&1 || true
    fi
    rm -rf "$IIP_AVAILABILITY_TEMP_DIR"
}
trap cleanup EXIT HUP INT TERM

if ! printf '%s\n' "$IIP_AVAILABILITY_CLUSTER" | \
    rg -q '^iip-availability-[a-z0-9][a-z0-9-]{0,44}$'; then
    echo "Availability cluster name is invalid" >&2
    exit 2
fi
if ! printf '%s\n' "$IIP_AVAILABILITY_NAMESPACE" | \
    rg -q '^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$'; then
    echo "Availability namespace is invalid" >&2
    exit 2
fi
if [ "$IIP_AVAILABILITY_KEEP_CLUSTER" != true ] && \
   [ "$IIP_AVAILABILITY_KEEP_CLUSTER" != false ]; then
    echo "IIP_AVAILABILITY_KEEP_CLUSTER must be true or false" >&2
    exit 2
fi
if [ "$IIP_AVAILABILITY_REQUIRE_CLEAN" != true ] && \
   [ "$IIP_AVAILABILITY_REQUIRE_CLEAN" != false ]; then
    echo "IIP_AVAILABILITY_REQUIRE_CLEAN must be true or false" >&2
    exit 2
fi

for command in \
    "$IIP_DOCKER_BIN" \
    "$IIP_KIND_BIN" \
    "$IIP_KUBECTL_BIN" \
    "$IIP_HELM_BIN" \
    "$IIP_TEST_PYTHON" \
    git \
    openssl \
    rg; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Required availability qualification command is unavailable" >&2
        exit 2
    }
done
"$IIP_DOCKER_BIN" info >/dev/null

if "$IIP_KIND_BIN" get clusters | rg -x "$IIP_AVAILABILITY_CLUSTER" >/dev/null; then
    echo "Refusing to reuse an existing Kind cluster" >&2
    exit 2
fi
if [ "$IIP_AVAILABILITY_REQUIRE_CLEAN" = true ] && \
   [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "Kubernetes availability qualification requires a clean source tree" >&2
    exit 2
fi

IIP_SOURCE_REVISION=$(git rev-parse HEAD)
IIP_APPLICATION_VERSION=$(
    "$IIP_TEST_PYTHON" -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' pyproject.toml
)
IIP_CHART_VERSION=$(awk '$1 == "version:" {print $2; exit}' deploy/helm/infra-intelligence/Chart.yaml)
IIP_REQUIRED_MIGRATION=$(rg --files src/iip/adapters/postgres/migrations -g '*.sql' | sort | tail -n 1)
IIP_REQUIRED_MIGRATION=$(basename "$IIP_REQUIRED_MIGRATION")

"$IIP_KIND_BIN" create cluster \
    --name "$IIP_AVAILABILITY_CLUSTER" \
    --image "$IIP_AVAILABILITY_KIND_NODE_IMAGE" \
    --config - >/dev/null <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
EOF
IIP_AVAILABILITY_CLUSTER_CREATED=true
IIP_KUBE_CONTEXT=kind-"$IIP_AVAILABILITY_CLUSTER"

IIP_IMAGE_REPOSITORY=iip-availability-control-plane
IIP_IMAGE_TAG="$IIP_APPLICATION_VERSION-$(printf '%s' "$IIP_SOURCE_REVISION" | cut -c1-12)"
IIP_IMAGE="$IIP_IMAGE_REPOSITORY:$IIP_IMAGE_TAG"
"$IIP_DOCKER_BIN" build --provenance=false \
    --build-arg "IIP_IMAGE_VERSION=$IIP_APPLICATION_VERSION" \
    --build-arg "IIP_IMAGE_REVISION=$IIP_SOURCE_REVISION" \
    --tag "$IIP_IMAGE" . >/dev/null
IIP_IMAGE_DIGEST=$("$IIP_DOCKER_BIN" image inspect "$IIP_IMAGE" --format '{{.Id}}')
if ! printf '%s\n' "$IIP_IMAGE_DIGEST" | rg -q '^sha256:[a-f0-9]{64}$'; then
    echo "Availability image did not resolve to an immutable digest" >&2
    exit 1
fi
"$IIP_KIND_BIN" load docker-image "$IIP_IMAGE" --name "$IIP_AVAILABILITY_CLUSTER" >/dev/null
for node in $("$IIP_KIND_BIN" get nodes --name "$IIP_AVAILABILITY_CLUSTER"); do
    "$IIP_DOCKER_BIN" exec "$node" ctr -n k8s.io images tag --force \
        "docker.io/library/$IIP_IMAGE" \
        "docker.io/library/$IIP_IMAGE_REPOSITORY@$IIP_IMAGE_DIGEST" >/dev/null
done

IIP_DB_PASSWORD_FILE="$IIP_AVAILABILITY_TEMP_DIR/database-password"
IIP_DB_URL_FILE="$IIP_AVAILABILITY_TEMP_DIR/database-url"
IIP_CONTROL_TOKEN_FILE="$IIP_AVAILABILITY_TEMP_DIR/control-token"
IIP_OTLP_TOKEN_FILE="$IIP_AVAILABILITY_TEMP_DIR/otlp-token"
umask 077
openssl rand -hex 24 > "$IIP_DB_PASSWORD_FILE"
openssl rand -hex 32 > "$IIP_CONTROL_TOKEN_FILE"
openssl rand -hex 32 > "$IIP_OTLP_TOKEN_FILE"
IIP_DB_PASSWORD=$(tr -d '\r\n' < "$IIP_DB_PASSWORD_FILE")
printf 'postgresql://iip:%s@iip-postgres:5432/iip' "$IIP_DB_PASSWORD" > "$IIP_DB_URL_FILE"
IIP_CONTROL_TOKEN_DIGEST=$(tr -d '\r\n' < "$IIP_CONTROL_TOKEN_FILE" | openssl dgst -sha256 -hex | awk '{print $NF}')
IIP_OTLP_TOKEN_DIGEST=$(tr -d '\r\n' < "$IIP_OTLP_TOKEN_FILE" | openssl dgst -sha256 -hex | awk '{print $NF}')
IIP_RESOURCE_UID=$(
    "$IIP_TEST_PYTHON" -c 'import hashlib; print("res_" + hashlib.sha256("availability\x1fqualification\x1fsynthetic/probe\x1fkubernetes-availability".encode()).hexdigest()[:32])'
)
IIP_AUTH_IDENTITIES_JSON=$(printf '%s' \
    "{\"identities\":[{\"tokenSha256\":\"sha256:$IIP_CONTROL_TOKEN_DIGEST\",\"actorId\":\"availability-probe\",\"tenantId\":\"availability\",\"roles\":[\"developer\"]}]}"
)
IIP_OTLP_CHANNELS_JSON=$(printf '%s' \
    "{\"channels\":[{\"channelId\":\"availability-metrics\",\"tokenSha256\":\"sha256:$IIP_OTLP_TOKEN_DIGEST\",\"tenantId\":\"availability\",\"integrationId\":\"availability-probe\",\"resourceRefs\":[\"$IIP_RESOURCE_UID\"],\"metrics\":[{\"otlpName\":\"iip.qualification.request.count\",\"metric\":\"iip.qualification.request.count\",\"unit\":\"{request}\",\"attributes\":{\"service.name\":\"service.name\"}}],\"limits\":{\"maxRequestBytes\":1048576,\"maxArtifactBytes\":1048576,\"maxSeries\":10,\"maxDataPoints\":100,\"maxAttributesPerPoint\":8,\"maxAgeSeconds\":3600,\"maxClockSkewSeconds\":30,\"maxProcessingSeconds\":10},\"handling\":{\"sensitivity\":\"internal\",\"retentionClass\":\"ephemeral\"}}]}"
)

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" create namespace \
    "$IIP_AVAILABILITY_NAMESPACE" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" create secret generic iip-database \
    --from-file="database-url=$IIP_DB_URL_FILE" \
    --from-file="password=$IIP_DB_PASSWORD_FILE" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" create secret generic iip-auth \
    --from-literal="identities-json=$IIP_AUTH_IDENTITIES_JSON" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" create secret generic iip-otlp-metrics \
    --from-literal="otlp-receiver-channels-json=$IIP_OTLP_CHANNELS_JSON" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" create secret generic iip-availability-probe \
    --from-file="control-token=$IIP_CONTROL_TOKEN_FILE" \
    --from-file="otlp-token=$IIP_OTLP_TOKEN_FILE" >/dev/null

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Service
metadata:
  name: iip-postgres
spec:
  selector:
    app: iip-postgres
  ports:
    - name: postgresql
      port: 5432
      targetPort: 5432
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: iip-postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: iip-postgres
  template:
    metadata:
      labels:
        app: iip-postgres
    spec:
      nodeSelector:
        node-role.kubernetes.io/control-plane: ""
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      containers:
        - name: postgres
          image: $IIP_AVAILABILITY_POSTGRES_IMAGE
          imagePullPolicy: IfNotPresent
          env:
            - name: POSTGRES_DB
              value: iip
            - name: POSTGRES_USER
              value: iip
            - name: POSTGRES_PASSWORD_FILE
              value: /var/run/iip-database/password
          ports:
            - containerPort: 5432
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "iip", "-d", "iip"]
            periodSeconds: 2
          volumeMounts:
            - name: password
              mountPath: /var/run/iip-database
              readOnly: true
            - name: data
              mountPath: /var/lib/postgresql
      volumes:
        - name: password
          secret:
            secretName: iip-database
            items:
              - key: password
                path: password
        - name: data
          emptyDir: {}
EOF
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" rollout status deployment/iip-postgres \
    --timeout=5m >/dev/null

"$IIP_HELM_BIN" upgrade --install iip deploy/helm/infra-intelligence \
    --kube-context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" \
    --set-string "image.repository=$IIP_IMAGE_REPOSITORY" \
    --set-string "image.digest=$IIP_IMAGE_DIGEST" \
    --set image.pullPolicy=IfNotPresent \
    --set replicaCount=2 \
    --set database.existingSecret=iip-database \
    --set database.migrations.enabled=true \
    --set auth.mode=local-hashed \
    --set auth.existingSecret=iip-auth \
    --set worker.enabled=true \
    --set worker.replicaCount=2 \
    --set 'worker.tenants[0]=availability' \
    --set worker.podDisruptionBudget.enabled=true \
    --set worker.podDisruptionBudget.minAvailable=1 \
    --set otlpReceiver.enabled=true \
    --set otlpReceiver.channelsExistingSecret=iip-otlp-metrics \
    --set otlpIngest.replicaCount=2 \
    --set otlpIngest.tls.mode=disabled \
    --set otlpIngest.podDisruptionBudget.enabled=true \
    --set otlpIngest.podDisruptionBudget.minAvailable=1 \
    --set podDisruptionBudget.enabled=true \
    --set podDisruptionBudget.minAvailable=1 \
    --set availability.topologySpread.enabled=true \
    --set availability.topologySpread.maxSkew=1 \
    --set availability.topologySpread.minDomains=2 \
    --set availability.topologySpread.topologyKey=kubernetes.io/hostname \
    --set availability.topologySpread.whenUnsatisfiable=DoNotSchedule \
    --set apiTermination.endpointDrainSeconds=1 \
    --set otlpIngest.endpointDrainSeconds=1 \
    --wait --timeout=10m >/dev/null

for deployment in \
    iip-infra-intelligence \
    iip-infra-intelligence-worker \
    iip-infra-intelligence-otlp-receiver; do
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_AVAILABILITY_NAMESPACE" rollout status \
        "deployment/$deployment" --timeout=5m >/dev/null
done

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" create configmap iip-availability-probe \
    --from-file="probe.py=scripts/kubernetes_availability_probe.py" >/dev/null
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: iip-availability-probe
  labels:
    app.kubernetes.io/name: iip-availability-probe
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  nodeSelector:
    node-role.kubernetes.io/control-plane: ""
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    fsGroup: 10001
    seccompProfile:
      type: RuntimeDefault
  initContainers:
    - name: seed
      image: $IIP_IMAGE_REPOSITORY@$IIP_IMAGE_DIGEST
      imagePullPolicy: IfNotPresent
      command: ["python", "/probe/probe.py", "seed"]
      args:
        - --api-url
        - http://iip-infra-intelligence
        - --otlp-url
        - http://iip-infra-intelligence-otlp-receiver:4318
        - --control-token-file
        - /credentials/control-token
        - --otlp-token-file
        - /credentials/otlp-token
        - --state-file
        - /state/probe-state.json
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      volumeMounts:
        - name: probe
          mountPath: /probe
          readOnly: true
        - name: credentials
          mountPath: /credentials
          readOnly: true
        - name: state
          mountPath: /state
  containers:
    - name: probe
      image: $IIP_IMAGE_REPOSITORY@$IIP_IMAGE_DIGEST
      imagePullPolicy: IfNotPresent
      command: ["sh", "-c"]
      args:
        - >-
          printf baseline > /state/phase &&
          exec python /probe/probe.py run
          --api-url http://iip-infra-intelligence
          --otlp-url http://iip-infra-intelligence-otlp-receiver:4318
          --control-token-file /credentials/control-token
          --otlp-token-file /credentials/otlp-token
          --state-file /state/probe-state.json
          --phase-file /state/phase
          --stop-file /state/stop
          --application-version $IIP_APPLICATION_VERSION
          --chart-version $IIP_CHART_VERSION
          --required-migration $IIP_REQUIRED_MIGRATION
          --source-revision $IIP_SOURCE_REVISION
          --image-digest $IIP_IMAGE_DIGEST
          --interval-milliseconds 250
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      volumeMounts:
        - name: probe
          mountPath: /probe
          readOnly: true
        - name: credentials
          mountPath: /credentials
          readOnly: true
        - name: state
          mountPath: /state
  volumes:
    - name: probe
      configMap:
        name: iip-availability-probe
        defaultMode: 0444
    - name: credentials
      secret:
        secretName: iip-availability-probe
        defaultMode: 0440
    - name: state
      emptyDir: {}
EOF
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" wait pod/iip-availability-probe \
    --for=condition=Ready --timeout=5m >/dev/null

wait_for_phase() {
    phase=$1
    minimum=$2
    attempt=0
    while [ "$attempt" -lt 240 ]; do
        result=$(
            "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
                --namespace "$IIP_AVAILABILITY_NAMESPACE" exec iip-availability-probe \
                -c probe -- python -c \
                'import json,sys; p=json.load(open("/state/probe-state.json"))["phases"][sys.argv[1]]; print(min(v["attempts"] for v in p.values()), sum(v["failures"] for v in p.values()))' \
                "$phase" 2>/dev/null || printf '0 1'
        )
        count=$(printf '%s\n' "$result" | awk '{print $1}')
        failures=$(printf '%s\n' "$result" | awk '{print $2}')
        if [ "$failures" -ne 0 ]; then
            echo "Availability probe observed a failure" >&2
            exit 1
        fi
        if [ "$count" -ge "$minimum" ]; then
            return 0
        fi
        sleep 1
        attempt=$((attempt + 1))
    done
    echo "Availability probe phase did not reach its sample floor" >&2
    exit 1
}

snapshot_objects() {
    output=$1
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_AVAILABILITY_NAMESPACE" get deployments,pods,pdb \
        --selector app.kubernetes.io/instance=iip -o json > "$output"
}

snapshot_nodes() {
    output=$1
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" get nodes -o json > "$output"
}

run_workflow_phase() {
    phase=$1
    output=$2
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_AVAILABILITY_NAMESPACE" exec iip-availability-probe \
        -c probe -- python /probe/probe.py workflow \
        --api-url http://iip-infra-intelligence \
        --control-token-file /credentials/control-token \
        --phase "$phase" \
        --timeout-seconds 60 > "$output"
}

wait_for_disruption_state() {
    attempt=0
    while [ "$attempt" -lt 240 ]; do
        values=$(
            "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
                --namespace "$IIP_AVAILABILITY_NAMESPACE" get deployment \
                iip-infra-intelligence iip-infra-intelligence-worker \
                iip-infra-intelligence-otlp-receiver -o json | \
            "$IIP_TEST_PYTHON" -c \
                'import json,sys; d=json.load(sys.stdin); print(" ".join("{}:{}".format(x.get("status",{}).get("readyReplicas",0),x.get("status",{}).get("unavailableReplicas",0)) for x in d["items"]))'
        )
        if [ "$values" = "1:1 1:1 1:1" ]; then
            return 0
        fi
        sleep 1
        attempt=$((attempt + 1))
    done
    echo "Application Deployments did not enter the expected disruption state" >&2
    exit 1
}

IIP_BASELINE_NODES="$IIP_AVAILABILITY_TEMP_DIR/baseline-nodes.json"
IIP_DISRUPTION_NODES="$IIP_AVAILABILITY_TEMP_DIR/disruption-nodes.json"
IIP_RECOVERY_NODES="$IIP_AVAILABILITY_TEMP_DIR/recovery-nodes.json"
IIP_BASELINE_SNAPSHOT="$IIP_AVAILABILITY_TEMP_DIR/baseline-snapshot.json"
IIP_DISRUPTION_SNAPSHOT="$IIP_AVAILABILITY_TEMP_DIR/disruption-snapshot.json"
IIP_RECOVERY_SNAPSHOT="$IIP_AVAILABILITY_TEMP_DIR/recovery-snapshot.json"
IIP_PROBE_STATE="$IIP_AVAILABILITY_TEMP_DIR/probe-state.json"
IIP_BASELINE_WORKFLOW="$IIP_AVAILABILITY_TEMP_DIR/baseline-workflow.json"
IIP_DISRUPTION_WORKFLOW="$IIP_AVAILABILITY_TEMP_DIR/disruption-workflow.json"
IIP_RECOVERY_WORKFLOW="$IIP_AVAILABILITY_TEMP_DIR/recovery-workflow.json"
IIP_WORKFLOW_STATE="$IIP_AVAILABILITY_TEMP_DIR/workflow-state.json"

wait_for_phase baseline 20
snapshot_nodes "$IIP_BASELINE_NODES"
snapshot_objects "$IIP_BASELINE_SNAPSHOT"
IIP_TARGET_NODE=$(
    "$IIP_TEST_PYTHON" -c \
        'import json,sys; d=json.load(open(sys.argv[1])); c={"control-plane-api","workflow-worker","otlp-receiver"}; by={}; [(by.setdefault(x.get("spec",{}).get("nodeName"),set()).add(x.get("metadata",{}).get("labels",{}).get("app.kubernetes.io/component"))) for x in d["items"] if x.get("kind")=="Pod" and any(y.get("type")=="Ready" and y.get("status")=="True" for y in x.get("status",{}).get("conditions",[]))]; n=sorted(k for k,v in by.items() if v==c); print(n[0] if len(n)==2 else "")' \
        "$IIP_BASELINE_SNAPSHOT"
)
if [ -z "$IIP_TARGET_NODE" ]; then
    echo "Could not select a worker containing one ready pod per component" >&2
    exit 1
fi
run_workflow_phase baseline "$IIP_BASELINE_WORKFLOW"

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" exec iip-availability-probe \
    -c probe -- \
    sh -c 'printf disruption > /state/phase'
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" drain "$IIP_TARGET_NODE" \
    --ignore-daemonsets --delete-emptydir-data --timeout=5m >/dev/null
wait_for_disruption_state
wait_for_phase disruption 20
snapshot_nodes "$IIP_DISRUPTION_NODES"
snapshot_objects "$IIP_DISRUPTION_SNAPSHOT"
run_workflow_phase disruption "$IIP_DISRUPTION_WORKFLOW"

"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" exec iip-availability-probe \
    -c probe -- \
    sh -c 'printf recovery > /state/phase'
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" uncordon "$IIP_TARGET_NODE" >/dev/null
for deployment in \
    iip-infra-intelligence \
    iip-infra-intelligence-worker \
    iip-infra-intelligence-otlp-receiver; do
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
        --namespace "$IIP_AVAILABILITY_NAMESPACE" rollout status \
        "deployment/$deployment" --timeout=5m >/dev/null
done
wait_for_phase recovery 20
snapshot_nodes "$IIP_RECOVERY_NODES"
snapshot_objects "$IIP_RECOVERY_SNAPSHOT"
run_workflow_phase recovery "$IIP_RECOVERY_WORKFLOW"
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" exec iip-availability-probe \
    -c probe -- \
    cat /state/probe-state.json > "$IIP_PROBE_STATE"
"$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" exec iip-availability-probe \
    -c probe -- \
    touch /state/stop

"$IIP_TEST_PYTHON" -c \
    'import json,sys; documents=[json.load(open(path, encoding="utf-8")) for path in sys.argv[1:]]; print(json.dumps({"phases": {item["phase"]: item for item in documents}}, separators=(",", ":"), sort_keys=True))' \
    "$IIP_BASELINE_WORKFLOW" "$IIP_DISRUPTION_WORKFLOW" \
    "$IIP_RECOVERY_WORKFLOW" > "$IIP_WORKFLOW_STATE"

IIP_KUBERNETES_VERSION=$(
    "$IIP_KUBECTL_BIN" --context "$IIP_KUBE_CONTEXT" version -o json | \
        "$IIP_TEST_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])'
)
IIP_KIND_VERSION=$("$IIP_KIND_BIN" version | awk '{print $2}')
IIP_DOCKER_VERSION=$("$IIP_DOCKER_BIN" version --format '{{.Server.Version}}')
IIP_CONTROL_PLANE_NODE=$("$IIP_KIND_BIN" get nodes --name "$IIP_AVAILABILITY_CLUSTER" | rg 'control-plane$')
IIP_CONTAINERD_VERSION=$(
    "$IIP_DOCKER_BIN" exec "$IIP_CONTROL_PLANE_NODE" containerd --version | awk '{print $3}'
)

IIP_CLEAN_ARGUMENT=
if [ "$IIP_AVAILABILITY_REQUIRE_CLEAN" = true ]; then
    IIP_CLEAN_ARGUMENT=--require-clean
fi
PYTHONPATH=src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/kubernetes_availability_qualification.py build \
    --baseline-nodes "$IIP_BASELINE_NODES" \
    --disruption-nodes "$IIP_DISRUPTION_NODES" \
    --recovery-nodes "$IIP_RECOVERY_NODES" \
    --baseline-snapshot "$IIP_BASELINE_SNAPSHOT" \
    --disruption-snapshot "$IIP_DISRUPTION_SNAPSHOT" \
    --recovery-snapshot "$IIP_RECOVERY_SNAPSHOT" \
    --probe-state "$IIP_PROBE_STATE" \
    --workflow-state "$IIP_WORKFLOW_STATE" \
    --cluster-name "$IIP_AVAILABILITY_CLUSTER" \
    --namespace "$IIP_AVAILABILITY_NAMESPACE" \
    --target-node "$IIP_TARGET_NODE" \
    --image-digest "$IIP_IMAGE_DIGEST" \
    --kubernetes-version "$IIP_KUBERNETES_VERSION" \
    --kind-version "$IIP_KIND_VERSION" \
    --docker-version "$IIP_DOCKER_VERSION" \
    --containerd-version "$IIP_CONTAINERD_VERSION" \
    --output "$IIP_AVAILABILITY_REPORT" $IIP_CLEAN_ARGUMENT

PYTHONPATH=src:sdks/python/src "$IIP_TEST_PYTHON" \
    scripts/kubernetes_availability_qualification.py verify \
    --report "$IIP_AVAILABILITY_REPORT" $IIP_CLEAN_ARGUMENT --require-qualified

echo "Kubernetes planned-disruption availability qualification passed"
