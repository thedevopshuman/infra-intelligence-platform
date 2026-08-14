"""Authentication adapters behind the application-owned identity port."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Iterable

from iip.application.ports import (
    ActorContext,
    AuthenticationConfigurationError,
    AuthenticationError,
)


_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
_ACTOR_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_ROLE = re.compile(r"[a-z][a-z0-9._:-]{0,63}")
_BEARER_TOKEN = re.compile(r"[a-zA-Z0-9._~+/-]{32,8192}=*")
_IDENTITY_KEYS = {"tokenSha256", "actorId", "tenantId", "roles"}


@dataclass(frozen=True)
class BearerIdentity:
    """One local token verifier and its derived platform identity."""

    token_sha256: str = field(repr=False)
    actor: ActorContext


class DenyAllAuthenticator:
    """Fail-closed default used when no HTTP authentication is composed."""

    def authenticate_bearer(self, token: str) -> ActorContext:
        del token
        raise AuthenticationError("authentication.invalid")


class HashedBearerAuthenticator:
    """Local reference authenticator using high-entropy token verifiers."""

    def __init__(self, identities: Iterable[BearerIdentity]) -> None:
        configured = tuple(identities)
        if not 1 <= len(configured) <= 1000 or any(
            not isinstance(identity, BearerIdentity) for identity in configured
        ):
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            )
        for identity in configured:
            self._validate_identity(identity)
        digests = [identity.token_sha256 for identity in configured]
        if len(set(digests)) != len(digests):
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            )
        self._identities = configured

    @classmethod
    def from_json(cls, raw: str) -> "HashedBearerAuthenticator":
        """Parse a bounded verifier configuration without exposing details."""

        if not isinstance(raw, str) or not 1 <= len(raw) <= 1_048_576:
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            )
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {"identities"}:
                raise ValueError
            entries = payload["identities"]
            if not isinstance(entries, list):
                raise ValueError
            identities = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != _IDENTITY_KEYS:
                    raise ValueError
                roles = entry["roles"]
                if not isinstance(roles, list) or not all(
                    isinstance(role, str) for role in roles
                ):
                    raise ValueError
                identities.append(
                    BearerIdentity(
                        token_sha256=entry["tokenSha256"],
                        actor=ActorContext(
                            actor_id=entry["actorId"],
                            tenant_id=entry["tenantId"],
                            roles=tuple(roles),
                        ),
                    )
                )
            return cls(identities)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            ) from None

    def authenticate_bearer(self, token: str) -> ActorContext:
        if not isinstance(token, str) or not _BEARER_TOKEN.fullmatch(token):
            raise AuthenticationError("authentication.invalid")
        candidate = self.token_sha256(token)
        matched = None
        for identity in self._identities:
            if hmac.compare_digest(candidate, identity.token_sha256):
                matched = identity.actor
        if matched is None:
            raise AuthenticationError("authentication.invalid")
        return matched

    @staticmethod
    def token_sha256(token: str) -> str:
        """Calculate the verifier format used by local configuration."""

        return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_identity(identity: BearerIdentity) -> None:
        actor = identity.actor
        invalid_roles = (
            not isinstance(actor, ActorContext)
            or not isinstance(actor.roles, tuple)
            or len(actor.roles) > 64
            or any(
                not isinstance(role, str) or not _ROLE.fullmatch(role)
                for role in actor.roles
            )
        )
        if (
            not isinstance(identity.token_sha256, str)
            or not _DIGEST.fullmatch(identity.token_sha256)
            or not isinstance(actor, ActorContext)
            or not isinstance(actor.actor_id, str)
            or actor.actor_id == "anonymous"
            or not _ACTOR_ID.fullmatch(actor.actor_id)
            or not isinstance(actor.tenant_id, str)
            or not _TENANT_ID.fullmatch(actor.tenant_id)
            or invalid_roles
            or len(set(actor.roles)) != len(actor.roles)
        ):
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            )
