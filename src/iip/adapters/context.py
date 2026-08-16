"""Protected local-file backend for allowlisted repository and runbook context."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from iip.application.ports import (
    Clock,
    ContextDocument,
    ContextDocumentQuery,
    ContextDocumentsResult,
)


_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_REFERENCE_ID = re.compile(r"[a-z][a-z0-9._/-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_KINDS = frozenset({"runbook", "source", "configuration", "service-catalog"})
_EXTENSIONS = frozenset(
    {".go", ".java", ".js", ".json", ".md", ".py", ".rs", ".ts", ".tsx", ".txt", ".yaml", ".yml"}
)
_MAX_CONTEXT_FILE_BYTES = 16_777_216


class ContextConfigurationError(RuntimeError):
    """Protected context configuration is invalid and must fail closed."""


@dataclass(frozen=True)
class FileContextDocumentConfig:
    reference_id: str
    resource_uids: tuple[str, ...]
    kind: str
    title: str
    locator: str
    relative_path: str


@dataclass(frozen=True)
class FileContextIntegrationConfig:
    tenant_id: str
    integration_id: str
    root: Path
    documents: tuple[FileContextDocumentConfig, ...]


class NoDataContextDocumentsBackend:
    """Honest default used when no repository/runbook backend is configured."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def query_context(self, request: ContextDocumentQuery) -> ContextDocumentsResult:
        del request
        return ContextDocumentsResult(self._clock.now(), "no-data", ())


class FileContextDocumentsBackend:
    """Read only explicitly configured files beneath protected integration roots."""

    def __init__(
        self,
        integrations: tuple[FileContextIntegrationConfig, ...],
        clock: Clock,
    ) -> None:
        self._clock = clock
        self._integrations = {
            (item.tenant_id, item.integration_id): item for item in integrations
        }
        if len(self._integrations) != len(integrations):
            raise ContextConfigurationError("context.configuration.invalid")

    def query_context(self, request: ContextDocumentQuery) -> ContextDocumentsResult:
        integration = self._integrations.get(
            (request.tenant_id, request.integration_id)
        )
        if integration is None:
            return ContextDocumentsResult(self._clock.now(), "no-data", ())
        requested_resources = set(request.resource_uids)
        requested_kinds = set(request.kinds)
        requested_references = set(request.reference_ids)
        documents: list[ContextDocument] = []
        warnings: set[str] = set()
        for configured in integration.documents:
            bound_resources = tuple(
                uid for uid in configured.resource_uids if uid in requested_resources
            )
            if (
                not bound_resources
                or (requested_kinds and configured.kind not in requested_kinds)
                or (
                    requested_references
                    and configured.reference_id not in requested_references
                )
            ):
                continue
            path = self._resolve_file(integration.root, configured.relative_path)
            try:
                with path.open("rb") as handle:
                    content = handle.read(_MAX_CONTEXT_FILE_BYTES + 1)
            except OSError as exc:
                raise ContextConfigurationError("context.document.unavailable") from exc
            if len(content) > _MAX_CONTEXT_FILE_BYTES:
                raise ContextConfigurationError("context.document.too-large")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                raise ContextConfigurationError("context.document.invalid-utf8") from None
            if not text:
                continue
            if len(text) > request.max_excerpt_chars:
                warnings.add("excerpt-limit")
            revision = "sha256:" + hashlib.sha256(content).hexdigest()
            documents.append(
                ContextDocument(
                    reference_id=configured.reference_id,
                    resource_uids=bound_resources,
                    kind=configured.kind,
                    title=configured.title,
                    locator=configured.locator,
                    revision=revision,
                    content=text,
                )
            )
            if len(documents) > request.max_documents:
                documents = documents[: request.max_documents]
                warnings.add("document-limit")
                break
        status = "partial" if warnings else "complete" if documents else "no-data"
        return ContextDocumentsResult(
            self._clock.now(),
            status,
            tuple(documents),
            tuple(sorted(warnings)),
        )

    @staticmethod
    def _resolve_file(root: Path, relative_path: str) -> Path:
        candidate = root / relative_path
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ContextConfigurationError("context.document.unavailable") from exc
        if (
            not resolved.is_file()
            or not resolved.is_relative_to(root)
            or resolved.suffix.lower() not in _EXTENSIONS
        ):
            raise ContextConfigurationError("context.document.outside-root")
        return resolved


