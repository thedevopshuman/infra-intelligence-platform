#!/usr/bin/env python3
"""Generate source-bound compatibility evidence for the external broker client."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "scripts"))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402
from iip.adapters.credential_broker import (  # noqa: E402
    CredentialBrokerUnavailableError,
    ExternalCredentialBrokerConfiguration,
    ExternalHttpCredentialBroker,
)
from iip.adapters.evidence import SystemClock  # noqa: E402
from iip.application.ports import CredentialLeaseRequest  # noqa: E402

import validate_schemas  # noqa: E402
import validate_repo  # noqa: E402
from compatibility_tls import write_tls_material  # noqa: E402


COMPOSE_FILE = ROOT / "deploy" / "docker-compose.credential-broker.yml"
PROJECT = "iip-credential-broker-test"
ENDPOINT = "https://127.0.0.1:18443"
CHECK_IDS = (
    "ca-verified-tls",
    "untrusted-ca-denial",
    "workload-jwt-authentication",
    "audience-denial",
    "subject-denial",
    "exact-scope-lease",
    "cross-tenant-denial",
    "scope-escalation-denial",
    "rotation-without-restart",
    "revocation",
    "audit-completeness",
    "broker-outage-fail-closed",
    "broker-recovery",
    "secret-redaction",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def base64url(content: bytes) -> str:
    return base64.urlsafe_b64encode(content).rstrip(b"=").decode("ascii")


def workload_token(
    signing_key: str,
    *,
    issuer: str,
    audience: str,
    subject: str,
    generation: str,
    now: datetime | None = None,
) -> str:
    issued = now or utc_now()
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": int(issued.timestamp()) - 1,
        "exp": int(issued.timestamp()) + 300,
        "jti": generation,
    }
    encoded_header = base64url(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    encoded_claims = base64url(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signed = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(signing_key.encode("ascii"), signed, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_claims}.{base64url(signature)}"


def write_fixture(directory: Path) -> dict[str, object]:
    issuer = "https://issuer.fixture.invalid"
    audience = "iip-credential-broker"
    subject = "system:serviceaccount:iip-system:iip-api"
    policy: dict[str, object] = {
        "issuer": issuer,
        "audience": audience,
        "subject": subject,
        "jwtSigningKey": secrets.token_urlsafe(32),
        "leaseDerivationKey": secrets.token_urlsafe(32),
        "workloadGenerations": ["generation-a", "generation-b"],
        "tenantId": "tenant-a",
        "integrationId": "prometheus-test",
        "credentialRef": "credential://prometheus/tenant-a/metrics-reader",
        "provider": "prometheus",
        "scopes": ["metrics:read"],
    }
    (directory / "policy.json").write_text(
        json.dumps(policy, sort_keys=True), encoding="utf-8"
    )
    (directory / "audit.jsonl").write_text("", encoding="utf-8")
    os.chmod(directory / "policy.json", 0o644)
    os.chmod(directory / "audit.jsonl", 0o666)

    write_tls_material(
        directory,
        common_name="credential-broker.fixture",
        dns_name="credential-broker.fixture",
    )
    return policy


def client(directory: Path, *, trusted: bool = True) -> ExternalHttpCredentialBroker:
    configuration = {
        "endpoint": ENDPOINT,
        "caBundlePath": str(directory / "ca.crt") if trusted else None,
        "workloadIdentityTokenPath": str(directory / "workload-token"),
        "requestTimeoutSeconds": 3,
        "maxResponseBytes": 65_536,
        "maxLeaseSeconds": 120,
        "maxClockSkewSeconds": 5,
    }
    return ExternalHttpCredentialBroker(
        ExternalCredentialBrokerConfiguration.from_json(json.dumps(configuration)),
        SystemClock(),
    )


def lease_request(
    *,
    tenant_id: str = "tenant-a",
    scopes: tuple[str, ...] = ("metrics:read",),
) -> CredentialLeaseRequest:
    return CredentialLeaseRequest(
        tenant_id=tenant_id,
        actor_id="investigation-runtime",
        integration_id="prometheus-test",
        credential_ref="credential://prometheus/tenant-a/metrics-reader",
        provider="prometheus",
        scopes=scopes,
        deadline=timestamp(utc_now() + timedelta(seconds=20)),
    )


def expect_stable_denial(
    broker: ExternalHttpCredentialBroker,
    request: CredentialLeaseRequest,
    *,
    expected_error: str,
) -> None:
    try:
        broker.resolve(request)
    except CredentialBrokerUnavailableError as error:
        if str(error) != expected_error:
            raise RuntimeError("credential compatibility returned an unstable error")
        return
    raise RuntimeError("credential compatibility unexpectedly issued a lease")


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
    revision = run_command(
        ["git", "rev-parse", "HEAD"],
        capture=True,
    )
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
    generated_at = timestamp(utc_now())
    identity = json.dumps(
        {
            "revision": revision,
            "platform": docker_platform,
            "profile": "local-tls-workload-identity-v1",
            "checks": CHECK_IDS,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checks = [{"id": check_id, "status": "passed"} for check_id in CHECK_IDS]
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "CredentialBrokerCompatibilityReport",
        "metadata": {
            "id": "cbr_" + hashlib.sha256(identity).hexdigest()[:32],
            "generatedAt": generated_at,
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
                "name": "local-tls-workload-identity-v1",
                "brokerProtocol": "iip.broker/v1alpha1",
                "workloadTokenProfile": "signed-jwt-fixture",
                "leaseScheme": "bearer",
                "maxLeaseSeconds": 120,
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
        (ROOT / "contracts/schemas/credential-broker-compatibility-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    errors = validate_schemas.instance_validation_errors(
        schema,
        report,
        label="generated credential broker compatibility report",
    )
    if errors:
        raise RuntimeError("; ".join(errors))
    semantic_errors: list[str] = []
    validate_repo.validate_credential_broker_compatibility_document(
        report, semantic_errors
    )
    if semantic_errors:
        raise RuntimeError("; ".join(semantic_errors))


def run_profile(docker: str, report_path: Path) -> dict[str, object]:
    report_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="iip-credential-broker-") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o755)
        policy = write_fixture(directory)
        signing_key = str(policy["jwtSigningKey"])
        token_a = workload_token(
            signing_key,
            issuer=str(policy["issuer"]),
            audience=str(policy["audience"]),
            subject=str(policy["subject"]),
            generation="generation-a",
        )
        token_b = workload_token(
            signing_key,
            issuer=str(policy["issuer"]),
            audience=str(policy["audience"]),
            subject=str(policy["subject"]),
            generation="generation-b",
        )
        token_path = directory / "workload-token"
        token_path.write_text(token_a, encoding="ascii")
        os.chmod(token_path, 0o644)
        environment = dict(os.environ)
        environment["IIP_CREDENTIAL_BROKER_FIXTURE_DIR"] = str(directory)
        down = compose_command(docker, "down", "--volumes", "--remove-orphans")
        run_command(down, environment=environment)
        stage = "fixture-startup"
        try:
            run_command(
                compose_command(docker, "up", "--detach", "--wait"),
                environment=environment,
            )
            broker = client(directory)
            stage = "initial exact-scope lease"
            lease_a = broker.resolve(lease_request())
            if lease_a.scheme != "bearer" or lease_a.secret in repr(lease_a):
                raise RuntimeError("credential compatibility lease is not redacted")

            token_path.write_text(
                workload_token(
                    signing_key,
                    issuer="https://other-issuer.fixture.invalid",
                    audience=str(policy["audience"]),
                    subject=str(policy["subject"]),
                    generation="generation-a",
                ),
                encoding="ascii",
            )
            expect_stable_denial(
                broker,
                lease_request(),
                expected_error="credential.broker.upstream.unavailable",
            )
            token_path.write_text(
                workload_token(
                    signing_key,
                    issuer=str(policy["issuer"]),
                    audience=str(policy["audience"]),
                    subject=str(policy["subject"]),
                    generation="generation-a",
                    now=utc_now() - timedelta(minutes=10),
                ),
                encoding="ascii",
            )
            expect_stable_denial(
                broker,
                lease_request(),
                expected_error="credential.broker.upstream.unavailable",
            )
            token_path.write_text(
                workload_token(
                    signing_key,
                    issuer=str(policy["issuer"]),
                    audience="wrong-audience",
                    subject=str(policy["subject"]),
                    generation="generation-a",
                ),
                encoding="ascii",
            )
            expect_stable_denial(
                broker,
                lease_request(),
                expected_error="credential.broker.upstream.unavailable",
            )
            token_path.write_text(
                workload_token(
                    signing_key,
                    issuer=str(policy["issuer"]),
                    audience=str(policy["audience"]),
                    subject="system:serviceaccount:other:workload",
                    generation="generation-a",
                ),
                encoding="ascii",
            )
            expect_stable_denial(
                broker,
                lease_request(),
                expected_error="credential.broker.upstream.unavailable",
            )

            token_path.write_text(token_a, encoding="ascii")
            expect_stable_denial(
                broker,
                lease_request(tenant_id="tenant-b"),
                expected_error="credential.broker.request.denied",
            )
            expect_stable_denial(
                broker,
                lease_request(scopes=("metrics:read", "admin:write")),
                expected_error="credential.broker.request.denied",
            )

            token_path.write_text(token_b, encoding="ascii")
            stage = "rotated exact-scope lease"
            lease_b = broker.resolve(lease_request())
            if lease_a.secret == lease_b.secret:
                raise RuntimeError("credential compatibility lease did not rotate")
            token_path.write_text(token_a, encoding="ascii")
            expect_stable_denial(
                broker,
                lease_request(),
                expected_error="credential.broker.upstream.unavailable",
            )

            token_path.write_text(token_b, encoding="ascii")
            expect_stable_denial(
                client(directory, trusted=False),
                lease_request(),
                expected_error="credential.broker.upstream.unavailable",
            )
            run_command(
                compose_command(docker, "stop", "credential-broker"),
                environment=environment,
            )
            expect_stable_denial(
                broker,
                lease_request(),
                expected_error="credential.broker.upstream.unavailable",
            )
            run_command(
                compose_command(
                    docker,
                    "up",
                    "--detach",
                    "--wait",
                    "credential-broker",
                ),
                environment=environment,
            )
            stage = "broker recovery lease"
            recovery_lease = broker.resolve(lease_request())
            if recovery_lease.scheme != "bearer":
                raise RuntimeError("credential compatibility did not recover")

            audit_text = (directory / "audit.jsonl").read_text(encoding="utf-8")
            audits = [json.loads(line) for line in audit_text.splitlines() if line]
            reasons = [entry.get("reason") for entry in audits]
            expected_reasons = {
                "exact-scope": 3,
                "workload-authentication": 2,
                "audience-denied": 1,
                "subject-denied": 1,
                "tenant-denied": 1,
                "scope-denied": 1,
                "workload-revoked": 1,
            }
            if any(reasons.count(reason) != count for reason, count in expected_reasons.items()):
                raise RuntimeError("credential compatibility audit is incomplete")
            protected_values = (
                signing_key,
                str(policy["leaseDerivationKey"]),
                token_a,
                token_b,
                lease_a.secret,
                lease_b.secret,
                recovery_lease.secret,
            )
            if any(value in audit_text for value in protected_values):
                raise RuntimeError("credential compatibility audit exposed a secret")

            revision, dirty = checked_revision()
            docker_platform = run_command(
                [docker, "info", "--format", "{{.OSType}}/{{.Architecture}}"],
                capture=True,
            )
            docker_version = run_command(
                [docker, "version", "--format", "{{.Server.Version}}"],
                capture=True,
            )
            report = compatibility_report(
                revision=revision,
                source_dirty=dirty,
                docker_platform=docker_platform,
                docker_version=docker_version,
            )
            encoded_report = json.dumps(report, sort_keys=True, separators=(",", ":"))
            if any(value in encoded_report for value in protected_values):
                raise RuntimeError("credential compatibility report exposed a secret")
            validate_report(report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
            return report
        except CredentialBrokerUnavailableError as error:
            audit_lines = (directory / "audit.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            reason = "no-fixture-audit"
            if audit_lines:
                last_audit = json.loads(audit_lines[-1])
                reason = str(last_audit.get("reason", "unknown"))
            raise RuntimeError(f"{stage}: {error} ({reason})") from None
        finally:
            run_command(down, environment=environment)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "credential-broker-compatibility-report.json",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    docker = os.environ.get("IIP_DOCKER_BIN", "docker")
    try:
        report = run_profile(docker, arguments.report.resolve())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        arguments.report.unlink(missing_ok=True)
        print(f"credential broker compatibility failed: {error}", file=sys.stderr)
        return 1
    summary = report["spec"]["summary"]  # type: ignore[index]
    print(
        "credential broker compatibility passed: "
        f"{summary['passedChecks']}/{summary['totalChecks']} checks; "  # type: ignore[index]
        "CA-verified TLS, exact scope, workload rotation/revocation, "
        "audit, and outage recovery"
    )
    print(f"report: {arguments.report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
