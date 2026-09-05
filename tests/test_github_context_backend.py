from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from iip.adapters.context import ContextConfigurationError
from iip.adapters.github_context import (
    GithubContextBackendError,
    GithubContextDocumentsBackend,
    GithubContextIntegrationRegistry,
    StaticGithubCredentialBroker,
    UrllibGithubContextHttpTransport,
    build_github_context_backend_from_environment,
)
from iip.application.ports import (
    CredentialLease,
    CredentialLeaseRequest,
    ContextDocumentQuery,
)
from scripts.compatibility_tls import write_tls_material
from iip.bootstrap import _context_documents_backend_from_env


TOKEN = "github-context-reference-token-0123456789abcdef"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
RESOURCE = "res_e0ae9225a316fce4c97df5c23057b97a"


class FixedClock:
    def now(self) -> str:
        return "2026-09-06T10:00:00Z"


class SequenceClock:
    def __init__(self, values: tuple[str, ...]) -> None:
        self._values = iter(values)

    def now(self) -> str:
        return next(self._values)


def git_blob_sha(content: bytes) -> str:
    payload = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def github_response(
    content: bytes = b"# Rollout\n\npassword=customer-secret\n",
    *,
    path: str = "runbooks/API rollout.md",
) -> dict[str, object]:
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(content),
        "path": path,
        "sha": git_blob_sha(content),
        "content": base64.encodebytes(content).decode("ascii"),
        "download_url": "https://example.invalid/must-not-be-followed",
    }


def integration_document(
    *,
    endpoint: str = "https://api.github.example/api/v3",
    path: str = "runbooks/API rollout.md",
    commit: str = COMMIT,
    ca_bundle_path: str | None = None,
) -> dict[str, object]:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "github-context",
                "provider": "github",
                "endpoint": endpoint,
                "apiVersion": "2026-03-10",
                "credentialRef": "credential://local/github/context",
                "caBundlePath": ca_bundle_path,
                "requestTimeoutSeconds": 10,
                "maxResponseBytes": 2097152,
                "repositories": [
                    {
                        "owner": "platform-team",
                        "name": "operations",
                        "commitSha": commit,
                        "documents": [
                            {
                                "referenceId": "runbooks/api-rollout",
                                "resourceRefs": [RESOURCE],
                                "kind": "runbook",
                                "title": "API rollout recovery",
                                "path": path,
                            }
                        ],
                    }
                ],
            }
        ]
    }


def credentials_document() -> dict[str, object]:
    return {
        "credentials": [
            {
                "tenantId": "local",
                "integrationId": "github-context",
                "credentialRef": "credential://local/github/context",
                "bearerToken": TOKEN,
                "expiresAt": "2026-09-06T10:02:00Z",
            }
        ]
    }


def query(**overrides: object) -> ContextDocumentQuery:
    values: dict[str, object] = {
        "tenant_id": "local",
        "actor_id": "developer",
        "request_id": "ctq_4d8a2f6c1b3e5097a8c6d4e2f109753b",
        "integration_id": "github-context",
        "resource_uids": (RESOURCE,),
        "kinds": ("runbook",),
        "reference_ids": ("runbooks/api-rollout",),
        "max_documents": 8,
        "max_excerpt_chars": 4096,
        "max_bytes": 524288,
        "deadline": "2026-09-06T10:01:00Z",
    }
    values.update(overrides)
    return ContextDocumentQuery(**values)  # type: ignore[arg-type]


class RecordingCredentialBroker:
    def __init__(self, lease: CredentialLease | None = None) -> None:
        self.lease = lease or CredentialLease(
            "bearer", TOKEN, "2026-09-06T10:02:00Z"
        )
        self.requests: list[CredentialLeaseRequest] = []

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        self.requests.append(request)
        return self.lease


class RecordingTransport:
    def __init__(self, response: object | None = None) -> None:
        self.response = response if response is not None else github_response()
        self.requests: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "caBundlePath": ca_bundle_path,
                "timeoutSeconds": timeout_seconds,
                "maxResponseBytes": max_response_bytes,
            }
        )
        return json.dumps(self.response).encode("utf-8")


