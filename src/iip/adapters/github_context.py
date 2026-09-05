"""Read-only GitHub repository context behind the provider-neutral context port."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from iip.adapters.context import ContextConfigurationError
from iip.application.ports import (
    Clock,
    ContextDocument,
    ContextDocumentQuery,
    ContextDocumentsResult,
    CredentialBroker,
    CredentialLease,
    CredentialLeaseRequest,
)


_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_REFERENCE_ID = re.compile(r"[a-z][a-z0-9._/-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_CREDENTIAL_REF = re.compile(r"credential://[A-Za-z0-9._~:/-]{1,2020}")
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}")
_COMMIT_SHA = re.compile(r"[a-f0-9]{40}")
_BLOB_SHA = re.compile(r"[a-f0-9]{40}")
_API_VERSION = re.compile(r"20[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])")
_BASE64_CONTENT = re.compile(r"[A-Za-z0-9+/=\r\n\t ]*")
_KINDS = frozenset({"runbook", "source", "configuration", "service-catalog"})
_MAX_CONFIGURATION_BYTES = 1_048_576
_MAX_INTEGRATIONS = 128
_MAX_REPOSITORIES = 64
_MAX_DOCUMENTS = 256
_MAX_CREDENTIALS = 256
_MAX_DOCUMENT_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 2_097_152
_READ_SCOPE = ("repository:contents:read",)


class GithubContextBackendError(RuntimeError):
    """GitHub could not safely satisfy a protected context read."""


@dataclass(frozen=True)
class GithubContextDocumentConfig:
    """One logical context reference mapped to a protected repository path."""

    reference_id: str
    resource_uids: tuple[str, ...]
    kind: str
    title: str
    path: str


@dataclass(frozen=True)
class GithubContextRepositoryConfig:
    """One repository pinned to an immutable commit for every configured read."""

    owner: str
    name: str
    commit_sha: str
    documents: tuple[GithubContextDocumentConfig, ...]


@dataclass(frozen=True, repr=False)
class GithubContextIntegrationConfig:
    """Protected tenant, transport, credential, and repository binding."""

    tenant_id: str
    integration_id: str
    endpoint: str
    api_version: str
    credential_ref: str
    ca_bundle_path: str | None
    request_timeout_seconds: int
    max_response_bytes: int
    repositories: tuple[GithubContextRepositoryConfig, ...]

    def __repr__(self) -> str:
        return (
            "GithubContextIntegrationConfig(tenant_id="
            f"{self.tenant_id!r}, integration_id={self.integration_id!r}, "
            f"repositories={len(self.repositories)}, endpoint=<protected>, "
            "credential_ref=<protected>)"
        )


class GithubContextIntegrationRegistry:
    """Validated protected mappings keyed by exact tenant and integration."""

    def __init__(self, integrations: tuple[GithubContextIntegrationConfig, ...]) -> None:
        self._integrations = {
            (item.tenant_id, item.integration_id): item for item in integrations
        }
        if len(self._integrations) != len(integrations):
            raise ContextConfigurationError("context.configuration.invalid")

    @classmethod
    def from_json(cls, encoded: str) -> "GithubContextIntegrationRegistry":
        document = _json_document(encoded, "context.configuration.invalid")
        try:
            if set(document) != {"integrations"}:
                raise TypeError
            values = document["integrations"]
            if not isinstance(values, list) or not 1 <= len(values) <= _MAX_INTEGRATIONS:
                raise TypeError
            integrations = tuple(cls._integration(item) for item in values)
        except (KeyError, TypeError, ValueError):
            raise ContextConfigurationError("context.configuration.invalid") from None
        return cls(integrations)

    @classmethod
    def _integration(cls, value: object) -> GithubContextIntegrationConfig:
        if not isinstance(value, dict) or set(value) != {
            "tenantId",
            "integrationId",
            "provider",
            "endpoint",
            "apiVersion",
            "credentialRef",
            "caBundlePath",
            "requestTimeoutSeconds",
            "maxResponseBytes",
            "repositories",
        }:
            raise TypeError
        tenant_id = value["tenantId"]
        integration_id = value["integrationId"]
        endpoint = value["endpoint"]
        api_version = value["apiVersion"]
        credential_ref = value["credentialRef"]
        ca_bundle_path = value["caBundlePath"]
        request_timeout_seconds = value["requestTimeoutSeconds"]
        max_response_bytes = value["maxResponseBytes"]
        repositories_value = value["repositories"]
        if (
            not isinstance(tenant_id, str)
            or not _TENANT_ID.fullmatch(tenant_id)
            or not isinstance(integration_id, str)
            or not _INTEGRATION_ID.fullmatch(integration_id)
            or value["provider"] != "github"
            or not isinstance(api_version, str)
            or not _API_VERSION.fullmatch(api_version)
            or not isinstance(credential_ref, str)
            or not _CREDENTIAL_REF.fullmatch(credential_ref)
            or (
                ca_bundle_path is not None
                and (
                    not isinstance(ca_bundle_path, str)
                    or not ca_bundle_path.startswith("/")
                    or not 1 <= len(ca_bundle_path) <= 2048
                    or any(character.isspace() for character in ca_bundle_path)
                )
            )
            or not _integer(request_timeout_seconds, 1, 120)
            or not _integer(max_response_bytes, 1024, _MAX_RESPONSE_BYTES)
            or not isinstance(repositories_value, list)
            or not 1 <= len(repositories_value) <= _MAX_REPOSITORIES
        ):
            raise TypeError
        _validate_endpoint(endpoint)
        repositories = tuple(cls._repository(item) for item in repositories_value)
        repository_ids = {(item.owner.casefold(), item.name.casefold()) for item in repositories}
        if len(repository_ids) != len(repositories):
            raise TypeError
        references = [
            document.reference_id
            for repository in repositories
            for document in repository.documents
        ]
        if len(references) > _MAX_DOCUMENTS or len(set(references)) != len(references):
            raise TypeError
        return GithubContextIntegrationConfig(
            tenant_id=tenant_id,
            integration_id=integration_id,
            endpoint=endpoint.rstrip("/"),
            api_version=api_version,
            credential_ref=credential_ref,
            ca_bundle_path=ca_bundle_path,
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            repositories=repositories,
        )

    @classmethod
    def _repository(cls, value: object) -> GithubContextRepositoryConfig:
        if not isinstance(value, dict) or set(value) != {
            "owner",
            "name",
            "commitSha",
            "documents",
        }:
            raise TypeError
        owner = value["owner"]
        name = value["name"]
        commit_sha = value["commitSha"]
        documents_value = value["documents"]
        if (
            not isinstance(owner, str)
            or not _OWNER.fullmatch(owner)
            or owner.endswith("-")
            or not isinstance(name, str)
            or not _REPOSITORY.fullmatch(name)
            or name in (".", "..")
            or not isinstance(commit_sha, str)
            or not _COMMIT_SHA.fullmatch(commit_sha)
            or not isinstance(documents_value, list)
            or not 1 <= len(documents_value) <= _MAX_DOCUMENTS
        ):
            raise TypeError
        documents = tuple(cls._document(item) for item in documents_value)
        return GithubContextRepositoryConfig(owner, name, commit_sha, documents)

    @staticmethod
    def _document(value: object) -> GithubContextDocumentConfig:
        if not isinstance(value, dict) or set(value) != {
            "referenceId",
            "resourceRefs",
            "kind",
            "title",
            "path",
        }:
            raise TypeError
        reference_id = value["referenceId"]
        resources = value["resourceRefs"]
        kind = value["kind"]
        title = value["title"]
        path = value["path"]
        if (
            not isinstance(reference_id, str)
            or not _REFERENCE_ID.fullmatch(reference_id)
            or not isinstance(resources, list)
            or not 1 <= len(resources) <= 32
            or len(resources) != len(set(resources))
            or any(
                not isinstance(item, str) or not _RESOURCE_UID.fullmatch(item)
                for item in resources
            )
            or kind not in _KINDS
            or not _safe_text(title, 256)
            or not _valid_repository_path(path)
        ):
            raise TypeError
        return GithubContextDocumentConfig(
            reference_id,
            tuple(resources),
            kind,
            title,
            path,
        )

    def resolve(
        self, tenant_id: str, integration_id: str
    ) -> GithubContextIntegrationConfig | None:
        return self._integrations.get((tenant_id, integration_id))


class StaticGithubCredentialBroker:
    """Exact local credential mapping; production should use the shared broker."""

    def __init__(self, credentials: Mapping[tuple[str, str, str], CredentialLease]) -> None:
        self._credentials = dict(credentials)

    def __repr__(self) -> str:
        return f"StaticGithubCredentialBroker(credentials={len(self._credentials)})"

    @classmethod
    def from_json(cls, encoded: str) -> "StaticGithubCredentialBroker":
        document = _json_document(encoded, "context.credential.configuration.invalid")
        try:
            if set(document) != {"credentials"}:
                raise TypeError
            values = document["credentials"]
            if not isinstance(values, list) or len(values) > _MAX_CREDENTIALS:
                raise TypeError
            credentials: dict[tuple[str, str, str], CredentialLease] = {}
            for value in values:
                if not isinstance(value, dict) or set(value) != {
                    "tenantId",
                    "integrationId",
                    "credentialRef",
                    "bearerToken",
                    "expiresAt",
                }:
                    raise TypeError
                tenant_id = value["tenantId"]
                integration_id = value["integrationId"]
                credential_ref = value["credentialRef"]
                token = value["bearerToken"]
                expires_at = value["expiresAt"]
                if (
                    not isinstance(tenant_id, str)
                    or not _TENANT_ID.fullmatch(tenant_id)
                    or not isinstance(integration_id, str)
                    or not _INTEGRATION_ID.fullmatch(integration_id)
                    or not isinstance(credential_ref, str)
                    or not _CREDENTIAL_REF.fullmatch(credential_ref)
                    or not _safe_secret(token)
                    or (expires_at is not None and not isinstance(expires_at, str))
                ):
                    raise TypeError
                if expires_at is not None:
                    _parse_time(expires_at)
                key = (tenant_id, integration_id, credential_ref)
                if key in credentials:
                    raise TypeError
                credentials[key] = CredentialLease("bearer", token, expires_at)
        except (KeyError, TypeError, ValueError, GithubContextBackendError):
            raise ContextConfigurationError(
                "context.credential.configuration.invalid"
            ) from None
        return cls(credentials)

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        if request.provider != "github" or request.scopes != _READ_SCOPE:
            raise GithubContextBackendError("context.credential.unavailable")
        lease = self._credentials.get(
            (request.tenant_id, request.integration_id, request.credential_ref)
        )
        if lease is None or lease.scheme != "bearer" or not _safe_secret(lease.secret):
            raise GithubContextBackendError("context.credential.unavailable")
        if lease.expires_at is not None and _parse_time(lease.expires_at) < _parse_time(
            request.deadline
        ):
            raise GithubContextBackendError("context.credential.unavailable")
        return lease


class GithubContextHttpTransport(Protocol):
    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """Execute one bounded, verified, direct, no-redirect HTTPS request."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class UrllibGithubContextHttpTransport:
    """Standard-library transport with verified TLS and no ambient proxy use."""

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        try:
            context = ssl.create_default_context(cafile=ca_bundle_path)
            opener = build_opener(
                ProxyHandler({}),
                HTTPSHandler(context=context),
                _NoRedirectHandler(),
            )
            request = Request(url, headers=dict(headers), method="GET")
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise GithubContextBackendError("context.document.unavailable")
                if response.headers.get_content_type() != "application/json":
                    raise GithubContextBackendError("context.document.response-invalid")
                content = response.read(max_response_bytes + 1)
        except GithubContextBackendError:
            raise
        except HTTPError as exc:
            exc.close()
            raise GithubContextBackendError("context.document.unavailable") from None
        except Exception:
            raise GithubContextBackendError("context.document.unavailable") from None
        if len(content) > max_response_bytes:
            raise GithubContextBackendError("context.document.response-limited")
        return content


