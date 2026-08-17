#!/usr/bin/env python3
"""Generate source-bound real-TLS compatibility evidence for OIDC/JWKS auth."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "scripts"))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402
from iip.adapters.auth import OidcConfiguration, OidcJwtAuthenticator  # noqa: E402
from iip.application.ports import AuthenticationError  # noqa: E402

from compatibility_tls import write_tls_material  # noqa: E402
import validate_repo  # noqa: E402
import validate_schemas  # noqa: E402


COMPOSE_FILE = ROOT / "deploy" / "docker-compose.oidc-issuer.yml"
PROJECT = "iip-oidc-issuer-test"
ENDPOINT = "https://127.0.0.1:19443"
CHECK_IDS = (
    "ca-verified-tls",
    "untrusted-ca-denial",
    "rs256-jwks-authentication",
    "issuer-denial",
    "audience-denial",
    "expiry-and-issued-at-denial",
    "claim-shape-denial",
    "tenant-role-derivation",
    "header-indirection-denial",
    "pkce-discovery-minimization",
    "jwks-cache",
    "unknown-kid-refresh-throttle",
    "key-rotation-without-restart",
    "removed-key-denial",
    "redirect-denial",
    "expired-cache-outage-fail-closed",
    "issuer-recovery",
    "secret-redaction",
)


class ManualMonotonic:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def encoded_integer(value: int) -> str:
    content = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(content).rstrip(b"=").decode("ascii")


def public_jwk(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, object]:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "key_ops": ["verify"],
        "alg": "RS256",
        "n": encoded_integer(numbers.n),
        "e": encoded_integer(numbers.e),
    }


def write_fixture(directory: Path) -> dict[str, rsa.RSAPrivateKey]:
    write_tls_material(
        directory,
        common_name="oidc-issuer.fixture",
        dns_name="oidc-issuer.fixture",
    )
    keys = {
        "generation-a": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "generation-b": rsa.generate_private_key(public_exponent=65537, key_size=2048),
    }
    public_keys = {
        generation: public_jwk(key, generation)
        for generation, key in keys.items()
    }
    (directory / "public-keys.json").write_text(
        json.dumps(public_keys, sort_keys=True), encoding="utf-8"
    )
    (directory / "generation").write_text("generation-a", encoding="ascii")
    (directory / "audit.jsonl").write_text("", encoding="utf-8")
    os.chmod(directory / "public-keys.json", 0o644)
    os.chmod(directory / "generation", 0o666)
    os.chmod(directory / "audit.jsonl", 0o666)
    return keys


def claims(**changes: object) -> dict[str, object]:
    now = utc_now()
    value: dict[str, object] = {
        "iss": ENDPOINT + "/",
        "aud": "iip-control-plane",
        "sub": "oidc-user-17",
        "iat": int(now.timestamp()) - 1,
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "iip_tenant_id": "tenant-a",
        "iip_roles": ["developer", "approver"],
    }
    value.update(changes)
    return value


def access_token(
    private_key: rsa.RSAPrivateKey,
    kid: str,
    *,
    claim_changes: Mapping[str, object] | None = None,
    header_changes: Mapping[str, object] | None = None,
) -> str:
    token_claims = claims(**dict(claim_changes or {}))
    headers: dict[str, object] = {"kid": kid, "typ": "at+jwt"}
    headers.update(header_changes or {})
    return jwt.encode(token_claims, private_key, algorithm="RS256", headers=headers)


def configuration(
    directory: Path,
    *,
    trusted: bool = True,
    jwks_path: str = "/jwks",
) -> OidcConfiguration:
    return OidcConfiguration(
        issuer=ENDPOINT + "/",
        audience="iip-control-plane",
        jwks_url=ENDPOINT + jwks_path,
        tenant_claim="iip_tenant_id",
        roles_claim="iip_roles",
        ca_bundle_path=str(directory / "ca.crt") if trusted else None,
        cache_seconds=30,
        clock_skew_seconds=5,
        browser=OidcConfiguration.from_json(
            json.dumps(
                {
                    "issuer": ENDPOINT + "/",
                    "audience": "iip-control-plane",
                    "jwksUrl": ENDPOINT + jwks_path,
                    "tenantClaim": "iip_tenant_id",
                    "rolesClaim": "iip_roles",
                    "caBundlePath": str(directory / "ca.crt") if trusted else None,
                    "cacheSeconds": 30,
                    "clockSkewSeconds": 5,
                    "browser": {
                        "clientId": "iip-console-fixture",
                        "authorizationEndpoint": ENDPOINT + "/authorize",
                        "tokenEndpoint": ENDPOINT + "/token",
                        "redirectUri": "http://127.0.0.1:8080/console",
                        "scopes": ["openid", "profile"],
                        "providerLabel": "Compatibility fixture",
                    },
                }
            )
        ).browser,
    )


def expect_stable_denial(authenticator: OidcJwtAuthenticator, token: str) -> None:
    try:
        authenticator.authenticate_bearer(token)
    except AuthenticationError as error:
        if str(error) != "authentication.invalid":
            raise RuntimeError("OIDC compatibility returned an unstable error")
        return
    raise RuntimeError("OIDC compatibility unexpectedly authenticated a token")


def run_command(
    command: list[str],
    *,
    environment: Mapping[str, str] | None = None,
    capture: bool = False,
) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=dict(environment) if environment is not None else None,
        text=True,
        capture_output=capture,
        timeout=180,
        check=True,
    )
    return completed.stdout.strip() if capture else ""


def compose_command(docker: str, *arguments: str) -> list[str]:
    return [
        docker,
        "compose",
        "--project-name",
        PROJECT,
        "-f",
        str(COMPOSE_FILE),
        *arguments,
    ]


def checked_revision() -> tuple[str, bool]:
    revision = run_command(["git", "rev-parse", "HEAD"], capture=True)
    dirty = bool(
        run_command(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            capture=True,
        )
    )
    return revision, dirty


def compatibility_report(
    *,
    revision: str,
    source_dirty: bool,
    docker_platform: str,
    docker_version: str,
) -> dict[str, object]:
    identity = json.dumps(
        {
            "revision": revision,
            "platform": docker_platform,
            "profile": "local-oidc-rs256-jwks-v1",
            "checks": CHECK_IDS,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checks = [{"id": check_id, "status": "passed"} for check_id in CHECK_IDS]
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "OidcIssuerCompatibilityReport",
        "metadata": {
            "id": "oir_" + hashlib.sha256(identity).hexdigest()[:32],
            "generatedAt": timestamp(utc_now()),
            "sourceRevision": revision,
            "sourceDirty": source_dirty,
        },
        "spec": {
            "status": "compatible",
            "environment": {
                "platform": docker_platform,
                "containerRuntime": "docker",
                "containerRuntimeVersion": docker_version,
                "pythonVersion": platform.python_version(),
                "applicationVersion": APPLICATION_VERSION,
            },
            "profile": {
                "name": "local-oidc-rs256-jwks-v1",
                "tokenAlgorithm": "RS256",
                "browserFlow": "authorization-code-pkce-s256",
                "cacheSeconds": 30,
                "minimumRefreshIntervalSeconds": 5,
            },
            "checks": checks,
            "summary": {
                "totalChecks": len(checks),
                "passedChecks": len(checks),
                "failedChecks": 0,
                "overallStatus": "compatible",
            },
        },
    }


def validate_report(report: dict[str, object]) -> None:
    schema = json.loads(
        (ROOT / "contracts/schemas/oidc-issuer-compatibility-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    errors = validate_schemas.instance_validation_errors(
        schema, report, label="generated OIDC issuer compatibility report"
    )
    if errors:
        raise RuntimeError("; ".join(errors))
    semantic_errors: list[str] = []
    validate_repo.validate_oidc_issuer_compatibility_document(
        report, semantic_errors
    )
    if semantic_errors:
        raise RuntimeError("; ".join(semantic_errors))


def audit_documents(directory: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (directory / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]


def run_profile(docker: str, report_path: Path) -> dict[str, object]:
    report_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="iip-oidc-issuer-") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o755)
        keys = write_fixture(directory)
        token_a = access_token(keys["generation-a"], "generation-a")
        token_b = access_token(keys["generation-b"], "generation-b")
        clock = ManualMonotonic()
        environment = dict(os.environ)
        environment["IIP_OIDC_ISSUER_FIXTURE_DIR"] = str(directory)
        down = compose_command(docker, "down", "--volumes", "--remove-orphans")
        run_command(down, environment=environment)
        stage = "fixture-startup"
        try:
            run_command(
                compose_command(docker, "up", "--detach", "--wait"),
                environment=environment,
            )
            authenticator = OidcJwtAuthenticator(
                configuration(directory), monotonic=clock
            )
            stage = "initial RS256 authentication"
            actor = authenticator.authenticate_bearer(token_a)
            if (
                actor.actor_id != "oidc-user-17"
                or actor.tenant_id != "tenant-a"
                or actor.roles != ("developer", "approver")
            ):
                raise RuntimeError("OIDC compatibility derived the wrong identity")
            repeated = authenticator.authenticate_bearer(token_a)
            if repeated != actor or len(audit_documents(directory)) != 1:
                raise RuntimeError("OIDC compatibility did not cache the JWKS")

            expect_stable_denial(
                authenticator,
                access_token(
                    keys["generation-a"],
                    "generation-a",
                    claim_changes={"iss": "https://other-issuer.fixture.invalid/"},
                ),
            )
            expect_stable_denial(
                authenticator,
                access_token(
                    keys["generation-a"],
                    "generation-a",
                    claim_changes={"aud": "other-control-plane"},
                ),
            )
            now = utc_now()
            for invalid_time in (
                {
                    "iat": int((now - timedelta(minutes=10)).timestamp()),
                    "exp": int((now - timedelta(minutes=5)).timestamp()),
                },
                {
                    "iat": int((now + timedelta(minutes=10)).timestamp()),
                    "exp": int((now + timedelta(minutes=15)).timestamp()),
                },
            ):
                expect_stable_denial(
                    authenticator,
                    access_token(
                        keys["generation-a"],
                        "generation-a",
                        claim_changes=invalid_time,
                    ),
                )
            for invalid_claim in (
                {"sub": "invalid actor"},
                {"iip_tenant_id": "invalid tenant"},
                {"iip_roles": "developer"},
            ):
                expect_stable_denial(
                    authenticator,
                    access_token(
                        keys["generation-a"],
                        "generation-a",
                        claim_changes=invalid_claim,
                    ),
                )
            expect_stable_denial(
                authenticator,
                access_token(
                    keys["generation-a"],
                    "generation-a",
                    header_changes={"jku": "https://attacker.invalid/jwks"},
                ),
            )
            hs_token = jwt.encode(
                claims(),
                "fixture-hmac-key-not-trusted-0123456789",
                algorithm="HS256",
                headers={"kid": "generation-a", "typ": "at+jwt"},
            )
            expect_stable_denial(authenticator, hs_token)

            discovery = authenticator.console_authentication_document()
            encoded_discovery = json.dumps(discovery, sort_keys=True)
            if (
                discovery.get("spec", {}).get("oidc", {}).get("pkceMethod") != "S256"  # type: ignore[union-attr]
                or any(
                    protected in encoded_discovery
                    for protected in (
                        "iip-control-plane",
                        "/jwks",
                        "iip_tenant_id",
                        "iip_roles",
                        str(directory / "ca.crt"),
                    )
                )
            ):
                raise RuntimeError("OIDC compatibility discovery exposed verifier policy")

            expect_stable_denial(authenticator, token_b)
            if len(audit_documents(directory)) != 1:
                raise RuntimeError("OIDC unknown kid bypassed the refresh throttle")
            (directory / "generation").write_text("generation-b", encoding="ascii")
            clock.advance(6)
            stage = "rotated RS256 authentication"
            rotated_actor = authenticator.authenticate_bearer(token_b)
            if rotated_actor != actor or len(audit_documents(directory)) != 2:
                raise RuntimeError("OIDC compatibility did not rotate the JWKS")
            expect_stable_denial(authenticator, token_a)

            expect_stable_denial(
                OidcJwtAuthenticator(configuration(directory, trusted=False)), token_b
            )
            expect_stable_denial(
                OidcJwtAuthenticator(
                    configuration(directory, jwks_path="/redirect")
                ),
                token_b,
            )

            run_command(
                compose_command(docker, "stop", "oidc-issuer"),
                environment=environment,
            )
            clock.advance(31)
            expect_stable_denial(authenticator, token_b)
            run_command(
                compose_command(docker, "up", "--detach", "--wait", "oidc-issuer"),
                environment=environment,
            )
            stage = "issuer recovery authentication"
            recovery_actor = authenticator.authenticate_bearer(token_b)
            if recovery_actor != actor:
                raise RuntimeError("OIDC compatibility did not recover")

            audits = audit_documents(directory)
            if [entry.get("route") for entry in audits] != [
                "/jwks",
                "/jwks",
                "/redirect",
                "/jwks",
            ] or [entry.get("generation") for entry in audits] != [
                "generation-a",
                "generation-b",
                "generation-b",
                "generation-b",
            ]:
                raise RuntimeError("OIDC compatibility JWKS audit is inconsistent")

            revision, dirty = checked_revision()
            report = compatibility_report(
                revision=revision,
                source_dirty=dirty,
                docker_platform=run_command(
                    [docker, "info", "--format", "{{.OSType}}/{{.Architecture}}"],
                    capture=True,
                ),
                docker_version=run_command(
                    [docker, "version", "--format", "{{.Server.Version}}"],
                    capture=True,
                ),
            )
            private_material = tuple(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ).decode("ascii")
                for key in keys.values()
            )
            server_key_material = (directory / "server.key").read_text(
                encoding="ascii"
            )
            encoded_report = json.dumps(report, sort_keys=True)
            audit_text = (directory / "audit.jsonl").read_text(encoding="utf-8")
            protected_values = private_material + (
                server_key_material,
                token_a,
                token_b,
                hs_token,
                ENDPOINT,
                "oidc-user-17",
                "tenant-a",
                "developer",
                "approver",
            )
            if any(
                value in encoded_report or value in audit_text
                for value in protected_values
            ):
                raise RuntimeError("OIDC compatibility evidence exposed a secret")
            validate_report(report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
            return report
        except AuthenticationError as error:
            raise RuntimeError(f"{stage}: {error}") from None
        finally:
            run_command(down, environment=environment)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "oidc-issuer-compatibility-report.json",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    docker = os.environ.get("IIP_DOCKER_BIN", "docker")
    try:
        report = run_profile(docker, arguments.report.resolve())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        arguments.report.unlink(missing_ok=True)
        print(f"OIDC issuer compatibility failed: {error}", file=sys.stderr)
        return 1
    summary = report["spec"]["summary"]  # type: ignore[index]
    print(
        "OIDC issuer compatibility passed: "
        f"{summary['passedChecks']}/{summary['totalChecks']} checks; "  # type: ignore[index]
        "CA-verified JWKS, exact claims, cache/rotation, PKCE discovery, and recovery"
    )
    print(f"report: {arguments.report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
