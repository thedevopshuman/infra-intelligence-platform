#!/bin/sh
set -eu

IIP_RELEASE_DOCKER_BIN=${IIP_RELEASE_DOCKER_BIN:-docker}
IIP_RELEASE_HELM_BIN=${IIP_RELEASE_HELM_BIN:-helm}
IIP_RELEASE_NPM_BIN=${IIP_RELEASE_NPM_BIN:-npm}
IIP_RELEASE_PYTHON=${IIP_RELEASE_PYTHON:-python3}
IIP_RELEASE_OUTPUT_ROOT=${IIP_RELEASE_OUTPUT_ROOT:-dist}
IIP_RELEASE_PLATFORMS=${IIP_RELEASE_PLATFORMS:-linux/amd64,linux/arm64}
IIP_RELEASE_SBOM_GENERATOR=${IIP_RELEASE_SBOM_GENERATOR:-docker.io/docker/buildkit-syft-scanner@sha256:79e7b013cbec16bbb436f312819a49a4a57752b2270c1a9332ae1a10fcc82a68}

for command in "$IIP_RELEASE_DOCKER_BIN" "$IIP_RELEASE_HELM_BIN" \
    "$IIP_RELEASE_NPM_BIN" "$IIP_RELEASE_PYTHON" git; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required release command is unavailable: $command" >&2
        exit 2
    fi
done

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "Release bundles must be built from a clean committed worktree" >&2
    exit 2
fi

IIP_RELEASE_VERSION=$(
    "$IIP_RELEASE_PYTHON" -c \
        'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
)
IIP_RELEASE_PYTHON_SDK_VERSION=$(
    "$IIP_RELEASE_PYTHON" -c \
        'import tomllib; print(tomllib.load(open("sdks/python/pyproject.toml", "rb"))["project"]["version"])'
)
IIP_RELEASE_TYPESCRIPT_SDK_VERSION=$(
    "$IIP_RELEASE_PYTHON" -c \
        'import json; print(json.load(open("sdks/typescript/package.json"))["version"])'
)
IIP_RELEASE_CHART_VERSION=$(awk '$1 == "version:" {print $2; exit}' deploy/helm/infra-intelligence/Chart.yaml)
IIP_RELEASE_CHART_APP_VERSION=$(awk '$1 == "appVersion:" {gsub(/"/, "", $2); print $2; exit}' deploy/helm/infra-intelligence/Chart.yaml)
IIP_RELEASE_REVISION=$(git rev-parse HEAD)
IIP_RELEASE_SOURCE_DATE=$(git show -s --format=%cI HEAD)
IIP_RELEASE_SHORT_REVISION=$(printf '%s' "$IIP_RELEASE_REVISION" | cut -c1-12)

if [ "$IIP_RELEASE_VERSION" != "$IIP_RELEASE_CHART_APP_VERSION" ]; then
    echo "Chart appVersion does not match the application version" >&2
    exit 1
fi

mkdir -p "$IIP_RELEASE_OUTPUT_ROOT"
IIP_RELEASE_BUNDLE="$IIP_RELEASE_OUTPUT_ROOT/iip-$IIP_RELEASE_VERSION-$IIP_RELEASE_SHORT_REVISION"
if [ -e "$IIP_RELEASE_BUNDLE" ]; then
    echo "Release output already exists: $IIP_RELEASE_BUNDLE" >&2
    exit 2
fi
mkdir "$IIP_RELEASE_BUNDLE"
IIP_RELEASE_BUNDLE_ABSOLUTE=$(cd "$IIP_RELEASE_BUNDLE" && pwd)

git archive --format=tar.gz --prefix=infra-intelligence/ \
    --output="$IIP_RELEASE_BUNDLE_ABSOLUTE/infra-intelligence-$IIP_RELEASE_CHART_VERSION.tgz" \
    HEAD:deploy/helm/infra-intelligence
"$IIP_RELEASE_HELM_BIN" lint \
    "$IIP_RELEASE_BUNDLE_ABSOLUTE/infra-intelligence-$IIP_RELEASE_CHART_VERSION.tgz" \
    >/dev/null

git archive --format=tar.gz \
    --prefix="infra-intelligence-contracts-$IIP_RELEASE_VERSION/" \
    --output="$IIP_RELEASE_BUNDLE_ABSOLUTE/infra-intelligence-contracts-$IIP_RELEASE_VERSION.tar.gz" \
    HEAD contracts api/openapi docs/specifications
git archive --format=tar.gz \
    --prefix="infra-intelligence-sdk-$IIP_RELEASE_PYTHON_SDK_VERSION/" \
    --output="$IIP_RELEASE_BUNDLE_ABSOLUTE/infra-intelligence-sdk-$IIP_RELEASE_PYTHON_SDK_VERSION.tar.gz" \
    HEAD:sdks/python

(
    cd sdks/typescript
    "$IIP_RELEASE_NPM_BIN" ci --ignore-scripts --no-audit --no-fund >/dev/null
    "$IIP_RELEASE_NPM_BIN" run build >/dev/null
    "$IIP_RELEASE_NPM_BIN" pack \
        --pack-destination "$IIP_RELEASE_BUNDLE_ABSOLUTE" >/dev/null
)

"$IIP_RELEASE_DOCKER_BIN" buildx build \
    --platform "$IIP_RELEASE_PLATFORMS" \
    --build-arg "IIP_IMAGE_VERSION=$IIP_RELEASE_VERSION" \
    --build-arg "IIP_IMAGE_REVISION=$IIP_RELEASE_REVISION" \
    --attest "type=sbom,generator=$IIP_RELEASE_SBOM_GENERATOR" \
    --provenance=mode=max \
    --output "type=oci,dest=$IIP_RELEASE_BUNDLE_ABSOLUTE/infra-intelligence-control-plane-$IIP_RELEASE_VERSION.oci.tar" \
    .

"$IIP_RELEASE_DOCKER_BIN" buildx build \
    --platform "$IIP_RELEASE_PLATFORMS" \
    --build-arg "IIP_IMAGE_VERSION=$IIP_RELEASE_VERSION" \
    --build-arg "IIP_IMAGE_REVISION=$IIP_RELEASE_REVISION" \
    --attest "type=sbom,generator=$IIP_RELEASE_SBOM_GENERATOR" \
    --provenance=mode=max \
    --output "type=oci,dest=$IIP_RELEASE_BUNDLE_ABSOLUTE/infra-intelligence-plugin-mediation-bridge-$IIP_RELEASE_VERSION.oci.tar" \
    -f deploy/plugin-mediation-bridge/Dockerfile \
    .

"$IIP_RELEASE_PYTHON" scripts/release_bundle.py finalize \
    "$IIP_RELEASE_BUNDLE_ABSOLUTE" \
    --version "$IIP_RELEASE_VERSION" \
    --chart-version "$IIP_RELEASE_CHART_VERSION" \
    --python-sdk-version "$IIP_RELEASE_PYTHON_SDK_VERSION" \
    --typescript-sdk-version "$IIP_RELEASE_TYPESCRIPT_SDK_VERSION" \
    --revision "$IIP_RELEASE_REVISION" \
    --source-date "$IIP_RELEASE_SOURCE_DATE" \
    --platforms "$IIP_RELEASE_PLATFORMS"
"$IIP_RELEASE_PYTHON" scripts/release_bundle.py verify "$IIP_RELEASE_BUNDLE_ABSOLUTE"

echo "Unsigned release bundle created: $IIP_RELEASE_BUNDLE_ABSOLUTE"