def backend(
    *,
    integration: dict[str, object] | None = None,
    transport: RecordingTransport | None = None,
    credentials: RecordingCredentialBroker | None = None,
) -> tuple[
    GithubContextDocumentsBackend,
    RecordingTransport,
    RecordingCredentialBroker,
]:
    selected_transport = transport or RecordingTransport()
    selected_credentials = credentials or RecordingCredentialBroker()
    return (
        GithubContextDocumentsBackend(
            GithubContextIntegrationRegistry.from_json(
                json.dumps(integration or integration_document())
            ),
            selected_credentials,
            FixedClock(),
            selected_transport,
        ),
        selected_transport,
        selected_credentials,
    )


class GithubContextConfigurationTests(unittest.TestCase):
    def test_registry_is_closed_https_only_and_commit_pinned(self) -> None:
        invalid: list[dict[str, object]] = []
        for endpoint in (
            "http://api.github.example",
            "https://user:secret@api.github.example",
            "https://api.github.example?token=secret",
            "https://api.github.example/",
        ):
            invalid.append(integration_document(endpoint=endpoint))
        invalid.append(integration_document(commit="main"))
        invalid.append(integration_document(path="../secrets.txt"))
        inline_secret = integration_document()
        inline_secret["integrations"][0]["bearerToken"] = TOKEN  # type: ignore[index]
        invalid.append(inline_secret)
        duplicate = integration_document()
        duplicate["integrations"][0]["repositories"].append(  # type: ignore[index,union-attr]
            copy.deepcopy(duplicate["integrations"][0]["repositories"][0])  # type: ignore[index]
        )
        invalid.append(duplicate)

        for document in invalid:
            with self.subTest(document=document), self.assertRaisesRegex(
                ContextConfigurationError, "context.configuration.invalid"
            ):
                GithubContextIntegrationRegistry.from_json(json.dumps(document))

    def test_static_credentials_are_exact_scoped_expiring_and_redacted(self) -> None:
        broker = StaticGithubCredentialBroker.from_json(
            json.dumps(credentials_document())
        )
        request = CredentialLeaseRequest(
            tenant_id="local",
            actor_id="developer",
            integration_id="github-context",
            credential_ref="credential://local/github/context",
            provider="github",
            scopes=("repository:contents:read",),
            deadline="2026-09-06T10:01:00Z",
        )
        self.assertEqual(broker.resolve(request).secret, TOKEN)
        self.assertNotIn(TOKEN, repr(broker))

        for changed in (
            {"provider": "gitlab"},
            {"scopes": ("repository:write",)},
            {"tenant_id": "other"},
            {"deadline": "2026-09-06T10:03:00Z"},
        ):
            values = request.__dict__ | changed
            with self.subTest(changed=changed), self.assertRaisesRegex(
                GithubContextBackendError, "context.credential.unavailable"
            ):
                broker.resolve(CredentialLeaseRequest(**values))


