#!/usr/bin/env python3
"""Exercise and retain the read-only GitHub context profile over real local TLS."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import ssl
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "scripts"))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402
from iip.adapters.github_context import (  # noqa: E402
    GithubContextBackendError,
    GithubContextDocumentsBackend,
    GithubContextIntegrationRegistry,
)
from iip.application.ports import (  # noqa: E402
    CredentialLease,
    CredentialLeaseRequest,
    ContextDocumentQuery,
)

from compatibility_tls import write_tls_material  # noqa: E402
import validate_schemas  # noqa: E402


SCHEMA = ROOT / "contracts" / "schemas" / "github-context-compatibility-report.schema.json"
TOKEN = "github-context-compatibility-token-0123456789abcdef"
TENANT = "tenant-private-a"
INTEGRATION = "github-context"
RESOURCE = "res_" + ("a" * 32)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PATH = "runbooks/API rollout.md"
API_VERSION = "2026-03-10"
CHECK_IDS = (
    "ca-verified-tls",
    "untrusted-ca-denial",
    "bearer-authentication",
    "exact-api-version",
    "immutable-commit-ref",
    "path-response-binding",
    "credential-scope",
    "tenant-isolation",
    "redirect-denial",
    "response-size-denial",
    "outage-fail-closed",
    "service-recovery",
    "secret-minimization",
)


class FixedClock:
    def now(self) -> str:
        return "2026-09-06T10:00:00Z"


class RecordingBroker:
    def __init__(self, token: str = TOKEN) -> None:
        self._token = token
        self.requests: list[CredentialLeaseRequest] = []

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        self.requests.append(request)
        return CredentialLease("bearer", self._token, "2026-09-06T10:02:00Z")


class FixtureState:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []


def git_blob_sha(content: bytes) -> str:
    payload = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def response_document(*, wrong_path: bool = False) -> dict[str, object]:
    content = b"# API rollout\n\nVerify the immutable image before rollback.\n"
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(content),
        "path": "runbooks/wrong.md" if wrong_path else PATH,
        "sha": git_blob_sha(content),
        "content": base64.encodebytes(content).decode("ascii"),
    }


def handler_type(state: FixtureState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            components = parsed.path.split("/")
            scenario = components[1] if len(components) > 1 else ""
            state.requests.append(
                {
                    "scenario": scenario,
                    "path": parsed.path,
                    "query": parse_qs(parsed.query),
                    "authenticated": self.headers.get("Authorization")
                    == f"Bearer {TOKEN}",
                    "apiVersion": self.headers.get("X-GitHub-Api-Version"),
                }
            )
            if scenario == "redirect":
                self.send_response(302)
                self.send_header("Location", "/leak")
                self.end_headers()
                return
            if scenario == "unavailable":
                self.send_response(503)
                self.end_headers()
                return
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self.send_response(401)
                self.end_headers()
                return
            if self.headers.get("X-GitHub-Api-Version") != API_VERSION:
                self.send_response(400)
                self.end_headers()
                return
            expected_path = (
                f"/{scenario}/repos/platform-team/operations/contents/"
                "runbooks/API%20rollout.md"
            )
            if parsed.path != expected_path or parse_qs(parsed.query) != {
                "ref": [COMMIT]
            }:
                self.send_response(400)
                self.end_headers()
                return
            document = response_document(wrong_path=scenario == "wrong-response")
            if scenario == "oversized":
                document["ignoredAdditiveField"] = "x" * 4096
            body = json.dumps(document).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return Handler


def integration_document(
    endpoint: str,
    ca_bundle_path: str | None,
    *,
    max_response_bytes: int = 2_097_152,
) -> dict[str, object]:
    return {
        "integrations": [
            {
                "tenantId": TENANT,
                "integrationId": INTEGRATION,
                "provider": "github",
                "endpoint": endpoint,
                "apiVersion": API_VERSION,
                "credentialRef": "credential://tenant-private-a/github/context",
                "caBundlePath": ca_bundle_path,
                "requestTimeoutSeconds": 3,
                "maxResponseBytes": max_response_bytes,
                "repositories": [
                    {
                        "owner": "platform-team",
                        "name": "operations",
                        "commitSha": COMMIT,
                        "documents": [
                            {
                                "referenceId": "runbooks/api-rollout",
                                "resourceRefs": [RESOURCE],
                                "kind": "runbook",
                                "title": "API rollout recovery",
                                "path": PATH,
                            }
                        ],
                    }
                ],
            }
        ]
    }


def request(*, tenant_id: str = TENANT) -> ContextDocumentQuery:
    return ContextDocumentQuery(
        tenant_id=tenant_id,
        actor_id="compatibility-operator",
        request_id="ctq_" + ("a" * 32),
        integration_id=INTEGRATION,
        resource_uids=(RESOURCE,),
        kinds=("runbook",),
        reference_ids=("runbooks/api-rollout",),
        max_documents=1,
        max_excerpt_chars=4096,
        max_bytes=524288,
        deadline="2026-09-06T10:01:00Z",
    )


def backend(
    base_url: str,
    directory: Path,
    scenario: str,
    *,
    broker: RecordingBroker | None = None,
    trusted: bool = True,
    max_response_bytes: int = 2_097_152,
) -> tuple[GithubContextDocumentsBackend, RecordingBroker]:
    selected_broker = broker or RecordingBroker()
    registry = GithubContextIntegrationRegistry.from_json(
        json.dumps(
            integration_document(
                f"{base_url}/{scenario}",
                str(directory / "ca.crt") if trusted else None,
                max_response_bytes=max_response_bytes,
            )
        )
    )
    return (
        GithubContextDocumentsBackend(registry, selected_broker, FixedClock()),
        selected_broker,
    )


def expect_error(operation: object, code: str) -> None:
    try:
        operation()  # type: ignore[operator]
    except GithubContextBackendError as exc:
        if str(exc) != code:
            raise RuntimeError("github context compatibility returned wrong failure") from exc
        return
    raise RuntimeError("github context compatibility did not fail closed")


def run_profile(directory: Path) -> tuple[dict[str, str], ...]:
    write_tls_material(
        directory,
        common_name="github-context.fixture",
        dns_name="github-context.fixture",
    )
    state = FixtureState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type(state))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(directory / "server.crt", directory / "server.key")
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"https://127.0.0.1:{server.server_port}"
    try:
        direct, broker = backend(base_url, directory, "success")
        with _hostile_proxy_environment():
            result = direct.query_context(request())
        if result.status != "complete" or len(result.documents) != 1:
            raise RuntimeError("github context compatibility read was incomplete")
        document = result.documents[0]
        success_request = state.requests[-1]
        checks: list[dict[str, str]] = []
        checks.append({"id": "ca-verified-tls", "status": "passed"})

        untrusted, _ = backend(base_url, directory, "success", trusted=False)
        expect_error(
            lambda: untrusted.query_context(request()),
            "context.document.unavailable",
        )
        checks.append({"id": "untrusted-ca-denial", "status": "passed"})

        wrong_token, _ = backend(
            base_url,
            directory,
            "success",
            broker=RecordingBroker("wrong-token-0123456789abcdef"),
        )
        expect_error(
            lambda: wrong_token.query_context(request()),
            "context.document.unavailable",
        )
        checks.append({"id": "bearer-authentication", "status": "passed"})

        if success_request["apiVersion"] != API_VERSION:
            raise RuntimeError("github context API version was not exact")
        checks.append({"id": "exact-api-version", "status": "passed"})

        if success_request["query"] != {"ref": [COMMIT]}:
            raise RuntimeError("github context immutable ref was not exact")
        checks.append({"id": "immutable-commit-ref", "status": "passed"})

        wrong_response, _ = backend(base_url, directory, "wrong-response")
        expect_error(
            lambda: wrong_response.query_context(request()),
            "context.document.response-invalid",
        )
        if (
            "%20" not in str(success_request["path"])
            or document.locator
            != "repo://github/platform-team/operations/runbooks/API rollout.md"
            or document.revision
            != f"git:{COMMIT}:blob:{git_blob_sha(document.content.encode('utf-8'))}"
        ):
            raise RuntimeError("github context path or response binding was invalid")
        checks.append({"id": "path-response-binding", "status": "passed"})

        credential_request = broker.requests[0]
        if (
            credential_request.tenant_id != TENANT
            or credential_request.actor_id != "compatibility-operator"
            or credential_request.provider != "github"
            or credential_request.scopes != ("repository:contents:read",)
            or credential_request.deadline != request().deadline
        ):
            raise RuntimeError("github context credential scope was not exact")
        checks.append({"id": "credential-scope", "status": "passed"})

        prior_broker_requests = len(broker.requests)
        prior_provider_requests = len(state.requests)
        isolated = direct.query_context(request(tenant_id="tenant-other"))
        if (
            isolated.status != "no-data"
            or len(broker.requests) != prior_broker_requests
            or len(state.requests) != prior_provider_requests
        ):
            raise RuntimeError("github context tenant isolation was invalid")
        checks.append({"id": "tenant-isolation", "status": "passed"})

        redirect, _ = backend(base_url, directory, "redirect")
        prior = len(state.requests)
        expect_error(
            lambda: redirect.query_context(request()),
            "context.document.unavailable",
        )
        redirect_requests = state.requests[prior:]
        if len(redirect_requests) != 1 or redirect_requests[0]["scenario"] != "redirect":
            raise RuntimeError("github context followed a redirect")
        checks.append({"id": "redirect-denial", "status": "passed"})

        oversized, _ = backend(
            base_url,
            directory,
            "oversized",
            max_response_bytes=1024,
        )
        expect_error(
            lambda: oversized.query_context(request()),
            "context.document.response-limited",
        )
        checks.append({"id": "response-size-denial", "status": "passed"})

        unavailable, _ = backend(base_url, directory, "unavailable")
        expect_error(
            lambda: unavailable.query_context(request()),
            "context.document.unavailable",
        )
        checks.append({"id": "outage-fail-closed", "status": "passed"})

        recovered = direct.query_context(request())
        if recovered.status != "complete" or len(recovered.documents) != 1:
            raise RuntimeError("github context did not recover")
        checks.append({"id": "service-recovery", "status": "passed"})
        return tuple(checks)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _hostile_proxy_environment:
    def __enter__(self) -> None:
        self._previous = {
            key: os.environ.get(key) for key in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")
        }
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1"
        os.environ["https_proxy"] = "http://127.0.0.1:1"
        os.environ["NO_PROXY"] = ""
        os.environ["no_proxy"] = ""

    def __exit__(self, *args: object) -> None:
        del args
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def source_identity() -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    )
    return revision, dirty


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def report_id(report: Mapping[str, object]) -> str:
    metadata = dict(report["metadata"])  # type: ignore[arg-type]
    metadata.pop("id", None)
    material = {"metadata": metadata, "spec": report["spec"]}
    return "gcr_" + hashlib.sha256(canonical_bytes(material)).hexdigest()[:32]


def compatibility_report(
    *,
    revision: str,
    source_dirty: bool,
    generated_at: str,
    platform_name: str,
    python_version: str,
    checks: tuple[dict[str, str], ...],
) -> dict[str, object]:
    complete_checks = (*checks, {"id": "secret-minimization", "status": "passed"})
    passed = sum(check["status"] == "passed" for check in complete_checks)
    status = "compatible" if passed == len(CHECK_IDS) else "incompatible"
    report: dict[str, object] = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "GithubContextCompatibilityReport",
        "metadata": {
            "id": "gcr_" + ("0" * 32),
            "generatedAt": generated_at,
            "sourceRevision": revision,
            "sourceDirty": source_dirty,
        },
        "spec": {
            "status": status,
            "environment": {
                "platform": platform_name,
                "pythonVersion": python_version,
                "applicationVersion": APPLICATION_VERSION,
            },
            "profile": {
                "name": "local-github-rest-contents-v1",
                "transport": "ca-verified-https-json",
                "apiVersion": API_VERSION,
                "credentialMode": "request-scoped-bearer",
                "credentialScope": "repository:contents:read",
                "revisionMode": "immutable-commit-and-blob",
                "maximumFileBytes": 1048576,
                "proxyMode": "disabled",
                "redirectMode": "deny",
            },
            "checks": list(complete_checks),
            "summary": {
                "totalChecks": len(CHECK_IDS),
                "passedChecks": passed,
                "failedChecks": len(CHECK_IDS) - passed,
                "overallStatus": status,
            },
        },
    }
    report["metadata"]["id"] = report_id(report)  # type: ignore[index]
    encoded = canonical_bytes(report).decode("utf-8")
    for forbidden in (TOKEN, TENANT, "127.0.0.1"):
        if forbidden in encoded:
            raise RuntimeError("github context report contains protected fixture data")
    return report


def validate_report(report: object, *, require_clean: bool = False) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = validate_schemas.instance_validation_errors(
        schema, report, label="github context compatibility report"
    )
    if errors:
        raise RuntimeError("; ".join(errors))
    assert isinstance(report, dict)
    metadata = report["metadata"]
    spec = report["spec"]
    assert isinstance(metadata, dict) and isinstance(spec, dict)
    checks = spec["checks"]
    assert isinstance(checks, list)
    if tuple(check["id"] for check in checks) != CHECK_IDS:
        raise RuntimeError("github context compatibility checks are not closed")
    passed = sum(check["status"] == "passed" for check in checks)
    status = "compatible" if passed == len(CHECK_IDS) else "incompatible"
    if spec["status"] != status or spec["summary"] != {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": passed,
        "failedChecks": len(CHECK_IDS) - passed,
        "overallStatus": status,
    }:
        raise RuntimeError("github context compatibility summary is inconsistent")
    if metadata["id"] != report_id(report):
        raise RuntimeError("github context compatibility report ID is invalid")
    if require_clean:
        revision, dirty = source_identity()
        if dirty or metadata["sourceDirty"] or metadata["sourceRevision"] != revision:
            raise RuntimeError("github context compatibility source is not clean-current")


def generate(output: Path, *, require_clean: bool) -> None:
    revision, dirty = source_identity()
    if require_clean and dirty:
        raise RuntimeError("github context qualification requires a clean source")
    with tempfile.TemporaryDirectory(prefix="iip-github-context-") as temporary:
        checks = run_profile(Path(temporary))
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    system = platform.system().lower()
    machine = platform.machine() or "unknown"
    python_version = platform.python_version()
    report = compatibility_report(
        revision=revision,
        source_dirty=dirty,
        generated_at=generated_at,
        platform_name=f"{system}/{machine}",
        python_version=python_version,
        checks=checks,
    )
    validate_report(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--require-clean", action="store_true")
    arguments = parser.parse_args()
    if (arguments.output is None) == (arguments.verify is None):
        parser.error("exactly one of --output or --verify is required")
    try:
        if arguments.output is not None:
            generate(arguments.output, require_clean=arguments.require_clean)
            print(f"github context compatibility report: {arguments.output}")
        else:
            report = json.loads(arguments.verify.read_text(encoding="utf-8"))
            validate_report(report, require_clean=arguments.require_clean)
            print(f"github context compatibility verified: {arguments.verify}")
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
