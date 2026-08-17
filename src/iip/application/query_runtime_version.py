"""Authenticated runtime and deployment identity query."""

from __future__ import annotations

import re
from dataclasses import dataclass

from iip.application.ports import ActorContext, Clock


_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MIGRATION = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql$")


class RuntimeVersionConfigurationError(ValueError):
    """Configured runtime identity is malformed or internally inconsistent."""


@dataclass(frozen=True)
class RuntimeVersionIdentity:
    """Non-secret build and deployment facts supplied by the composition root."""

    application_version: str
    contract_api_version: str
    required_storage_migration: str
    build_revision: str | None = None
    helm_chart_version: str | None = None
    image_digest: str | None = None

    def __post_init__(self) -> None:
        values = (
            isinstance(self.application_version, str)
            and _SEMVER.fullmatch(self.application_version),
            self.contract_api_version == "iip.platform/v1alpha1",
            isinstance(self.required_storage_migration, str)
            and _MIGRATION.fullmatch(self.required_storage_migration),
            self.build_revision is None
            or (
                isinstance(self.build_revision, str)
                and _REVISION.fullmatch(self.build_revision)
            ),
            self.helm_chart_version is None
            or (
                isinstance(self.helm_chart_version, str)
                and _SEMVER.fullmatch(self.helm_chart_version)
            ),
            self.image_digest is None
            or (
                isinstance(self.image_digest, str)
                and _DIGEST.fullmatch(self.image_digest)
            ),
        )
        if not all(values):
            raise RuntimeVersionConfigurationError(
                "runtime.version.configuration.invalid"
            )


@dataclass(frozen=True)
class GetRuntimeVersionCommand:
    actor: ActorContext


@dataclass(frozen=True)
class RuntimeVersionReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class RuntimeVersionService:
    """Render verified runtime identity inside the authenticated tenant context."""

    def __init__(self, identity: RuntimeVersionIdentity, clock: Clock) -> None:
        self._identity = identity
        self._clock = clock

    def get(self, command: GetRuntimeVersionCommand) -> RuntimeVersionReport:
        build: dict[str, object] = {
            "mode": "release" if self._identity.build_revision else "development"
        }
        if self._identity.build_revision is not None:
            build["revision"] = self._identity.build_revision

        deployment: dict[str, object] = {}
        if self._identity.helm_chart_version is not None:
            deployment["helmChartVersion"] = self._identity.helm_chart_version
        if self._identity.image_digest is not None:
            deployment["imageDigest"] = self._identity.image_digest

        return RuntimeVersionReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "RuntimeVersionReport",
                "metadata": {
                    "tenantId": command.actor.tenant_id,
                    "evaluatedAt": self._clock.now(),
                },
                "spec": {
                    "application": {"version": self._identity.application_version},
                    "contracts": {"apiVersion": self._identity.contract_api_version},
                    "storage": {
                        "requiredMigration": self._identity.required_storage_migration
                    },
                    "build": build,
                    "deployment": deployment,
                },
            }
        )
