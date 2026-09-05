"""Authentication adapters behind the application-owned identity port."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Iterable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import jwt

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
_CLAIM_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,255}")
_OIDC_REQUIRED_KEYS = {"issuer", "audience", "jwksUrl", "tenantClaim", "rolesClaim"}
_OIDC_OPTIONAL_KEYS = {
    "actorClaim",
    "browser",
    "caBundlePath",
    "cacheSeconds",
    "clockSkewSeconds",
}
_OIDC_BROWSER_REQUIRED_KEYS = {
    "clientId",
    "authorizationEndpoint",
    "tokenEndpoint",
    "redirectUri",
    "scopes",
}
_OIDC_BROWSER_OPTIONAL_KEYS = {"providerLabel"}
_OIDC_SCOPE = re.compile(r"[A-Za-z0-9._:/-]{1,128}")


def _safe_url_parts(value: object):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or re.search(r"[\x00-\x20\x7f]", value) is not None
    ):
        return None
    try:
        parsed = urlsplit(value)
        parsed.port
        return parsed
    except ValueError:
        return None


def _is_strict_https_endpoint(value: object) -> bool:
    parsed = _safe_url_parts(value)
    return bool(
        parsed is not None
        and parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _is_safe_console_redirect(value: object) -> bool:
    parsed = _safe_url_parts(value)
    if parsed is None:
        return False
    secure = parsed.scheme == "https"
    loopback = parsed.scheme == "http" and parsed.hostname in (
        "localhost",
        "127.0.0.1",
    )
    return bool(
        (secure or loopback)
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path in ("/console", "/console/")
    )


@dataclass(frozen=True)
class OidcBrowserConfiguration:
    """Closed public-client profile used only to bootstrap browser PKCE."""

    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    redirect_uri: str
    scopes: tuple[str, ...]
    provider_label: str = "Organization SSO"

    @classmethod
    def from_mapping(cls, payload: object) -> "OidcBrowserConfiguration":
        if (
            not isinstance(payload, Mapping)
            or not _OIDC_BROWSER_REQUIRED_KEYS.issubset(payload)
            or set(payload).difference(
                _OIDC_BROWSER_REQUIRED_KEYS | _OIDC_BROWSER_OPTIONAL_KEYS
            )
        ):
            raise ValueError
        scopes = payload["scopes"]
        if not isinstance(scopes, list):
            raise ValueError
        configuration = cls(
            client_id=payload["clientId"],
            authorization_endpoint=payload["authorizationEndpoint"],
            token_endpoint=payload["tokenEndpoint"],
            redirect_uri=payload["redirectUri"],
            scopes=tuple(scopes),
            provider_label=payload.get("providerLabel", "Organization SSO"),
        )
        configuration.validate()
        return configuration

    def validate(self) -> None:
        if (
            not isinstance(self.client_id, str)
            or re.fullmatch(r"[^\s\x00-\x1f]{1,256}", self.client_id) is None
            or not _is_strict_https_endpoint(self.authorization_endpoint)
            or not _is_strict_https_endpoint(self.token_endpoint)
            or not _is_safe_console_redirect(self.redirect_uri)
            or not 1 <= len(self.scopes) <= 32
            or any(
                not isinstance(scope, str) or _OIDC_SCOPE.fullmatch(scope) is None
                for scope in self.scopes
            )
            or len(set(self.scopes)) != len(self.scopes)
            or "openid" not in self.scopes
            or not isinstance(self.provider_label, str)
            or re.fullmatch(r"[^\x00-\x1f\x7f]{1,64}", self.provider_label)
            is None
        ):
            raise ValueError

    @property
    def token_origin(self) -> str:
        parsed = urlsplit(self.token_endpoint)
        return f"{parsed.scheme}://{parsed.netloc}"

    def public_document(self, issuer: str) -> Mapping[str, object]:
        return {
            "issuer": issuer,
            "clientId": self.client_id,
            "authorizationEndpoint": self.authorization_endpoint,
            "tokenEndpoint": self.token_endpoint,
            "redirectUri": self.redirect_uri,
            "scopes": list(self.scopes),
            "providerLabel": self.provider_label,
            "pkceMethod": "S256",
        }


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


@dataclass(frozen=True)
class OidcConfiguration:
    """Closed OIDC verifier configuration; token claims remain untrusted input."""

    issuer: str
    audience: str
    jwks_url: str
    tenant_claim: str
    roles_claim: str
    actor_claim: str = "sub"
    browser: OidcBrowserConfiguration | None = None
    ca_bundle_path: str | None = None
    cache_seconds: int = 300
    clock_skew_seconds: int = 30

    @classmethod
    def from_json(cls, raw: str) -> "OidcConfiguration":
        if not isinstance(raw, str) or not 1 <= len(raw) <= 65_536:
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            )
        try:
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or not _OIDC_REQUIRED_KEYS.issubset(payload)
                or set(payload).difference(_OIDC_REQUIRED_KEYS | _OIDC_OPTIONAL_KEYS)
            ):
                raise ValueError
            configuration = cls(
                issuer=payload["issuer"],
                audience=payload["audience"],
                jwks_url=payload["jwksUrl"],
                tenant_claim=payload["tenantClaim"],
                roles_claim=payload["rolesClaim"],
                actor_claim=payload.get("actorClaim", "sub"),
                browser=(
                    OidcBrowserConfiguration.from_mapping(payload["browser"])
                    if "browser" in payload
                    else None
                ),
                ca_bundle_path=payload.get("caBundlePath"),
                cache_seconds=payload.get("cacheSeconds", 300),
                clock_skew_seconds=payload.get("clockSkewSeconds", 30),
            )
            configuration.validate()
            return configuration
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            ) from None

    def validate(self) -> None:
        if self.browser is not None:
            try:
                self.browser.validate()
            except (AttributeError, ValueError):
                raise AuthenticationConfigurationError(
                    "authentication.configuration.invalid"
                ) from None
        issuer = urlsplit(self.issuer) if isinstance(self.issuer, str) else None
        jwks = urlsplit(self.jwks_url) if isinstance(self.jwks_url, str) else None
        invalid_url = (
            issuer is None
            or re.search(r"[\x00-\x20\x7f]", self.issuer) is not None
            or issuer.scheme != "https"
            or not issuer.hostname
            or issuer.username is not None
            or issuer.password is not None
            or issuer.query
            or issuer.fragment
            or jwks is None
            or re.search(r"[\x00-\x20\x7f]", self.jwks_url) is not None
            or jwks.scheme != "https"
            or not jwks.hostname
            or jwks.username is not None
            or jwks.password is not None
            or jwks.fragment
        )
        if (
            invalid_url
            or len(self.issuer) > 2048
            or len(self.jwks_url) > 2048
            or not isinstance(self.audience, str)
            or re.fullmatch(r"[^\s\x00-\x1f]{1,512}", self.audience) is None
            or any(
                not isinstance(claim, str) or _CLAIM_NAME.fullmatch(claim) is None
                for claim in (self.actor_claim, self.tenant_claim, self.roles_claim)
            )
            or len({self.actor_claim, self.tenant_claim, self.roles_claim}) != 3
            or (
                self.browser is not None
                and not isinstance(self.browser, OidcBrowserConfiguration)
            )
            or (
                self.ca_bundle_path is not None
                and (
                    not isinstance(self.ca_bundle_path, str)
                    or not self.ca_bundle_path.startswith("/")
                    or len(self.ca_bundle_path) > 4096
                )
            )
            or isinstance(self.cache_seconds, bool)
            or not isinstance(self.cache_seconds, int)
            or not 30 <= self.cache_seconds <= 3600
            or isinstance(self.clock_skew_seconds, bool)
            or not isinstance(self.clock_skew_seconds, int)
            or not 0 <= self.clock_skew_seconds <= 300
        ):
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            )

    def console_authentication_document(self) -> Mapping[str, object]:
        spec: dict[str, object] = {
            "mode": "oidc-pkce" if self.browser is not None else "access-token"
        }
        if self.browser is not None:
            spec["oidc"] = self.browser.public_document(self.issuer)
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ConsoleAuthenticationConfiguration",
            "spec": spec,
        }

    @property
    def console_token_origin(self) -> str | None:
        return self.browser.token_origin if self.browser is not None else None


class JwksTransport(Protocol):
    def fetch(self, url: str, context: ssl.SSLContext) -> Mapping[str, object]:
        """Fetch one bounded key set from the configured TLS endpoint."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        return None