class GithubContextDocumentsBackend:
    """Read allowlisted GitHub files at one administrator-pinned commit."""

    def __init__(
        self,
        registry: GithubContextIntegrationRegistry,
        credentials: CredentialBroker,
        clock: Clock,
        transport: GithubContextHttpTransport | None = None,
    ) -> None:
        self._registry = registry
        self._credentials = credentials
        self._clock = clock
        self._transport = transport or UrllibGithubContextHttpTransport()

    def query_context(self, request: ContextDocumentQuery) -> ContextDocumentsResult:
        integration = self._registry.resolve(request.tenant_id, request.integration_id)
        if integration is None:
            return ContextDocumentsResult(self._clock.now(), "no-data", ())
        candidates = self._candidates(integration, request)
        if not candidates:
            return ContextDocumentsResult(self._clock.now(), "no-data", ())
        warnings: set[str] = set()
        if len(candidates) > request.max_documents:
            candidates = candidates[: request.max_documents]
            warnings.add("document-limit")
        self._timeout(request.deadline, integration.request_timeout_seconds)
        lease = self._lease(integration, request)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {lease.secret}",
            "User-Agent": "iip-github-context-adapter/0.77.0",
            "X-GitHub-Api-Version": integration.api_version,
        }
        documents: list[ContextDocument] = []
        decoded_bytes = 0
        for repository, configured, bound_resources in candidates:
            timeout = self._timeout(
                request.deadline, integration.request_timeout_seconds
            )
            url = self._url(integration, repository, configured)
            content = self._transport.get(
                url,
                headers,
                ca_bundle_path=integration.ca_bundle_path,
                timeout_seconds=timeout,
                max_response_bytes=integration.max_response_bytes,
            )
            text, blob_sha, byte_count = self._decode_response(
                content,
                expected_path=configured.path,
                request_max_bytes=request.max_bytes,
            )
            if decoded_bytes + byte_count > request.max_bytes:
                warnings.add("backend-partial")
                break
            decoded_bytes += byte_count
            if len(text) > request.max_excerpt_chars:
                warnings.add("excerpt-limit")
            locator = (
                "repo://github/"
                f"{repository.owner}/{repository.name}/{configured.path}"
            )
            documents.append(
                ContextDocument(
                    reference_id=configured.reference_id,
                    resource_uids=bound_resources,
                    kind=configured.kind,
                    title=configured.title,
                    locator=locator,
                    revision=(
                        f"git:{repository.commit_sha}:blob:{blob_sha}"
                    ),
                    content=text,
                )
            )
        status = "partial" if warnings else "complete" if documents else "no-data"
        return ContextDocumentsResult(
            self._clock.now(),
            status,
            tuple(documents),
            tuple(sorted(warnings)),
        )

    @staticmethod
    def _candidates(
        integration: GithubContextIntegrationConfig,
        request: ContextDocumentQuery,
    ) -> list[
        tuple[
            GithubContextRepositoryConfig,
            GithubContextDocumentConfig,
            tuple[str, ...],
        ]
    ]:
        requested_resources = set(request.resource_uids)
        requested_kinds = set(request.kinds)
        requested_references = set(request.reference_ids)
        selected = []
        for repository in integration.repositories:
            for document in repository.documents:
                bound_resources = tuple(
                    uid for uid in document.resource_uids if uid in requested_resources
                )
                if (
                    not bound_resources
                    or (requested_kinds and document.kind not in requested_kinds)
                    or (
                        requested_references
                        and document.reference_id not in requested_references
                    )
                ):
                    continue
                selected.append((repository, document, bound_resources))
        return selected

    def _lease(
        self,
        integration: GithubContextIntegrationConfig,
        request: ContextDocumentQuery,
    ) -> CredentialLease:
        try:
            lease = self._credentials.resolve(
                CredentialLeaseRequest(
                    tenant_id=request.tenant_id,
                    actor_id=request.actor_id,
                    integration_id=request.integration_id,
                    credential_ref=integration.credential_ref,
                    provider="github",
                    scopes=_READ_SCOPE,
                    deadline=request.deadline,
                )
            )
            if lease.scheme != "bearer" or not _safe_secret(lease.secret):
                raise ValueError
            if (
                lease.expires_at is not None
                and _parse_time(lease.expires_at) < _parse_time(request.deadline)
            ):
                raise ValueError
        except Exception:
            raise GithubContextBackendError("context.credential.unavailable") from None
        return lease

    def _timeout(self, deadline: str, configured_seconds: int) -> float:
        remaining = (_parse_time(deadline) - _parse_time(self._clock.now())).total_seconds()
        if remaining <= 0:
            raise GithubContextBackendError("context.deadline.exceeded")
        return min(float(configured_seconds), remaining)

    @staticmethod
    def _url(
        integration: GithubContextIntegrationConfig,
        repository: GithubContextRepositoryConfig,
        document: GithubContextDocumentConfig,
    ) -> str:
        path = quote(document.path, safe="/")
        return (
            f"{integration.endpoint}/repos/{quote(repository.owner, safe='')}"
            f"/{quote(repository.name, safe='')}/contents/{path}?"
            + urlencode({"ref": repository.commit_sha})
        )

    @staticmethod
    def _decode_response(
        content: bytes,
        *,
        expected_path: str,
        request_max_bytes: int,
    ) -> tuple[str, str, int]:
        try:
            document = json.loads(content.decode("utf-8"))
            if (
                not isinstance(document, dict)
                or document.get("type") != "file"
                or document.get("encoding") != "base64"
                or document.get("path") != expected_path
                or not isinstance(document.get("sha"), str)
                or not _BLOB_SHA.fullmatch(document["sha"])
                or not isinstance(document.get("size"), int)
                or isinstance(document["size"], bool)
                or not isinstance(document.get("content"), str)
                or not _BASE64_CONTENT.fullmatch(document["content"])
            ):
                raise TypeError
            encoded = "".join(document["content"].split())
            decoded = base64.b64decode(encoded, validate=True)
            if (
                not decoded
                or document["size"] != len(decoded)
                or len(decoded) > _MAX_DOCUMENT_BYTES
                or len(decoded) > request_max_bytes
            ):
                raise TypeError
            blob_input = b"blob " + str(len(decoded)).encode("ascii") + b"\0" + decoded
            blob_sha = hashlib.sha1(blob_input, usedforsecurity=False).hexdigest()
            if blob_sha != document["sha"]:
                raise TypeError
            text = decoded.decode("utf-8")
        except (
            binascii.Error,
            json.JSONDecodeError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ):
            raise GithubContextBackendError("context.document.response-invalid") from None
        return text, blob_sha, len(decoded)


