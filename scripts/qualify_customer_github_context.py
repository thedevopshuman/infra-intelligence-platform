#!/usr/bin/env python3
"""Qualify one exact customer GitHub context read through the production adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "scripts"))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402
from iip.adapters.credential_broker import (  # noqa: E402
    ExternalCredentialBrokerConfiguration,
    ExternalHttpCredentialBroker,
)
from iip.adapters.github_context import (  # noqa: E402
    GithubContextBackendError,
    GithubContextDocumentsBackend,
    GithubContextHttpTransport,
    GithubContextIntegrationConfig,
    GithubContextIntegrationRegistry,
    GithubContextRepositoryConfig,
)
from iip.application.ports import (  # noqa: E402
    Clock,
    ContextDocumentQuery,
    CredentialBroker,
)
from scripts import (  # noqa: E402
    qualify_customer_credential_broker as credential_qualification,
)


API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerGithubContextQualificationProfile"
REPORT_KIND = "CustomerGithubContextQualificationReport"
QUALIFICATION = "customer-github-context-prerequisites-v1"
PROFILE_SCHEMA = (
    ROOT
    / "contracts/schemas/customer-github-context-qualification-profile.schema.json"
)
REPORT_SCHEMA = (
    ROOT
    / "contracts/schemas/customer-github-context-qualification-report.schema.json"
)
REPORT_ID = re.compile(r"^cgcq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
CHECK_IDS = (
    "profile-binding",
    "source-binding",
    "immutable-release",
    "protected-input-files",
    "integration-config-binding",
    "exact-endpoint",
    "exact-api-version",
    "qualified-credential-broker",
    "exact-read-authority",
    "workload-identity-accepted",
    "provider-credential-accepted",
    "ca-verified-tls",
    "direct-no-proxy-no-redirect",
    "immutable-commit-request",
    "document-path-binding",
    "complete-single-document",
    "expected-git-blob",
    "bounded-read-latency",
    "content-not-retained",
    "minimized-output",
)
LIMITATIONS = (
    "github-app-installation-and-credential-lifecycle-not-qualified",
    "organization-repository-and-content-governance-not-qualified",
    "rate-limit-secondary-throttling-and-sustained-load-not-qualified",
    "certificate-network-proxy-and-service-ha-not-qualified",
    "additional-repositories-documents-and-provider-apis-not-qualified",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "actorId",
        "authorization",
        "blobSha",
        "commitSha",
        "content",
        "credentialRef",
        "documentKind",
        "endpoint",
        "integrationId",
        "locator",
        "path",
        "referenceId",
        "repositoryName",
        "repositoryOwner",
        "resourceUid",
        "secret",
        "tenantId",
        "title",
        "token",
        "url",
        "workloadIdentity",
    }
)
FORBIDDEN_PROFILE_KEY_PARTS = (
    "authorization",
    "bearer",
    "cookie",
    "password",
    "privatekey",
    "secret",
    "token",
)
READ_SCOPE = ("repository:contents:read",)


class CustomerGithubContextQualificationError(RuntimeError):
    """Stable failure for unsafe or crossed customer GitHub evidence."""


class _LiveClock:
    def now(self) -> str:
        return _timestamp(datetime.now(timezone.utc))


class _CallableClock:
    def __init__(self, current: Callable[[], datetime]) -> None:
        self._current = current

    def now(self) -> str:
        return _timestamp(self._current())


def _fail(code: str) -> None:
    raise CustomerGithubContextQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("customer-github-context-qualification.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _integer(value: object, minimum: int, maximum: int, code: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _fail(code)
    return value


def _schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _validate_schema(document: Mapping[str, Any], path: Path, code: str) -> None:
    errors = sorted(
        Draft202012Validator(
            _schema(path, code), format_checker=FormatChecker()
        ).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail(code)


def _protected_bytes(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    descriptor = -1
    candidate = path.expanduser()
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or stat.S_IMODE(information.st_mode) != 0o600
            or information.st_uid != os.getuid()
            or not 1 <= information.st_size <= maximum_bytes
        ):
            _fail(code)
        payload = os.read(descriptor, maximum_bytes + 1)
        if len(payload) != information.st_size or len(payload) > maximum_bytes:
            _fail(code)
        return payload
    except CustomerGithubContextQualificationError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _regular_bytes(
    path: Path, *, maximum_bytes: int, code: str
) -> tuple[Path, bytes]:
    candidate = path.expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file():
            _fail(code)
        payload = candidate.read_bytes()
        if not payload or len(payload) > maximum_bytes:
            _fail(code)
        return candidate.resolve(), payload
    except CustomerGithubContextQualificationError:
        raise
    except OSError:
        _fail(code)


def _json_document(payload: bytes, code: str) -> Mapping[str, Any]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(document, Mapping):
        _fail(code)
    return document


def load_profile(path: Path) -> Mapping[str, Any]:
    profile = _json_document(
        _protected_bytes(
            path,
            maximum_bytes=262_144,
            code="customer-github-context-qualification.profile.invalid",
        ),
        "customer-github-context-qualification.profile.invalid",
    )
    validate_profile(profile)
    return profile


def load_integration_configuration(path: Path) -> Mapping[str, Any]:
    return _json_document(
        _protected_bytes(
            path,
            maximum_bytes=1_048_576,
            code="customer-github-context-qualification.integration.invalid",
        ),
        "customer-github-context-qualification.integration.invalid",
    )


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(
                character for character in str(key).lower() if character.isalnum()
            )
            if any(part in normalized for part in FORBIDDEN_PROFILE_KEY_PARTS):
                return True
            if _contains_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(child) for child in value)
    return False


def _valid_repository_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 1024
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or "\\" in value
        or "?" in value
        or "#" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    parts = PurePosixPath(value).parts
    return bool(parts and all(part not in ("", ".", "..") for part in parts))


def _endpoint_for_mode(value: object, mode: object, code: str) -> str:
    if not isinstance(value, str) or not isinstance(mode, str):
        _fail(code)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        _fail(code)
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
        _fail(code)
    canonical = value.rstrip("/")
    if mode == "github-cloud":
        if canonical != "https://api.github.com":
            _fail(code)
    elif mode == "github-enterprise-server":
        if parsed.hostname.casefold() == "api.github.com" or parsed.path != "/api/v3":
            _fail(code)
    else:
        _fail(code)
    return canonical


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-github-context-qualification.profile.invalid"
    _validate_schema(profile, PROFILE_SCHEMA, code)
    if (
        profile.get("apiVersion") != API_VERSION
        or profile.get("kind") != PROFILE_KIND
        or _contains_sensitive_key(profile)
    ):
        _fail(code)
    spec = _mapping(profile.get("spec"), code)
    query = _mapping(spec.get("query"), code)
    expected = _mapping(spec.get("expected"), code)
    objective = _mapping(spec.get("objective"), code)
    del query
    _endpoint_for_mode(expected.get("endpoint"), spec.get("serviceMode"), code)
    if not _valid_repository_path(expected.get("path")):
        _fail(code)
    request_timeout = _integer(
        objective.get("requestTimeoutSeconds"), 1, 30, code
    )
    maximum_response = _integer(
        objective.get("maximumResponseBytes"), 1024, 2_097_152, code
    )
    maximum_document = _integer(
        objective.get("maximumDocumentBytes"), 1, 1_048_576, code
    )
    deadline = _integer(objective.get("requestDeadlineSeconds"), 5, 300, code)
    maximum_latency = _integer(
        objective.get("maximumReadLatencyMilliseconds"), 1, 30_000, code
    )
    if (
        request_timeout > deadline
        or maximum_latency > deadline * 1000
        or ((maximum_document + 2) // 3) * 4 + 2048 > maximum_response
    ):
        _fail(code)


def _selected_configuration(
    profile: Mapping[str, Any],
    configuration: Mapping[str, Any],
    github_ca_path: Path | None,
) -> tuple[
    GithubContextIntegrationRegistry,
    GithubContextIntegrationConfig,
    GithubContextRepositoryConfig,
]:
    code = "customer-github-context-qualification.integration.crossed"
    try:
        registry = GithubContextIntegrationRegistry.from_json(
            json.dumps(configuration, separators=(",", ":"), sort_keys=True)
        )
    except Exception:
        _fail("customer-github-context-qualification.integration.invalid")
    integrations = configuration.get("integrations")
    if not isinstance(integrations, list) or len(integrations) != 1:
        _fail(code)
    spec = _mapping(profile.get("spec"), code)
    query = _mapping(spec.get("query"), code)
    expected = _mapping(spec.get("expected"), code)
    selected = registry.resolve(str(query["tenantId"]), str(query["integrationId"]))
    if selected is None or len(selected.repositories) != 1:
        _fail(code)
    repository = selected.repositories[0]
    if len(repository.documents) != 1:
        _fail(code)
    document = repository.documents[0]
    objective = _mapping(spec.get("objective"), code)
    if (
        selected.tenant_id != query.get("tenantId")
        or selected.integration_id != query.get("integrationId")
        or selected.endpoint != expected.get("endpoint")
        or selected.api_version != expected.get("apiVersion")
        or selected.credential_ref != expected.get("credentialRef")
        or selected.request_timeout_seconds != objective.get("requestTimeoutSeconds")
        or selected.max_response_bytes != objective.get("maximumResponseBytes")
        or repository.owner != expected.get("repositoryOwner")
        or repository.name != expected.get("repositoryName")
        or repository.commit_sha != expected.get("commitSha")
        or document.reference_id != query.get("referenceId")
        or document.resource_uids != (query.get("resourceUid"),)
        or document.kind != query.get("documentKind")
        or document.path != expected.get("path")
    ):
        _fail(code)
    if spec.get("serviceMode") == "github-cloud" and (
        github_ca_path is not None or selected.ca_bundle_path is not None
    ):
        _fail(code)
    if spec.get("serviceMode") == "github-enterprise-server" and (
        (github_ca_path is None) != (selected.ca_bundle_path is None)
    ):
        _fail(code)
    qualification_registry = registry
    if github_ca_path is not None and selected.ca_bundle_path != str(github_ca_path):
        rewritten = json.loads(json.dumps(configuration))
        rewritten["integrations"][0]["caBundlePath"] = str(github_ca_path)
        try:
            qualification_registry = GithubContextIntegrationRegistry.from_json(
                json.dumps(rewritten, separators=(",", ":"), sort_keys=True)
            )
        except Exception:
            _fail("customer-github-context-qualification.integration.invalid")
    return qualification_registry, selected, repository


def _broker_authority(
    profile: Mapping[str, Any], broker_profile: Mapping[str, Any]
) -> Mapping[str, Any]:
    code = "customer-github-context-qualification.credential-broker.crossed"
    spec = _mapping(profile.get("spec"), code)
    query = _mapping(spec.get("query"), code)
    expected = _mapping(spec.get("expected"), code)
    broker_spec = _mapping(broker_profile.get("spec"), code)
    cases = broker_spec.get("cases")
    if not isinstance(cases, list) or not cases:
        _fail(code)
    authority = _mapping(cases[0], code)
    if (
        authority.get("id") != "exact-authority"
        or authority.get("expectedOutcome") != "issued"
        or authority.get("tenantId") != query.get("tenantId")
        or authority.get("actorId") != query.get("actorId")
        or authority.get("integrationId") != query.get("integrationId")
        or authority.get("credentialRef") != expected.get("credentialRef")
        or authority.get("provider") != "github"
        or authority.get("scopes") != list(READ_SCOPE)
    ):
        _fail(code)
    return authority


def _source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("customer-github-context-qualification.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-github-context-qualification.source.invalid")
    return revision, dirty


def _check(identifier: str, passed: bool) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = (
            f"customer-github-context-qualification.{identifier}.failed"
        )
    return result


def _expected_summary(checks: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    passed = sum(1 for item in checks if item.get("status") == "passed")
    total = len(CHECK_IDS)
    return {
        "totalChecks": total,
        "passedChecks": passed,
        "failedChecks": total - passed,
        "overallStatus": "qualified" if passed == total else "not-qualified",
    }


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _report_identifier(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cgcq_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def build_report(
    *,
    revision: str,
    profile: Mapping[str, Any],
    integration_configuration: Mapping[str, Any],
    image_digest: str,
    github_ca_bundle_digest: str,
    credential_broker_report_digest: str,
    credential_broker_report: Mapping[str, Any],
    credential_broker_profile: Mapping[str, Any],
    started_at: datetime,
    completed_at: datetime,
    returned_document_count: int,
    read_latency_milliseconds: int,
    observed_revision: str | None,
    observations: Mapping[str, bool],
) -> dict[str, Any]:
    code = "customer-github-context-qualification.report.invalid"
    validate_profile(profile)
    if (
        REVISION.fullmatch(revision) is None
        or any(
            DIGEST.fullmatch(value) is None
            for value in (
                image_digest,
                github_ca_bundle_digest,
                credential_broker_report_digest,
            )
        )
        or returned_document_count not in (0, 1)
        or not 0 <= read_latency_milliseconds <= 300_000
    ):
        _fail(code)
    profile_spec = _mapping(profile.get("spec"), code)
    profile_metadata = _mapping(profile.get("metadata"), code)
    expected = _mapping(profile_spec.get("expected"), code)
    objective = _mapping(profile_spec.get("objective"), code)
    broker_spec = _mapping(credential_broker_report.get("spec"), code)
    broker_bindings = _mapping(broker_spec.get("bindings"), code)
    broker_profile_spec = _mapping(credential_broker_profile.get("spec"), code)
    cases = broker_profile_spec.get("cases")
    if not isinstance(cases, list):
        _fail(code)
    checks = [_check(identifier, observations.get(identifier, False)) for identifier in CHECK_IDS]
    summary = _expected_summary(checks)
    repository_binding = {
        "owner": expected["repositoryOwner"],
        "name": expected["repositoryName"],
        "commitSha": expected["commitSha"],
    }
    document_binding = {
        "referenceId": profile_spec["query"]["referenceId"],
        "resourceUid": profile_spec["query"]["resourceUid"],
        "kind": profile_spec["query"]["documentKind"],
        "path": expected["path"],
        "blobSha": expected["blobSha"],
    }
    report_spec: dict[str, Any] = {
        "status": summary["overallStatus"],
        "qualification": QUALIFICATION,
        "subject": {
            "applicationVersion": APPLICATION_VERSION,
            "contractsApiVersion": API_VERSION,
            "sourceRevision": revision,
            "imageDigest": image_digest,
        },
        "bindings": {
            "profileDigest": _digest_value(profile),
            "integrationConfigurationDigest": _digest_value(integration_configuration),
            "endpointBindingDigest": _digest_value(expected["endpoint"]),
            "repositoryBindingDigest": _digest_value(repository_binding),
            "documentBindingDigest": _digest_value(document_binding),
            "githubCaBundleDigest": github_ca_bundle_digest,
            "credentialBrokerReportDigest": credential_broker_report_digest,
            "credentialBrokerEndpointBindingDigest": broker_bindings.get(
                "endpointBindingDigest"
            ),
            "credentialBrokerProfileDigest": broker_bindings.get("profileDigest"),
            "credentialBrokerAuthoritySetDigest": broker_bindings.get(
                "authoritySetDigest"
            ),
            "credentialBrokerCaBundleDigest": broker_bindings.get("caBundleDigest"),
            "observedRevisionDigest": _digest_value(observed_revision),
        },
        "profile": {
            "name": QUALIFICATION,
            "serviceMode": profile_spec["serviceMode"],
            "provider": "github",
            "api": "rest-repository-contents",
            "credentialMode": "external-request-scoped-bearer",
            "credentialScope": READ_SCOPE[0],
            "revisionMode": "exact-commit-and-git-blob",
            "transport": "ca-verified-https-json",
            "redirectMode": "denied",
            "proxyMode": "disabled",
            "contentRetention": "none-in-qualification-report",
        },
        "objective": dict(objective),
        "measurements": {
            "profileReviewedAt": profile_metadata["reviewedAt"],
            "startedAt": _timestamp(started_at),
            "completedAt": _timestamp(completed_at),
            "selectedRepositoryCount": 1,
            "requestedDocumentCount": 1,
            "returnedDocumentCount": returned_document_count,
            "readLatencyMilliseconds": read_latency_milliseconds,
        },
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": summary,
    }
    metadata_without_id = {
        "generatedAt": _timestamp(completed_at),
        "sourceRevision": revision,
        "sourceDirty": False,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": {
            "id": _report_identifier(metadata_without_id, report_spec),
            **metadata_without_id,
        },
        "spec": report_spec,
    }
    if _has_forbidden_key(report):
        _fail("customer-github-context-qualification.report.not-minimized")
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    _validate_schema(
        report,
        REPORT_SCHEMA,
        "customer-github-context-qualification.report.schema-invalid",
    )
    code = "customer-github-context-qualification.report.invalid"
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    subject = _mapping(spec.get("subject"), code)
    bindings = _mapping(spec.get("bindings"), code)
    objective = _mapping(spec.get("objective"), code)
    measurements = _mapping(spec.get("measurements"), code)
    checks = spec.get("checks")
    if (
        not isinstance(checks, list)
        or [item.get("id") for item in checks if isinstance(item, Mapping)]
        != list(CHECK_IDS)
    ):
        _fail("customer-github-context-qualification.report.checks-invalid")
    for identifier, item in zip(CHECK_IDS, checks):
        current = _mapping(
            item, "customer-github-context-qualification.report.checks-invalid"
        )
        if current != _check(identifier, current.get("status") == "passed"):
            _fail("customer-github-context-qualification.report.checks-invalid")
    reviewed = _parse_timestamp(
        measurements.get("profileReviewedAt"),
        "customer-github-context-qualification.report.time-invalid",
    )
    started = _parse_timestamp(
        measurements.get("startedAt"),
        "customer-github-context-qualification.report.time-invalid",
    )
    completed = _parse_timestamp(
        measurements.get("completedAt"),
        "customer-github-context-qualification.report.time-invalid",
    )
    latency = _integer(
        measurements.get("readLatencyMilliseconds"), 0, 300_000, code
    )
    returned = _integer(measurements.get("returnedDocumentCount"), 0, 1, code)
    check_by_id = {
        item.get("id"): item.get("status") == "passed"
        for item in checks
        if isinstance(item, Mapping)
    }
    profile_current = (
        0 <= (started - reviewed).total_seconds()
        <= objective.get("maximumProfileAgeSeconds")
    )
    successful_read = returned == 1
    static_checks = (
        "source-binding",
        "immutable-release",
        "protected-input-files",
        "integration-config-binding",
        "exact-endpoint",
        "exact-api-version",
        "qualified-credential-broker",
        "exact-read-authority",
        "direct-no-proxy-no-redirect",
        "content-not-retained",
        "minimized-output",
    )
    dynamic_checks = (
        "workload-identity-accepted",
        "provider-credential-accepted",
        "ca-verified-tls",
        "immutable-commit-request",
        "document-path-binding",
        "complete-single-document",
        "expected-git-blob",
    )
    if (
        not reviewed <= started <= completed
        or completed
        != _parse_timestamp(
            metadata.get("generatedAt"),
            "customer-github-context-qualification.report.time-invalid",
        )
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or measurements.get("selectedRepositoryCount") != 1
        or measurements.get("requestedDocumentCount") != 1
        or spec.get("limitations") != list(LIMITATIONS)
        or _has_forbidden_key(report)
        or any(DIGEST.fullmatch(str(value)) is None for value in bindings.values())
        or check_by_id.get("profile-binding") != profile_current
        or check_by_id.get("bounded-read-latency")
        != (latency <= objective.get("maximumReadLatencyMilliseconds"))
        or any(check_by_id.get(identifier) is not True for identifier in static_checks)
        or any(
            check_by_id.get(identifier) != successful_read
            for identifier in dynamic_checks
        )
    ):
        _fail("customer-github-context-qualification.report.checks-invalid")
    summary = _expected_summary(checks)
    if spec.get("summary") != summary or spec.get("status") != summary["overallStatus"]:
        _fail("customer-github-context-qualification.report.summary-invalid")
    metadata_without_id = dict(metadata)
    identifier = metadata_without_id.pop("id", None)
    if (
        not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_identifier(metadata_without_id, spec)
    ):
        _fail("customer-github-context-qualification.report.id-invalid")


def _external_broker(
    profile: Mapping[str, Any], identity_path: Path, ca_path: Path
) -> ExternalHttpCredentialBroker:
    code = "customer-github-context-qualification.credential-broker.invalid"
    spec = _mapping(profile.get("spec"), code)
    objective = _mapping(spec.get("objective"), code)
    configuration = {
        "endpoint": spec.get("endpoint"),
        "caBundlePath": str(ca_path),
        "workloadIdentityTokenPath": str(identity_path),
        "requestTimeoutSeconds": objective.get("requestTimeoutSeconds"),
        "maxResponseBytes": objective.get("maximumResponseBytes"),
        "maxLeaseSeconds": objective.get("maximumLeaseSeconds"),
        "maxClockSkewSeconds": objective.get("maximumClockSkewSeconds"),
    }
    try:
        parsed = ExternalCredentialBrokerConfiguration.from_json(
            json.dumps(configuration)
        )
        return ExternalHttpCredentialBroker(parsed, _LiveClock())
    except Exception:
        _fail(code)


def _load_broker_prerequisite(
    *,
    report_path: Path,
    profile_path: Path,
    endpoint: str,
    ca_bundle_path: Path,
    image_digest: str,
) -> tuple[Mapping[str, Any], str, Mapping[str, Any]]:
    _, report_payload = _regular_bytes(
        report_path,
        maximum_bytes=1_048_576,
        code="customer-github-context-qualification.credential-broker-report.invalid",
    )
    report = _json_document(
        report_payload,
        "customer-github-context-qualification.credential-broker-report.invalid",
    )
    try:
        credential_qualification.verify_report(
            report_path=report_path,
            profile_path=profile_path,
            endpoint=endpoint,
            ca_bundle_path=ca_bundle_path,
            image_digest=image_digest,
            require_qualified=True,
        )
        broker_profile = credential_qualification.load_profile(profile_path)
    except credential_qualification.CustomerCredentialBrokerQualificationError:
        _fail("customer-github-context-qualification.credential-broker.not-qualified")
    _, current_payload = _regular_bytes(
        report_path,
        maximum_bytes=1_048_576,
        code="customer-github-context-qualification.credential-broker-report.invalid",
    )
    if current_payload != report_payload:
        _fail("customer-github-context-qualification.credential-broker-report.changed")
    return report, _digest_bytes(report_payload), broker_profile


def qualify(
    *,
    profile: Mapping[str, Any],
    integration_configuration: Mapping[str, Any],
    credential_broker_report_path: Path,
    credential_broker_profile_path: Path,
    credential_broker_endpoint: str,
    credential_broker_workload_identity_token_path: Path,
    credential_broker_ca_bundle_path: Path,
    github_ca_bundle_path: Path | None,
    image_digest: str,
    allow_context_observation: bool,
    broker: CredentialBroker | None = None,
    transport: GithubContextHttpTransport | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not allow_context_observation:
        _fail("customer-github-context-qualification.enable.required")
    if DIGEST.fullmatch(image_digest) is None:
        _fail("customer-github-context-qualification.image.invalid")
    validate_profile(profile)
    resolved_github_ca: Path | None = None
    if github_ca_bundle_path is not None:
        resolved_github_ca, github_ca_payload = _regular_bytes(
            github_ca_bundle_path,
            maximum_bytes=2 * 1024 * 1024,
            code="customer-github-context-qualification.github-ca.invalid",
        )
        github_ca_digest = _digest_bytes(github_ca_payload)
    else:
        github_ca_digest = _digest_value("system-trust")
    registry, selected, repository = _selected_configuration(
        profile, integration_configuration, resolved_github_ca
    )
    try:
        identity_path = credential_qualification.validate_workload_identity_file(
            credential_broker_workload_identity_token_path
        )
    except credential_qualification.CustomerCredentialBrokerQualificationError:
        _fail(
            "customer-github-context-qualification.credential-broker-identity.invalid"
        )
    broker_ca_path, _ = _regular_bytes(
        credential_broker_ca_bundle_path,
        maximum_bytes=2 * 1024 * 1024,
        code="customer-github-context-qualification.credential-broker-ca.invalid",
    )
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-github-context-qualification.source.dirty")
    broker_report, broker_report_digest, broker_profile = _load_broker_prerequisite(
        report_path=credential_broker_report_path,
        profile_path=credential_broker_profile_path,
        endpoint=credential_broker_endpoint,
        ca_bundle_path=broker_ca_path,
        image_digest=image_digest,
    )
    _broker_authority(profile, broker_profile)
    current: Callable[[], datetime] = (
        (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    )
    started = current()
    if started.tzinfo is None or started.utcoffset() is None:
        _fail("customer-github-context-qualification.time.invalid")
    started = started.astimezone(timezone.utc)
    profile_metadata = _mapping(
        profile.get("metadata"),
        "customer-github-context-qualification.profile.invalid",
    )
    reviewed = _parse_timestamp(
        profile_metadata.get("reviewedAt"),
        "customer-github-context-qualification.profile.invalid",
    )
    if reviewed > started:
        _fail("customer-github-context-qualification.profile.invalid")
    profile_spec = _mapping(
        profile.get("spec"), "customer-github-context-qualification.profile.invalid"
    )
    query_spec = _mapping(
        profile_spec.get("query"),
        "customer-github-context-qualification.profile.invalid",
    )
    expected = _mapping(
        profile_spec.get("expected"),
        "customer-github-context-qualification.profile.invalid",
    )
    objective = _mapping(
        profile_spec.get("objective"),
        "customer-github-context-qualification.profile.invalid",
    )
    point = broker or _external_broker(broker_profile, identity_path, broker_ca_path)
    backend = GithubContextDocumentsBackend(
        registry,
        point,
        _CallableClock(current),
        transport=transport,
    )
    request = ContextDocumentQuery(
        tenant_id=str(query_spec["tenantId"]),
        actor_id=str(query_spec["actorId"]),
        request_id="ctq_" + hashlib.sha256(_canonical(profile)).hexdigest()[:32],
        integration_id=str(query_spec["integrationId"]),
        resource_uids=(str(query_spec["resourceUid"]),),
        kinds=(str(query_spec["documentKind"]),),
        reference_ids=(str(query_spec["referenceId"]),),
        max_documents=1,
        max_excerpt_chars=int(objective["maximumDocumentBytes"]),
        max_bytes=int(objective["maximumDocumentBytes"]),
        deadline=_timestamp(
            started + timedelta(seconds=int(objective["requestDeadlineSeconds"]))
        ),
    )
    began = time.monotonic_ns()
    result = None
    try:
        result = backend.query_context(request)
    except GithubContextBackendError:
        pass
    latency = max(0, (time.monotonic_ns() - began + 999_999) // 1_000_000)
    if latency > 300_000:
        _fail("customer-github-context-qualification.latency.unbounded")
    documents = result.documents if result is not None else ()
    returned_count = 1 if len(documents) == 1 else 0
    document = documents[0] if returned_count == 1 else None
    expected_locator = (
        f"repo://github/{repository.owner}/{repository.name}/{expected['path']}"
    )
    expected_commit_prefix = f"git:{expected['commitSha']}:blob:"
    expected_revision = expected_commit_prefix + str(expected["blobSha"])
    complete = bool(
        result is not None
        and result.status == "complete"
        and not result.warnings
        and returned_count == 1
    )
    immutable_commit = bool(
        document is not None and document.revision.startswith(expected_commit_prefix)
    )
    path_bound = bool(
        document is not None
        and document.reference_id == query_spec["referenceId"]
        and document.resource_uids == (query_spec["resourceUid"],)
        and document.kind == query_spec["documentKind"]
        and document.locator == expected_locator
    )
    expected_blob = bool(document is not None and document.revision == expected_revision)
    successful_read = complete and immutable_commit and path_bound and expected_blob
    observations = {
        "profile-binding": 0
        <= (started - reviewed).total_seconds()
        <= int(objective["maximumProfileAgeSeconds"]),
        "source-binding": True,
        "immutable-release": True,
        "protected-input-files": True,
        "integration-config-binding": True,
        "exact-endpoint": selected.endpoint == expected["endpoint"],
        "exact-api-version": selected.api_version == expected["apiVersion"],
        "qualified-credential-broker": True,
        "exact-read-authority": True,
        "workload-identity-accepted": successful_read,
        "provider-credential-accepted": successful_read,
        "ca-verified-tls": successful_read,
        "direct-no-proxy-no-redirect": True,
        "immutable-commit-request": successful_read,
        "document-path-binding": successful_read,
        "complete-single-document": successful_read,
        "expected-git-blob": successful_read,
        "bounded-read-latency": latency
        <= int(objective["maximumReadLatencyMilliseconds"]),
        "content-not-retained": True,
        "minimized-output": True,
    }
    completed = current().astimezone(timezone.utc)
    return build_report(
        revision=revision,
        profile=profile,
        integration_configuration=integration_configuration,
        image_digest=image_digest,
        github_ca_bundle_digest=github_ca_digest,
        credential_broker_report_digest=broker_report_digest,
        credential_broker_report=broker_report,
        credential_broker_profile=broker_profile,
        started_at=started,
        completed_at=completed,
        returned_document_count=returned_count if successful_read else 0,
        read_latency_milliseconds=latency,
        observed_revision=document.revision if successful_read else None,
        observations=observations,
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-github-context-qualification.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-github-context-qualification.output.invalid")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    integration_configuration_path: Path,
    credential_broker_report_path: Path,
    credential_broker_profile_path: Path,
    credential_broker_endpoint: str,
    credential_broker_ca_bundle_path: Path,
    github_ca_bundle_path: Path | None,
    image_digest: str,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    _, report_payload = _regular_bytes(
        report_path,
        maximum_bytes=1_048_576,
        code="customer-github-context-qualification.report.unreadable",
    )
    report = _json_document(
        report_payload, "customer-github-context-qualification.report.unreadable"
    )
    validate_report_document(report)
    profile = load_profile(profile_path)
    integration_configuration = load_integration_configuration(
        integration_configuration_path
    )
    resolved_github_ca: Path | None = None
    if github_ca_bundle_path is not None:
        resolved_github_ca, github_ca_payload = _regular_bytes(
            github_ca_bundle_path,
            maximum_bytes=2 * 1024 * 1024,
            code="customer-github-context-qualification.github-ca.invalid",
        )
        github_ca_digest = _digest_bytes(github_ca_payload)
    else:
        github_ca_digest = _digest_value("system-trust")
    _selected_configuration(profile, integration_configuration, resolved_github_ca)
    broker_ca_path, _ = _regular_bytes(
        credential_broker_ca_bundle_path,
        maximum_bytes=2 * 1024 * 1024,
        code="customer-github-context-qualification.credential-broker-ca.invalid",
    )
    broker_report, broker_report_digest, broker_profile = _load_broker_prerequisite(
        report_path=credential_broker_report_path,
        profile_path=credential_broker_profile_path,
        endpoint=credential_broker_endpoint,
        ca_bundle_path=broker_ca_path,
        image_digest=image_digest,
    )
    _broker_authority(profile, broker_profile)
    revision, dirty = _source_identity()
    metadata = _mapping(report.get("metadata"), "customer-github-context-qualification.report.invalid")
    spec = _mapping(report.get("spec"), "customer-github-context-qualification.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-github-context-qualification.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-github-context-qualification.report.invalid")
    profile_spec = _mapping(profile.get("spec"), "customer-github-context-qualification.profile.invalid")
    expected = _mapping(profile_spec.get("expected"), "customer-github-context-qualification.profile.invalid")
    broker_bindings = _mapping(
        _mapping(broker_report.get("spec"), "customer-github-context-qualification.credential-broker.crossed").get("bindings"),
        "customer-github-context-qualification.credential-broker.crossed",
    )
    repository_binding = {
        "owner": expected["repositoryOwner"],
        "name": expected["repositoryName"],
        "commitSha": expected["commitSha"],
    }
    document_binding = {
        "referenceId": profile_spec["query"]["referenceId"],
        "resourceUid": profile_spec["query"]["resourceUid"],
        "kind": profile_spec["query"]["documentKind"],
        "path": expected["path"],
        "blobSha": expected["blobSha"],
    }
    if (
        dirty
        or metadata.get("sourceRevision") != revision
        or subject.get("sourceRevision") != revision
        or subject.get("applicationVersion") != APPLICATION_VERSION
        or subject.get("imageDigest") != image_digest
        or bindings.get("profileDigest") != _digest_value(profile)
        or bindings.get("integrationConfigurationDigest")
        != _digest_value(integration_configuration)
        or bindings.get("endpointBindingDigest") != _digest_value(expected["endpoint"])
        or bindings.get("repositoryBindingDigest") != _digest_value(repository_binding)
        or bindings.get("documentBindingDigest") != _digest_value(document_binding)
        or bindings.get("githubCaBundleDigest") != github_ca_digest
        or bindings.get("credentialBrokerReportDigest") != broker_report_digest
        or bindings.get("credentialBrokerEndpointBindingDigest")
        != broker_bindings.get("endpointBindingDigest")
        or bindings.get("credentialBrokerProfileDigest")
        != broker_bindings.get("profileDigest")
        or bindings.get("credentialBrokerAuthoritySetDigest")
        != broker_bindings.get("authoritySetDigest")
        or bindings.get("credentialBrokerCaBundleDigest")
        != broker_bindings.get("caBundleDigest")
    ):
        _fail("customer-github-context-qualification.report.crossed")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-github-context-qualification.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify_parser = subparsers.add_parser("qualify")
    qualify_parser.add_argument("--profile", type=Path, required=True)
    qualify_parser.add_argument("--integration-config", type=Path, required=True)
    qualify_parser.add_argument("--credential-broker-report", type=Path, required=True)
    qualify_parser.add_argument("--credential-broker-profile", type=Path, required=True)
    qualify_parser.add_argument("--credential-broker-endpoint", required=True)
    qualify_parser.add_argument(
        "--credential-broker-workload-token-file", type=Path, required=True
    )
    qualify_parser.add_argument("--credential-broker-ca-file", type=Path, required=True)
    qualify_parser.add_argument("--github-ca-file", type=Path)
    qualify_parser.add_argument("--image-digest", required=True)
    qualify_parser.add_argument("--output", type=Path, required=True)
    qualify_parser.add_argument("--allow-context-observation", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--profile", type=Path, required=True)
    verify_parser.add_argument("--integration-config", type=Path, required=True)
    verify_parser.add_argument("--credential-broker-report", type=Path, required=True)
    verify_parser.add_argument("--credential-broker-profile", type=Path, required=True)
    verify_parser.add_argument("--credential-broker-endpoint", required=True)
    verify_parser.add_argument("--credential-broker-ca-file", type=Path, required=True)
    verify_parser.add_argument("--github-ca-file", type=Path)
    verify_parser.add_argument("--image-digest", required=True)
    verify_parser.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "qualify":
            profile = load_profile(arguments.profile)
            integration_configuration = load_integration_configuration(
                arguments.integration_config
            )
            report = qualify(
                profile=profile,
                integration_configuration=integration_configuration,
                credential_broker_report_path=arguments.credential_broker_report,
                credential_broker_profile_path=arguments.credential_broker_profile,
                credential_broker_endpoint=arguments.credential_broker_endpoint,
                credential_broker_workload_identity_token_path=arguments.credential_broker_workload_token_file,
                credential_broker_ca_bundle_path=arguments.credential_broker_ca_file,
                github_ca_bundle_path=arguments.github_ca_file,
                image_digest=arguments.image_digest,
                allow_context_observation=arguments.allow_context_observation,
            )
            _write_report(arguments.output, report)
            print(
                f"customer GitHub context qualification {report['spec']['status']}: "
                f"{arguments.output}"
            )
            return 0 if report["spec"]["status"] == "qualified" else 1
        verify_report(
            report_path=arguments.report,
            profile_path=arguments.profile,
            integration_configuration_path=arguments.integration_config,
            credential_broker_report_path=arguments.credential_broker_report,
            credential_broker_profile_path=arguments.credential_broker_profile,
            credential_broker_endpoint=arguments.credential_broker_endpoint,
            credential_broker_ca_bundle_path=arguments.credential_broker_ca_file,
            github_ca_bundle_path=arguments.github_ca_file,
            image_digest=arguments.image_digest,
            require_qualified=arguments.require_qualified,
        )
        print("customer GitHub context qualification report verified")
    except CustomerGithubContextQualificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