class HttpsJwksTransport:
    """Bounded HTTPS-only JWKS transport with redirects disabled."""

    MAX_RESPONSE_BYTES = 262_144

    def fetch(self, url: str, context: ssl.SSLContext) -> Mapping[str, object]:
        opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "iip-oidc/0.67.0"},
            method="GET",
        )
        try:
            with opener.open(request, timeout=5) as response:
                content_type = response.headers.get_content_type()
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
                if (
                    response.status != 200
                    or content_type not in ("application/json", "application/jwk-set+json")
                    or len(body) > self.MAX_RESPONSE_BYTES
                ):
                    raise AuthenticationError("authentication.invalid")
        except (HTTPError, URLError, OSError, TimeoutError):
            raise AuthenticationError("authentication.invalid") from None
        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AuthenticationError("authentication.invalid") from None
        if not isinstance(document, Mapping):
            raise AuthenticationError("authentication.invalid")
        return document


class OidcJwtAuthenticator:
    """Verify RS256 OIDC access tokens against a bounded cached JWKS."""

    MINIMUM_REFRESH_INTERVAL_SECONDS = 5

    def __init__(
        self,
        configuration: OidcConfiguration,
        transport: JwksTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        configuration.validate()
        self._configuration = configuration
        try:
            self._ssl_context = ssl.create_default_context(
                cafile=configuration.ca_bundle_path
            )
        except (OSError, ssl.SSLError):
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            ) from None
        self._transport = transport or HttpsJwksTransport()
        self._monotonic = monotonic
        self._cached_jwks: Mapping[str, object] | None = None
        self._cache_expires_at = 0.0
        self._last_fetch_at = float("-inf")
        self._lock = RLock()

    @classmethod
    def from_json(cls, raw: str) -> "OidcJwtAuthenticator":
        return cls(OidcConfiguration.from_json(raw))

    def console_authentication_document(self) -> Mapping[str, object]:
        """Return only the reviewed non-secret browser bootstrap profile."""

        return self._configuration.console_authentication_document()

    @property
    def console_token_origin(self) -> str | None:
        """Return the single additional CSP connection origin, when configured."""

        return self._configuration.console_token_origin

    def authenticate_bearer(self, token: str) -> ActorContext:
        if (
            not isinstance(token, str)
            or not _BEARER_TOKEN.fullmatch(token)
            or token.count(".") != 2
        ):
            raise AuthenticationError("authentication.invalid")
        try:
            header = jwt.get_unverified_header(token)
            if (
                not isinstance(header, dict)
                or header.get("alg") != "RS256"
                or not isinstance(header.get("kid"), str)
                or not 1 <= len(header["kid"]) <= 256
                or ("typ" in header and header["typ"] not in ("JWT", "at+jwt"))
                or "crit" in header
                or "jku" in header
                or "x5u" in header
            ):
                raise AuthenticationError("authentication.invalid")
            key = self._signing_key(header["kid"], refresh=False)
            if key is None:
                key = self._signing_key(header["kid"], refresh=True)
            if key is None:
                raise AuthenticationError("authentication.invalid")
            claims = jwt.decode(
                token,
                key=jwt.PyJWK.from_dict(dict(key), algorithm="RS256").key,
                algorithms=["RS256"],
                audience=self._configuration.audience,
                issuer=self._configuration.issuer,
                leeway=self._configuration.clock_skew_seconds,
                options={
                    "require": [
                        "aud",
                        "exp",
                        "iat",
                        "iss",
                        "sub",
                        self._configuration.actor_claim,
                        self._configuration.tenant_claim,
                        self._configuration.roles_claim,
                    ]
                },
            )
            roles = claims[self._configuration.roles_claim]
            if not isinstance(roles, list) or not all(
                isinstance(role, str) for role in roles
            ):
                raise AuthenticationError("authentication.invalid")
            actor = ActorContext(
                actor_id=claims[self._configuration.actor_claim],
                tenant_id=claims[self._configuration.tenant_claim],
                roles=tuple(roles),
            )
            _validate_actor_context(actor)
            return actor
        except AuthenticationError:
            raise
        except (KeyError, TypeError, ValueError, jwt.PyJWTError):
            raise AuthenticationError("authentication.invalid") from None

    def _signing_key(
        self, kid: str, *, refresh: bool
    ) -> Mapping[str, object] | None:
        jwks = self._jwks(refresh=refresh)
        keys = jwks.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 64:
            raise AuthenticationError("authentication.invalid")
        matches = []
        for key in keys:
            if not isinstance(key, Mapping) or key.get("kid") != kid:
                continue
            if (
                key.get("kty") != "RSA"
                or key.get("use", "sig") != "sig"
                or key.get("alg", "RS256") != "RS256"
                or (
                    "key_ops" in key
                    and (
                        not isinstance(key["key_ops"], list)
                        or "verify" not in key["key_ops"]
                    )
                )
            ):
                raise AuthenticationError("authentication.invalid")
            matches.append(key)
        if len(matches) > 1:
            raise AuthenticationError("authentication.invalid")
        return matches[0] if matches else None

    def _jwks(self, *, refresh: bool) -> Mapping[str, object]:
        with self._lock:
            now = self._monotonic()
            if not refresh and self._cached_jwks is not None and now < self._cache_expires_at:
                return self._cached_jwks
            if (
                refresh
                and self._cached_jwks is not None
                and now - self._last_fetch_at < self.MINIMUM_REFRESH_INTERVAL_SECONDS
            ):
                return self._cached_jwks
            try:
                document = self._transport.fetch(
                    self._configuration.jwks_url, self._ssl_context
                )
            except AuthenticationError:
                raise
            except Exception:
                raise AuthenticationError("authentication.invalid") from None
            self._cached_jwks = dict(document)
            self._last_fetch_at = now
            self._cache_expires_at = now + self._configuration.cache_seconds
            return self._cached_jwks


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
        if (
            not isinstance(identity.token_sha256, str)
            or not _DIGEST.fullmatch(identity.token_sha256)
        ):
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            )
        _validate_actor_context(actor, configuration=True)


def _validate_actor_context(
    actor: ActorContext, *, configuration: bool = False
) -> None:
    invalid = (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.actor_id, str)
        or actor.actor_id == "anonymous"
        or _ACTOR_ID.fullmatch(actor.actor_id) is None
        or not isinstance(actor.tenant_id, str)
        or _TENANT_ID.fullmatch(actor.tenant_id) is None
        or not isinstance(actor.roles, tuple)
        or len(actor.roles) > 64
        or any(
            not isinstance(role, str) or _ROLE.fullmatch(role) is None
            for role in actor.roles
        )
        or len(set(actor.roles)) != len(actor.roles)
    )
    if invalid:
        if configuration:
            raise AuthenticationConfigurationError(
                "authentication.configuration.invalid"
            )
        raise AuthenticationError("authentication.invalid")
