#!/usr/bin/env python3
"""Opt-in, owned-namespace Kind smoke test of an existing immutable IIP image.

This does not build images, create clusters, qualify a release, or exercise
production availability. Only a trusted local core chart may be supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
OWNER_LABEL = "iip.platform/helm-smoke-run"
POSTGRES_IMAGE = (
    "postgres:18.4-alpine@sha256:"
    "9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"
)
APP_DEPLOYMENT = "iip-infra-intelligence"
VERSION = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?"


class SmokeError(Exception):
    """Stable, non-secret diagnostic; subprocess output must never be printed."""


def run(arguments: list[str], *, stage: str, data: str | None = None,
        timeout: int = 60, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            arguments, input=data, text=True, capture_output=True,
            timeout=timeout, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SmokeError(f"helm-smoke.{stage}.unavailable-or-timeout") from None
    if result.returncode:
        raise SmokeError(f"helm-smoke.{stage}.failed")
    return result.stdout


def read_json(value: str) -> dict:
    try:
        document = json.loads(value)
    except (ValueError, TypeError):
        raise SmokeError("helm-smoke.response.invalid") from None
    if not isinstance(document, dict):
        raise SmokeError("helm-smoke.response.invalid")
    return document


def protected_file(path: Path, value: str) -> Path:
    with path.open("x", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        handle.write(value)
    return path


def validate_image(image: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"(docker\.io/thedevopshuman/iip)@(sha256:[0-9a-f]{64})", image,
    )
    if match is None:
        raise SmokeError("helm-smoke.image.explicit-iip-digest-required")
    return match.group(1), match.group(2)


def validate_context(context: str, configuration: dict) -> tuple[str, int]:
    if re.fullmatch(r"kind-[a-z0-9][a-z0-9-]{0,49}", context) is None:
        raise SmokeError("helm-smoke.context.kind-required")
    try:
        contexts = configuration["contexts"]
        clusters = configuration["clusters"]
        users = configuration["users"]
        if len(contexts) != 1 or len(clusters) != 1 or len(users) != 1:
            raise ValueError
        selected = contexts[0]
        cluster = clusters[0]["cluster"]
        user = users[0]["user"]
        if (selected["name"] != context
                or selected["context"]["cluster"] != clusters[0]["name"]
                or selected["context"]["user"] != users[0]["name"]):
            raise ValueError
        server = urlsplit(cluster["server"])
        if (server.scheme != "https" or server.username or server.password
                or server.path not in ("", "/") or server.query or server.fragment
                or not ipaddress.ip_address(server.hostname or "").is_loopback
                or not server.port or cluster.get("insecure-skip-tls-verify")
                or cluster.get("proxy-url")
                or not cluster.get("certificate-authority-data")
                or set(user) != {"client-certificate-data", "client-key-data"}
                or not all(user.values())):
            raise ValueError
        return server.hostname, server.port
    except (KeyError, TypeError, ValueError, IndexError):
        raise SmokeError("helm-smoke.context.local-certificate-kind-required") from None


def local_preflight(context: str, temporary: Path, docker_host: str | None) -> list[str]:
    # Reject unsafe names before invoking any credential-bearing command.
    if re.fullmatch(r"kind-[a-z0-9][a-z0-9-]{0,49}", context) is None:
        raise SmokeError("helm-smoke.context.kind-required")
    configuration = read_json(run(
        ["kubectl", "--context", context, "config", "view", "--minify",
         "--flatten", "--raw", "-o", "json"], stage="context",
    ))
    address, port = validate_context(context, configuration)
    if docker_host is None:
        docker_host = os.environ.get("DOCKER_HOST") or run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            stage="docker-context",
        ).strip()
    endpoint = urlsplit(docker_host)
    if (endpoint.scheme != "unix" or endpoint.netloc or not endpoint.path.startswith("/")
            or endpoint.query or endpoint.fragment):
        raise SmokeError("helm-smoke.docker.local-unix-socket-required")
    cluster_name = context.removeprefix("kind-")
    environment = dict(os.environ, DOCKER_HOST=docker_host, KIND_EXPERIMENTAL_PROVIDER="docker")
    nodes = run(["kind", "get", "nodes", "--name", cluster_name],
                stage="kind-nodes", env=environment).split()
    if not nodes or len(nodes) != len(set(nodes)) or any(
        re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", node) is None for node in nodes
    ):
        raise SmokeError("helm-smoke.kind.nodes-invalid")
    matched_api = False
    for node in nodes:
        inspection = read_json(run(
            ["docker", "--host", docker_host, "inspect", node, "--format", "{{json .}}"],
            stage="kind-container",
        ))
        labels = inspection.get("Config", {}).get("Labels", {})
        if (inspection.get("Name") != f"/{node}"
                or not inspection.get("State", {}).get("Running")
                or labels.get("io.x-k8s.kind.cluster") != cluster_name
                or labels.get("io.x-k8s.kind.role") not in ("control-plane", "worker")):
            raise SmokeError("helm-smoke.kind.container-mismatch")
        for binding in inspection.get("NetworkSettings", {}).get("Ports", {}).get("6443/tcp") or []:
            if binding.get("HostIp") == address and binding.get("HostPort") == str(port):
                matched_api = labels["io.x-k8s.kind.role"] == "control-plane"
    if not matched_api:
        raise SmokeError("helm-smoke.kind.api-port-mismatch")
    kubeconfig = protected_file(temporary / "kubeconfig.json", json.dumps(configuration))
    kubectl = ["kubectl", "--kubeconfig", str(kubeconfig), "--context", context,
               "--request-timeout=30s"]
    live = read_json(run(kubectl + ["get", "nodes", "-o", "json"], stage="nodes"))
    if {item["metadata"]["name"] for item in live.get("items", [])} != set(nodes):
        raise SmokeError("helm-smoke.kind.api-node-mismatch")
    return kubectl


def reserve_namespace(kubectl: list[str], run_id: str) -> tuple[str, str]:
    if re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
        raise SmokeError("helm-smoke.namespace.run-invalid")
    name = "iip-helm-smoke-" + run_id
    resource = {"apiVersion": "v1", "kind": "Namespace", "metadata": {
        "name": name, "labels": {OWNER_LABEL: run_id},
    }}
    # CREATE, never apply: collision cannot adopt or overwrite an existing namespace.
    result = read_json(run(kubectl + ["create", "-f", "-", "-o", "json"],
                           stage="namespace-create", data=json.dumps(resource)))
    metadata = result.get("metadata", {})
    if (metadata.get("name") != name or not metadata.get("uid")
            or metadata.get("labels", {}).get(OWNER_LABEL) != run_id):
        raise SmokeError("helm-smoke.namespace.ownership-unconfirmed")
    return name, metadata["uid"]


def cleanup_namespace(kubectl: list[str], name: str, uid: str, run_id: str) -> None:
    if name != "iip-helm-smoke-" + run_id or re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
        raise SmokeError("helm-smoke.cleanup.target-invalid")
    result = read_json(run(kubectl + ["get", "namespace", name, "-o", "json"],
                           stage="namespace-read"))
    metadata = result.get("metadata", {})
    if (not uid or metadata.get("uid") != uid
            or metadata.get("labels", {}).get(OWNER_LABEL) != run_id
            or not metadata.get("resourceVersion")):
        raise SmokeError("helm-smoke.cleanup.ownership-changed")
    # API preconditions close both replacement and label-change races after GET.
    options = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
        "uid": uid, "resourceVersion": metadata["resourceVersion"],
    }, "propagationPolicy": "Foreground"}
    run(kubectl + ["delete", "--raw", f"/api/v1/namespaces/{name}", "-f", "-"],
        stage="namespace-delete", data=json.dumps(options))


def postgres_fixture() -> dict:
    labels = {"app.kubernetes.io/name": "postgresql"}
    return {"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "iip-postgres"},
         "spec": {"replicas": 1, "selector": {"matchLabels": labels}, "template": {
             "metadata": {"labels": labels}, "spec": {
                 "automountServiceAccountToken": False,
                 "containers": [{"name": "postgres", "image": POSTGRES_IMAGE,
                     "env": [{"name": "POSTGRES_USER", "value": "iip"},
                             {"name": "POSTGRES_DB", "value": "iip"},
                             {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {
                                 "name": "iip-database", "key": "password"}}}],
                     "ports": [{"name": "postgres", "containerPort": 5432}],
                     "readinessProbe": {"exec": {"command": ["/bin/sh", "-ec",
                         'PGSSLMODE=verify-full PGSSLROOTCERT="$PGDATA/ca.crt" '
                         'PGGSSENCMODE=disable pg_isready -h 127.0.0.1 -U iip -d iip']},
                         "periodSeconds": 2},
                     "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                                   "limits": {"cpu": "500m", "memory": "256Mi"}},
                     "volumeMounts": [
                         {"name": "data", "mountPath": "/var/lib/postgresql"},
                         {"name": "tls", "mountPath": "/fixture", "readOnly": True},
                         {"name": "init", "mountPath": "/docker-entrypoint-initdb.d/initialize-postgres-tls.sh",
                          "subPath": "initialize-postgres-tls.sh", "readOnly": True}]}],
                 "volumes": [{"name": "data", "emptyDir": {}},
                             {"name": "tls", "secret": {"secretName": "iip-postgres-server-tls"}},
                             {"name": "init", "configMap": {"name": "iip-postgres-tls-init",
                                                            "defaultMode": 0o555}}],
             }}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "iip-postgres"},
         "spec": {"selector": labels, "ports": [{"port": 5432, "targetPort": "postgres"}]}},
    ]}


PROBE = '''import json, os, re, sys, urllib.request, urllib.error
import psycopg
from iip.adapters.postgres import PostgresConnectionConfiguration
p = json.load(sys.stdin)
def fetch(path, token=None):
    request = urllib.request.Request("http://127.0.0.1:8080" + path,
        headers={"Authorization": "Bearer " + token} if token else {})
    return json.load(urllib.request.urlopen(request, timeout=5))
assert fetch("/readyz") == {"status": "ok"}
assert fetch("/v1/authentication/console")["spec"] == {"mode": "local-token"}
for invalid in (None, "invalid-smoke-token"):
    try:
        fetch("/v1/system/version", invalid)
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("invalid credential accepted")
version = fetch("/v1/system/version", p["token"])
assert version["kind"] == "RuntimeVersionReport"
assert version["metadata"]["tenantId"] == "helm-smoke"
assert version["spec"]["application"]["version"] == p["appVersion"]
assert version["spec"]["deployment"] == {"helmChartVersion": p["chartVersion"], "imageDigest": p["digest"]}
assert version["spec"]["build"]["mode"] == "release"
assert re.fullmatch("[0-9a-f]{40,64}", version["spec"]["build"]["revision"])
configuration = PostgresConnectionConfiguration.from_environment(os.environ["IIP_DATABASE_URL"])
with psycopg.connect(configuration.connection_string) as connection:
    assert connection.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone() == (True,)
    if p["initialize"]:
        connection.execute("CREATE SCHEMA helm_smoke")
        connection.execute("CREATE TABLE helm_smoke.sentinel (value text NOT NULL)")
        connection.execute("INSERT INTO helm_smoke.sentinel VALUES (%s)", (p["runId"],))
    assert connection.execute("SELECT value FROM helm_smoke.sentinel").fetchall() == [(p["runId"],)]
print("ok")
'''


def run_smoke(arguments: argparse.Namespace) -> None:
    try:
        if __package__:
            from scripts.compatibility_tls import write_tls_material
        else:
            from compatibility_tls import write_tls_material
    except ImportError:
        raise SmokeError("helm-smoke.tls-fixture-dependency-unavailable") from None
    repository, digest = validate_image(arguments.image)
    if re.fullmatch(VERSION, arguments.app_version) is None:
        raise SmokeError("helm-smoke.app-version.invalid")
    chart = Path(arguments.chart).resolve()
    if not chart.is_dir() or not (chart / "Chart.yaml").is_file():
        raise SmokeError("helm-smoke.chart.local-directory-required")
    metadata = (chart / "Chart.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version: [\"']?(" + VERSION + r")[\"']?\s*$", metadata, re.MULTILINE)
    if re.search(r"^name: infra-intelligence\s*$", metadata, re.MULTILINE) is None or match is None:
        raise SmokeError("helm-smoke.chart.core-chart-required")
    chart_version = match.group(1)
    run_id = uuid.uuid4().hex
    namespace = uid = ""
    with tempfile.TemporaryDirectory(prefix="iip-helm-smoke-") as directory:
        temporary = Path(directory)
        kubectl = local_preflight(arguments.context, temporary, arguments.docker_host)
        try:
            namespace, uid = reserve_namespace(kubectl, run_id)
            print(f"Helm smoke namespace: {namespace}", flush=True)
            scoped = kubectl + ["--namespace", namespace]
            tls = temporary / "tls"
            tls.mkdir(mode=0o700)
            write_tls_material(tls, common_name="iip-postgres", dns_name="iip-postgres")
            for material in tls.iterdir():
                material.chmod(0o600)
            password, token = secrets.token_hex(24), secrets.token_hex(32)
            files = {
                "database-url": f"postgresql://iip:{password}@iip-postgres:5432/iip",
                "password": password,
                "identities-json": json.dumps({"identities": [{
                    "tokenSha256": "sha256:" + hashlib.sha256(token.encode()).hexdigest(),
                    "actorId": "helm-smoke-operator", "tenantId": "helm-smoke",
                    "roles": ["developer", "platform-admin"],
                }]}),
            }
            for name, content in files.items():
                protected_file(temporary / name, content)
            for name, mappings in (
                ("iip-database", {key: temporary / key for key in ("database-url", "password")}),
                ("iip-auth", {"identities-json": temporary / "identities-json"}),
                ("iip-database-ca", {"ca.crt": tls / "ca.crt"}),
                ("iip-postgres-server-tls", {key: tls / key for key in ("ca.crt", "server.crt", "server.key")}),
            ):
                run(scoped + ["create", "secret", "generic", name]
                    + [f"--from-file={key}={path}" for key, path in mappings.items()], stage="secret-create")
            initialization = (ROOT / "tests/fixtures/postgres_tls/initialize.sh").read_text(encoding="utf-8")
            # Preserve the shared TLS-only fixture but require its generated password.
            initialization = initialization.replace("0.0.0.0/0 trust", "0.0.0.0/0 scram-sha-256")
            initialization = initialization.replace("::/0 trust", "::/0 scram-sha-256")
            init = protected_file(temporary / "initialize.sh", initialization)
            run(scoped + ["create", "configmap", "iip-postgres-tls-init",
                          f"--from-file=initialize-postgres-tls.sh={init}"], stage="fixture-init")
            run(scoped + ["create", "-f", "-"], stage="fixture-create", data=json.dumps(postgres_fixture()))
            run(scoped + ["rollout", "status", "deployment/iip-postgres", f"--timeout={arguments.timeout}s"],
                stage="postgres-ready", timeout=arguments.timeout + 30)
            postgres_pods = read_json(run(scoped + ["get", "pods", "-l", "app.kubernetes.io/name=postgresql",
                                                   "-o", "json"], stage="postgres-identity"))
            postgres_uids = [item["metadata"]["uid"] for item in postgres_pods["items"]]
            if len(postgres_uids) != 1:
                raise SmokeError("helm-smoke.postgres.identity-invalid")
            values = protected_file(temporary / "values.json", json.dumps({
                "image": {"repository": repository, "tag": arguments.app_version,
                          "digest": digest, "pullPolicy": "IfNotPresent"},
                "database": {"existingSecret": "iip-database", "migrations": {"enabled": True},
                             "transportSecurity": {"mode": "verify-full", "caExistingSecret": "iip-database-ca"}},
                "auth": {"existingSecret": "iip-auth"},
                "networkPolicy": {"databaseEgress": {"namespaceSelector": {"kubernetes.io/metadata.name": namespace}}},
            }))
            helm = ["helm", "--kubeconfig", str(temporary / "kubeconfig.json"), "--kube-context", arguments.context]
            for initialize in (True, False):
                stage = "install" if initialize else "upgrade"
                print(f"Helm smoke {stage}: existing digest, no image build", flush=True)
                run(helm + ["upgrade", "--install", "iip", str(chart), "--namespace", namespace,
                            "--values", str(values), "--wait", f"--timeout={arguments.timeout}s"],
                    stage=stage, timeout=arguments.timeout + 30)
                deployment = read_json(run(scoped + ["get", "deployment", APP_DEPLOYMENT, "-o", "json"],
                                           stage="image-identity"))
                if deployment["spec"]["template"]["spec"]["containers"][0]["image"] != arguments.image:
                    raise SmokeError("helm-smoke.image.deployment-mismatch")
                response = run(scoped + ["exec", "-i", f"deployment/{APP_DEPLOYMENT}", "--", "python", "-c", PROBE],
                    stage=f"{stage}-probe", data=json.dumps({
                        "token": token, "appVersion": arguments.app_version, "chartVersion": chart_version,
                        "digest": digest, "runId": run_id, "initialize": initialize,
                    }))
                if response.strip() != "ok":
                    raise SmokeError(f"helm-smoke.{stage}-probe.invalid")
            after = read_json(run(scoped + ["get", "pods", "-l", "app.kubernetes.io/name=postgresql", "-o", "json"],
                                   stage="postgres-upgrade-identity"))
            if [item["metadata"]["uid"] for item in after["items"]] != postgres_uids:
                raise SmokeError("helm-smoke.postgres.replaced-during-upgrade")
            print("Helm smoke passed: install, migration/readiness, authentication, release identity, SQL TLS, same-version upgrade and retained sentinel.")
            print("Not release qualification: no worker/receiver, dependency stack, PVC recovery, version migration, HA or multi-architecture coverage.")
        finally:
            if namespace and uid:
                if arguments.keep_namespace:
                    print(f"Owned test namespace retained by request: {namespace}")
                else:
                    cleanup_namespace(kubectl, namespace, uid, run_id)
                    print(f"Owned test namespace deletion requested: {namespace}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--chart", default=str(ROOT / "deploy/helm/infra-intelligence"))
    parser.add_argument("--docker-host", help="Explicit local unix:// Docker socket; otherwise resolve current local context")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--keep-namespace", action="store_true")
    arguments = parser.parse_args()
    if not 60 <= arguments.timeout <= 600:
        parser.error("timeout must be between 60 and 600 seconds")
    try:
        run_smoke(arguments)
    except SmokeError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (KeyError, ValueError, TypeError, OSError):
        print("helm-smoke.unexpected-response-or-local-file-error", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