def build_context_backend_from_environment(
    environment: Mapping[str, str] | None,
    clock: Clock,
) -> FileContextDocumentsBackend | None:
    """Build the protected file backend; absent configuration keeps no-data default."""

    values = os.environ if environment is None else environment
    encoded = values.get("IIP_CONTEXT_INTEGRATIONS_JSON")
    if not encoded:
        return None
    try:
        payload = json.loads(encoded)
        if set(payload) != {"integrations"} or not isinstance(
            payload["integrations"], list
        ):
            raise KeyError
        integrations = tuple(_parse_integration(item) for item in payload["integrations"])
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
        raise ContextConfigurationError("context.configuration.invalid") from None
    return FileContextDocumentsBackend(integrations, clock)


def _parse_integration(value: object) -> FileContextIntegrationConfig:
    if not isinstance(value, dict) or set(value) != {
        "tenantId",
        "integrationId",
        "root",
        "documents",
    }:
        raise ContextConfigurationError("context.configuration.invalid")
    tenant_id = value.get("tenantId")
    integration_id = value.get("integrationId")
    root_value = value.get("root")
    documents_value = value.get("documents")
    if (
        not isinstance(tenant_id, str)
        or not 1 <= len(tenant_id) <= 128
        or not isinstance(integration_id, str)
        or not _INTEGRATION_ID.fullmatch(integration_id)
        or not isinstance(root_value, str)
        or not root_value.startswith("/")
        or not isinstance(documents_value, list)
        or not 1 <= len(documents_value) <= 256
    ):
        raise ContextConfigurationError("context.configuration.invalid")
    root = Path(root_value).resolve(strict=True)
    if not root.is_dir():
        raise ContextConfigurationError("context.configuration.invalid")
    documents = tuple(_parse_document(item) for item in documents_value)
    if len({item.reference_id for item in documents}) != len(documents):
        raise ContextConfigurationError("context.configuration.invalid")
    for document in documents:
        FileContextDocumentsBackend._resolve_file(root, document.relative_path)
    return FileContextIntegrationConfig(tenant_id, integration_id, root, documents)


def _parse_document(value: object) -> FileContextDocumentConfig:
    if not isinstance(value, dict) or set(value) != {
        "referenceId",
        "resourceRefs",
        "kind",
        "title",
        "locator",
        "path",
    }:
        raise ContextConfigurationError("context.configuration.invalid")
    reference_id = value.get("referenceId")
    resource_refs = value.get("resourceRefs")
    relative_path = value.get("path")
    title = value.get("title")
    locator = value.get("locator")
    if (
        not isinstance(reference_id, str)
        or not _REFERENCE_ID.fullmatch(reference_id)
        or not isinstance(resource_refs, list)
        or not 1 <= len(resource_refs) <= 32
        or len(resource_refs) != len(set(resource_refs))
        or any(not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid) for uid in resource_refs)
        or value.get("kind") not in _KINDS
        or not isinstance(title, str)
        or not 1 <= len(title) <= 256
        or not isinstance(locator, str)
        or not 1 <= len(locator) <= 2048
        or not isinstance(relative_path, str)
        or not relative_path
        or Path(relative_path).is_absolute()
        or ".." in Path(relative_path).parts
    ):
        raise ContextConfigurationError("context.configuration.invalid")
    return FileContextDocumentConfig(
        reference_id,
        tuple(resource_refs),
        value["kind"],
        title,
        locator,
        relative_path,
    )
