#!/bin/sh
# Disposable local-only learning profile. No Python, Kubernetes or cloud login.
set -eu
umask 077

case "${1:-}" in up|status|down) ;; *)
    echo 'Usage: sh scripts/learning.sh up|status|down' >&2; exit 2 ;;
esac
if [ "$#" -ne 1 ]; then echo 'Only one command is accepted.' >&2; exit 2; fi
IIP_LEARN_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$IIP_LEARN_ROOT"
IIP_LEARN_DOCKER=${IIP_DOCKER_BIN:-docker}
command -v "$IIP_LEARN_DOCKER" >/dev/null 2>&1 || {
    echo 'Install and start Docker Desktop, then retry.' >&2; exit 2;
}
# A remote Docker context must never receive this deliberately insecure fixture.
if [ -n "${DOCKER_HOST:-}" ] || [ -n "${DOCKER_CONTEXT:-}" ]; then
    echo 'Unset DOCKER_HOST/DOCKER_CONTEXT and select a local Docker context.' >&2; exit 2
fi
IIP_LEARN_HOST=$("$IIP_LEARN_DOCKER" context inspect --format '{{.Endpoints.docker.Host}}')
case "$IIP_LEARN_HOST" in unix:///*) ;; *)
    echo 'Learning requires a local Unix-socket Docker daemon, not a remote context.' >&2; exit 2 ;;
esac
docker_local() {
    # Do not pass ambient application settings, proxy credentials or Compose overrides.
    env -i PATH="$PATH" HOME="$HOME" "$IIP_LEARN_DOCKER" --host "$IIP_LEARN_HOST" "$@"
}
docker_local compose version >/dev/null
IIP_LEARN_DAEMON=$(docker_local info --format '{{.ID}}')
[ -n "$IIP_LEARN_DAEMON" ] || { echo 'Start Docker Desktop and retry.' >&2; exit 2; }
# Docker's atomic name reservation serializes this fixed project across all
# checkouts on the selected daemon. The helper never starts or mounts anything.
IIP_LEARN_LOCK=''
IIP_LEARN_TEMP=''
cleanup_learning() {
    if [ -n "$IIP_LEARN_TEMP" ]; then rm -f "$IIP_LEARN_TEMP"; fi
    if [ -n "$IIP_LEARN_LOCK" ]; then docker_local rm "$IIP_LEARN_LOCK" >/dev/null; fi
}
trap cleanup_learning EXIT
trap 'exit 130' HUP INT TERM
if [ "$1" != status ]; then
    if ! IIP_LEARN_LOCK=$(docker_local create --name iip-learning-lifecycle-lock \
        --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
        -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= -e FTP_PROXY= -e NO_PROXY='*' \
        -e http_proxy= -e https_proxy= -e all_proxy= -e ftp_proxy= -e no_proxy='*' \
        --label "iip.learning.checkout=$IIP_LEARN_ROOT" \
        python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a \
        python --version); then
        echo 'Another learning lifecycle operation holds the daemon lock. No project changes made.' >&2
        echo 'If an earlier command was killed, inspect iip-learning-lifecycle-lock and its checkout label before manually removing only that stale helper.' >&2
        exit 2
    fi
fi
for IIP_LEARN_DIR in .iip .iip/learning; do
    [ ! -L "$IIP_LEARN_DIR" ] || { echo 'Learning state must not be a symlink.' >&2; exit 2; }
    mkdir -p "$IIP_LEARN_DIR"
done
for IIP_LEARN_FILE in .iip/learning/fixture.env .iip/learning/daemon; do
    [ ! -L "$IIP_LEARN_FILE" ] || { echo 'Learning state must not be a symlink.' >&2; exit 2; }
done
IIP_LEARN_BINDING="$IIP_LEARN_HOST $IIP_LEARN_DAEMON"
if [ -f .iip/learning/daemon ] && [ "$(cat .iip/learning/daemon)" != "$IIP_LEARN_BINDING" ]; then
    echo 'Docker daemon differs from this learning setup; return to the original context.' >&2; exit 2
fi
compose_learning() {
    docker_local compose --project-name iip-learning \
        --env-file .iip/learning/fixture.env \
        -f deploy/docker-compose.ai-finops.yml -f deploy/docker-compose.learning.yml "$@"
}
IIP_LEARN_CONTAINERS=$(docker_local ps -aq --filter label=com.docker.compose.project=iip-learning)
# Never operate on another checkout's same-named project.
for IIP_LEARN_CONTAINER in $IIP_LEARN_CONTAINERS; do
    IIP_LEARN_OWNER=$(docker_local inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$IIP_LEARN_CONTAINER")
    if [ "$IIP_LEARN_OWNER" != "$IIP_LEARN_ROOT/deploy" ]; then
        echo 'The iip-learning project belongs to another checkout. No changes made.' >&2; exit 2
    fi
done
case "$1" in
    up)
        if [ -n "$IIP_LEARN_CONTAINERS" ]; then
            echo 'Learning containers already exist. Use learning-status, or learning-down to discard their sample data before restarting.' >&2
            exit 2
        fi
        echo 'Building the learning preview. First startup downloads images and Python dependencies.'
        docker_local build --target learning --tag iip-learning:0.84.0 .
        IIP_LEARN_TEMP=$(mktemp .iip/learning/fixture.XXXXXX)
        docker_local run --rm --network none --read-only --cap-drop ALL \
            -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= -e FTP_PROXY= -e NO_PROXY='*' \
            -e http_proxy= -e https_proxy= -e all_proxy= -e ftp_proxy= -e no_proxy='*' \
            --security-opt no-new-privileges:true iip-learning:0.84.0 \
            python scripts/learning_fixture.py configuration > "$IIP_LEARN_TEMP"
        mv "$IIP_LEARN_TEMP" .iip/learning/fixture.env
        IIP_LEARN_TEMP=''
        printf '%s\n' "$IIP_LEARN_BINDING" > .iip/learning/daemon
        compose_learning up --detach --no-build --wait --wait-timeout 180
        compose_learning run --rm --no-deps learning-tools
        printf '%s\n' \
            'IIP learning preview is ready. Synthetic data only; no provider calls were made.' \
            'Console: http://127.0.0.1:18082/console' \
            'Demo token: ai-finops-local-operator-token-0123456789abcdef' \
            'Grafana: http://127.0.0.1:13000/d/iip-ai-finops' \
            'Guide: docs/learning/first-session.md' \
            'Stop and discard sample data: make learning-down'
        ;;
    status|down)
        if [ ! -f .iip/learning/fixture.env ]; then
            echo 'No learning setup here. Start with make learning-up.' >&2; exit 2
        fi
        if [ "$1" = status ]; then compose_learning ps --all
        else
            compose_learning down --volumes
            echo 'Removed only the iip-learning containers and disposable data; source and downloaded images remain.'
        fi
        ;;
esac
