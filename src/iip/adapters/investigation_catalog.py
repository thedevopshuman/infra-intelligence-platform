"""Protected static investigation signal catalog adapter."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Iterable


_TENANT_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_SELECTION_FIELDS = frozenset(
    {
        "kubernetesEventSelections",
        "contextSelections",
        "changeSelections",
        "telemetrySelections",
        "logSelections",
    }
)


class InvestigationSignalCatalogConfigurationError(RuntimeError):
    """Protected catalog configuration is malformed or unsafe."""


class DisabledInvestigationSignalCatalog:
    """No-op catalog used until an operator configures tenant profiles."""

    def profiles(self) -> tuple[Mapping[str, object], ...]:
        return ()

    def get_profile(self, tenant_id: str) -> None:
        del tenant_id
        return None


class StaticInvestigationSignalCatalog:
    """Resolve one immutable, deployment-supplied profile per exact tenant."""

    MAX_CONFIGURATION_BYTES = 1_048_576

    def __init__(self, profiles: Iterable[Mapping[str, object]]) -> None:
        by_tenant: dict[str, dict[str, object]] = {}
        for raw_profile in profiles:
            profile = self._validate_header(raw_profile)
            tenant_id = str(profile["tenantId"])
            if tenant_id in by_tenant:
                raise InvestigationSignalCatalogConfigurationError(
                    "investigation.catalog.configuration.invalid"
                )
            by_tenant[tenant_id] = profile
        self._profiles = by_tenant

    @classmethod
    def from_json(cls, raw: str) -> "StaticInvestigationSignalCatalog":
        if (
            not isinstance(raw, str)
            or not 1 <= len(raw.encode("utf-8")) <= cls.MAX_CONFIGURATION_BYTES
        ):
            raise InvestigationSignalCatalogConfigurationError(
                "investigation.catalog.configuration.invalid"
            )
        try:
            document = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError):
            raise InvestigationSignalCatalogConfigurationError(
                "investigation.catalog.configuration.invalid"
            ) from None
        if (
            not isinstance(document, Mapping)
            or set(document) != {"apiVersion", "kind", "profiles"}
            or document.get("apiVersion") != "iip.platform/v1alpha1"
            or document.get("kind") != "InvestigationSignalCatalog"
        ):
            raise InvestigationSignalCatalogConfigurationError(
                "investigation.catalog.configuration.invalid"
            )
        profiles = document.get("profiles")
        if not isinstance(profiles, list) or len(profiles) > 256:
            raise InvestigationSignalCatalogConfigurationError(
                "investigation.catalog.configuration.invalid"
            )
        return cls(profiles)

    @staticmethod
    def _validate_header(raw: Mapping[str, object]) -> dict[str, object]:
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"tenantId", "profileId", "version", "selections"}
        ):
            raise InvestigationSignalCatalogConfigurationError(
                "investigation.catalog.configuration.invalid"
            )
        tenant_id = raw.get("tenantId")
        profile_id = raw.get("profileId")
        version = raw.get("version")
        selections = raw.get("selections")
        if (
            not isinstance(tenant_id, str)
            or _TENANT_ID.fullmatch(tenant_id) is None
            or not isinstance(profile_id, str)
            or _PROFILE_ID.fullmatch(profile_id) is None
            or not isinstance(version, str)
            or _SEMVER.fullmatch(version) is None
            or not isinstance(selections, Mapping)
            or not selections
            or set(selections).difference(_SELECTION_FIELDS)
        ):
            raise InvestigationSignalCatalogConfigurationError(
                "investigation.catalog.configuration.invalid"
            )
        for candidates in selections.values():
            if not isinstance(candidates, list) or not 1 <= len(candidates) <= 8:
                raise InvestigationSignalCatalogConfigurationError(
                    "investigation.catalog.configuration.invalid"
                )
        return copy.deepcopy(dict(raw))

    def profiles(self) -> tuple[Mapping[str, object], ...]:
        return tuple(copy.deepcopy(profile) for profile in self._profiles.values())

    def get_profile(self, tenant_id: str) -> Mapping[str, object] | None:
        profile = self._profiles.get(tenant_id)
        return copy.deepcopy(profile) if profile is not None else None


def build_investigation_signal_catalog(
    environment: Mapping[str, str],
) -> DisabledInvestigationSignalCatalog | StaticInvestigationSignalCatalog:
    raw = environment.get("IIP_INVESTIGATION_SIGNAL_CATALOG_JSON")
    if raw is None or not raw.strip():
        return DisabledInvestigationSignalCatalog()
    return StaticInvestigationSignalCatalog.from_json(raw)