def build_github_context_backend_from_environment(
    environment: Mapping[str, str],
    clock: Clock,
    credential_broker: CredentialBroker | None = None,
) -> GithubContextDocumentsBackend:
    """Build the GitHub adapter from protected runtime configuration."""

    integrations = environment.get("IIP_CONTEXT_INTEGRATIONS_JSON")
    if integrations is None:
        raise ContextConfigurationError("context.configuration.required")
    credentials = environment.get(
        "IIP_GITHUB_CONTEXT_CREDENTIALS_JSON", '{"credentials":[]}'
    ) or '{"credentials":[]}'
    return GithubContextDocumentsBackend(
        GithubContextIntegrationRegistry.from_json(integrations),
        credential_broker
        if credential_broker is not None
        else StaticGithubCredentialBroker.from_json(credentials),
        clock,
    )


def _json_document(encoded: object, error_code: str) -> dict[str, object]:
    if (
        not isinstance(encoded, str)
        or not 1 <= len(encoded.encode("utf-8")) <= _MAX_CONFIGURATION_BYTES
    ):
        raise ContextConfigurationError(error_code)
    try:
        document = json.loads(encoded)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ContextConfigurationError(error_code) from None
    if not isinstance(document, dict):
        raise ContextConfigurationError(error_code)
    return document


def _validate_endpoint(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise TypeError from None
    if (
        not 1 <= len(value) <= 2048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
        or any(character.isspace() for character in value)
        or "//" in parsed.path
        or parsed.path.endswith("/")
    ):
        raise TypeError


def _valid_repository_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 1024
        or value.startswith("/")
        or value.endswith("/")
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "?" in value
        or "#" in value
        or "\\" in value
        or "//" in value
    ):
        return False
    parts = PurePosixPath(value).parts
    return bool(parts and all(part not in ("", ".", "..") for part in parts))


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _safe_text(value: object, maximum: int) -> bool:
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _safe_secret(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and 16 <= len(value) <= 8192
        and not any(ord(character) < 33 or ord(character) == 127 for character in value)
    )


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise GithubContextBackendError("context.time.invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise GithubContextBackendError("context.time.invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GithubContextBackendError("context.time.invalid")
    return parsed.astimezone(timezone.utc)