class GithubContextBackendTests(unittest.TestCase):
    def test_exact_brokered_read_returns_commit_and_blob_bound_document(self) -> None:
        adapter, transport, credentials = backend()

        result = adapter.query_context(query())

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.warnings, ())
        self.assertEqual(len(result.documents), 1)
        document = result.documents[0]
        self.assertEqual(document.reference_id, "runbooks/api-rollout")
        self.assertEqual(document.resource_uids, (RESOURCE,))
        self.assertEqual(
            document.locator,
            "repo://github/platform-team/operations/runbooks/API rollout.md",
        )
        self.assertEqual(
            document.revision,
            f"git:{COMMIT}:blob:{git_blob_sha(document.content.encode('utf-8'))}",
        )
        self.assertIn("customer-secret", document.content)

        lease_request = credentials.requests[0]
        self.assertEqual(lease_request.tenant_id, "local")
        self.assertEqual(lease_request.actor_id, "developer")
        self.assertEqual(lease_request.provider, "github")
        self.assertEqual(lease_request.scopes, ("repository:contents:read",))
        self.assertEqual(lease_request.deadline, query().deadline)

        outbound = transport.requests[0]
        parsed = urlsplit(str(outbound["url"]))
        self.assertEqual(
            parsed.path,
            "/api/v3/repos/platform-team/operations/contents/runbooks/API%20rollout.md",
        )
        self.assertEqual(parse_qs(parsed.query), {"ref": [COMMIT]})
        self.assertEqual(
            outbound["headers"],
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {TOKEN}",
                "User-Agent": "iip-github-context-adapter/0.78.0",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        self.assertEqual(outbound["timeoutSeconds"], 10.0)
        self.assertEqual(outbound["maxResponseBytes"], 2097152)

    def test_no_match_is_honest_no_data_without_credentials_or_network(self) -> None:
        adapter, transport, credentials = backend()
        result = adapter.query_context(
            query(reference_ids=("runbooks/not-configured",))
        )

        self.assertEqual(result.status, "no-data")
        self.assertEqual(result.documents, ())
        self.assertEqual(transport.requests, [])
        self.assertEqual(credentials.requests, [])

    def test_document_and_excerpt_limits_are_explicit_before_extra_reads(self) -> None:
        configuration = integration_document()
        repository = configuration["integrations"][0]["repositories"][0]  # type: ignore[index]
        second = copy.deepcopy(repository["documents"][0])
        second["referenceId"] = "runbooks/api-second"
        second["path"] = "runbooks/second.md"
        repository["documents"].append(second)
        adapter, transport, _ = backend(integration=configuration)

        result = adapter.query_context(
            query(reference_ids=(), max_documents=1, max_excerpt_chars=8)
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.warnings, ("document-limit", "excerpt-limit"))
        self.assertEqual(len(result.documents), 1)
        self.assertEqual(len(transport.requests), 1)

    def test_untrusted_response_identity_and_bytes_fail_closed(self) -> None:
        variants: list[dict[str, object]] = []
        for field, value in (
            ("type", "dir"),
            ("encoding", "none"),
            ("path", "runbooks/other.md"),
            ("sha", "0" * 40),
            ("size", 999),
            ("content", "not base64!"),
        ):
            response = github_response()
            response[field] = value
            variants.append(response)
        variants.append(github_response(b"\xff\xfe"))

        for response in variants:
            adapter, _, _ = backend(transport=RecordingTransport(response))
            with self.subTest(response=response), self.assertRaisesRegex(
                GithubContextBackendError, "context.document.response-invalid"
            ):
                adapter.query_context(query())

    def test_invalid_or_short_credential_never_reaches_transport(self) -> None:
        for lease in (
            CredentialLease("basic", TOKEN),
            CredentialLease("bearer", "short"),
            CredentialLease("bearer", TOKEN, "2026-09-06T10:00:30Z"),
            CredentialLease("bearer", TOKEN, "not-a-time"),
        ):
            transport = RecordingTransport()
            adapter, _, _ = backend(
                transport=transport,
                credentials=RecordingCredentialBroker(lease),
            )
            with self.subTest(lease=repr(lease)), self.assertRaisesRegex(
                GithubContextBackendError, "context.credential.unavailable"
            ):
                adapter.query_context(query())
            self.assertEqual(transport.requests, [])

    def test_each_remote_read_is_bounded_by_the_remaining_deadline(self) -> None:
        configuration = integration_document()
        integration = configuration["integrations"][0]  # type: ignore[index]
        integration["requestTimeoutSeconds"] = 120  # type: ignore[index]
        repository = integration["repositories"][0]  # type: ignore[index]
        second = copy.deepcopy(repository["documents"][0])  # type: ignore[index]
        second["referenceId"] = "runbooks/api-second"
        second["path"] = "runbooks/API rollout.md"
        repository["documents"].append(second)  # type: ignore[index]
        transport = RecordingTransport()
        adapter = GithubContextDocumentsBackend(
            GithubContextIntegrationRegistry.from_json(json.dumps(configuration)),
            RecordingCredentialBroker(),
            SequenceClock(
                (
                    "2026-09-06T10:00:00Z",
                    "2026-09-06T10:00:30Z",
                    "2026-09-06T10:00:50Z",
                    "2026-09-06T10:00:50Z",
                )
            ),
            transport,
        )

        result = adapter.query_context(query(reference_ids=(), max_documents=2))

        self.assertEqual(result.status, "complete")
        self.assertEqual(
            [item["timeoutSeconds"] for item in transport.requests],
            [30.0, 10.0],
        )

    def test_environment_builder_uses_static_or_shared_broker_explicitly(self) -> None:
        environment = {
            "IIP_CONTEXT_INTEGRATIONS_JSON": json.dumps(integration_document()),
            "IIP_GITHUB_CONTEXT_CREDENTIALS_JSON": json.dumps(
                credentials_document()
            ),
        }
        static = build_github_context_backend_from_environment(
            environment, FixedClock()
        )
        static._transport = RecordingTransport()  # type: ignore[attr-defined]
        self.assertEqual(static.query_context(query()).status, "complete")

        shared_broker = RecordingCredentialBroker()
        shared = build_github_context_backend_from_environment(
            {"IIP_CONTEXT_INTEGRATIONS_JSON": environment["IIP_CONTEXT_INTEGRATIONS_JSON"]},
            FixedClock(),
            shared_broker,
        )
        shared._transport = RecordingTransport()  # type: ignore[attr-defined]
        self.assertEqual(shared.query_context(query()).status, "complete")
        self.assertEqual(len(shared_broker.requests), 1)

        with patch.dict(
            os.environ,
            {
                "IIP_CONTEXT_BACKEND": "github",
                "IIP_CONTEXT_INTEGRATIONS_JSON": environment[
                    "IIP_CONTEXT_INTEGRATIONS_JSON"
                ],
            },
            clear=True,
        ):
            composed = _context_documents_backend_from_env(shared_broker)
        self.assertIsInstance(composed, GithubContextDocumentsBackend)


class GithubContextTlsTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        write_tls_material(
            self.directory,
            common_name="github-context.fixture",
            dns_name="github-context.fixture",
        )
        self.requests: list[dict[str, object]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "version": self.headers.get("X-GitHub-Api-Version"),
                    }
                )
                if self.path.startswith("/redirect"):
                    self.send_response(302)
                    self.send_header("Location", "/leak")
                    self.end_headers()
                    return
                body = json.dumps(github_response()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            self.directory / "server.crt", self.directory / "server.key"
        )
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.endpoint = f"https://127.0.0.1:{self.server.server_port}"

    def test_real_tls_is_ca_verified_direct_and_bounded(self) -> None:
        transport = UrllibGithubContextHttpTransport()
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        with patch.dict(
            os.environ,
            {"HTTPS_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""},
            clear=False,
        ):
            body = transport.get(
                self.endpoint + "/contents",
                headers,
                ca_bundle_path=str(self.directory / "ca.crt"),
                timeout_seconds=2,
                max_response_bytes=2097152,
            )
        self.assertEqual(json.loads(body)["type"], "file")
        self.assertEqual(self.requests[0]["authorization"], f"Bearer {TOKEN}")

        with self.assertRaisesRegex(
            GithubContextBackendError, "context.document.unavailable"
        ):
            transport.get(
                self.endpoint + "/contents",
                headers,
                ca_bundle_path=None,
                timeout_seconds=2,
                max_response_bytes=2097152,
            )

    def test_redirect_is_denied_without_forwarding_authorization(self) -> None:
        transport = UrllibGithubContextHttpTransport()
        with self.assertRaisesRegex(
            GithubContextBackendError, "context.document.unavailable"
        ):
            transport.get(
                self.endpoint + "/redirect",
                {"Authorization": f"Bearer {TOKEN}"},
                ca_bundle_path=str(self.directory / "ca.crt"),
                timeout_seconds=2,
                max_response_bytes=2097152,
            )

        self.assertEqual(len(self.requests), 1)
        self.assertTrue(str(self.requests[0]["path"]).startswith("/redirect"))


if __name__ == "__main__":
    unittest.main()
