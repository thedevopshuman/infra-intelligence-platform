#!/usr/bin/env python3
"""Generate source-bound real-TLS compatibility evidence for external policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "scripts"))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402
from iip.adapters.policy import (  # noqa: E402
    ExternalHttpPolicyDecisionPoint,
    ExternalPolicyConfiguration,
)
from iip.application.ports import ActorContext, PolicyDecision  # noqa: E402

from compatibility_tls import write_tls_material  # noqa: E402
import validate_repo  # noqa: E402
import validate_schemas  # noqa: E402


COMPOSE_FILE = ROOT / "deploy" / "docker-compose.policy-engine.yml"
PROJECT = "iip-policy-engine-test"
ENDPOINT = "https://127.0.0.1:19444"
RESOURCE_UID = "res_" + ("a" * 32)
CHECK_IDS = (
    "ca-verified-tls",
    "untrusted-ca-denial",
    "bearer-authentication",
    "exact-input",
    "allow-decision",
    "deny-decision",
    "input-digest-binding",
    "tenant-binding",
    "snapshot-binding",
    "redirect-denial",
    "content-type-denial",
    "response-size-denial",
    "token-rotation-without-restart",
    "token-revocation",
    "outage-fail-closed",
    "service-recovery",
    "audit-minimization",
    "secret-redaction",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def write_fixture(directory: Path) -> dict[str, str]:
    tokens = {
        "generation-a": secrets.token_urlsafe(48),
        "generation-b": secrets.token_urlsafe(48),
    }
    write_tls_material(
        directory,
        common_name="policy-engine.fixture",
        dns_name="policy-engine.fixture",
    )
    (directory / "tokens.json").write_text(
        json.dumps(tokens, sort_keys=True), encoding="utf-8"
    )
    (directory / "generation").write_text("generation-a", encoding="ascii")
    (directory / "client-token").write_text(
        tokens["generation-a"], encoding="ascii"
    )
    (directory / "audit.jsonl").write_text("", encoding="utf-8")
    os.chmod(directory / "tokens.json", 0o644)
    os.chmod(directory / "generation", 0o666)
    os.chmod(directory / "client-token", 0o600)
    os.chmod(directory / "audit.jsonl", 0o666)
    return tokens


def client(
    directory: Path,
    *,
    trusted: bool = True,
    path: str = "/v1/decision",
    max_response_bytes: int = 65_536,
) -> ExternalHttpPolicyDecisionPoint:
    configuration = ExternalPolicyConfiguration(
        endpoint=ENDPOINT + path,
        ca_bundle_path=str(directory / "ca.crt") if trusted else None,
        bearer_token_path=str(directory / "client-token"),
        timeout_seconds=3,
        max_response_bytes=max_response_bytes,
    )
    return ExternalHttpPolicyDecisionPoint(configuration)


def actor() -> ActorContext:
    return ActorContext("operator-a", "tenant-a", ("developer", "approver"))


def resource(scenario: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "tenantId": "tenant-a",
        "resourceUid": RESOURCE_UID,
    }
    if scenario is not None:
        value["scenario"] = scenario
    return value


def decide(
    policy: ExternalHttpPolicyDecisionPoint,
    scenario: str | None = None,
) -> PolicyDecision:
    return policy.decide(actor(), "resource:read", resource(scenario))


def expect_unavailable(decision: PolicyDecision) -> None:
    if decision.allowed or decision.reason_code != "policy.unavailable":
        raise RuntimeError("policy compatibility did not fail closed")
    if decision.policy_snapshot_ref != "policy://tenant-a/snapshots/unavailable":
        raise RuntimeError("policy compatibility returned an unstable denial")


def expect_allowed(decision: PolicyDecision) -> None:
    if (
        not decision.allowed
        or decision.reason_code != "policy.fixture-allowed"
        or decision.policy_snapshot_ref
        != "policy://tenant-a/snapshots/bundle-generation-1"
    ):
        raise RuntimeError("policy compatibility returned the wrong allow decision")


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
            "profile": "local-external-http-policy-v1",
            "checks": CHECK_IDS,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checks = [{"id": check_id, "status": "passed"} for check_id in CHECK_IDS]
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "PolicyEngineCompatibilityReport",
        "metadata": {
            "id": "per_" + hashlib.sha256(identity).hexdigest()[:32],
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
                "name": "local-external-http-policy-v1",
                "transport": "https-json",
                "requestWrapper": "opa-input",
                "credentialMode": "rotating-bearer-file",
                "maximumResponseBytes": 65_536,
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
        (
            ROOT
            / "contracts/schemas/policy-engine-compatibility-report.schema.json"
        ).read_text(encoding="utf-8")
    )
    errors = validate_schemas.instance_validation_errors(
        schema, report, label="generated policy engine compatibility report"
    )
    if errors:
        raise RuntimeError("; ".join(errors))
    semantic_errors: list[str] = []
    validate_repo.validate_policy_engine_compatibility_document(
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
    with tempfile.TemporaryDirectory(prefix="iip-policy-engine-") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o755)
        tokens = write_fixture(directory)
        environment = dict(os.environ)
        environment["IIP_POLICY_ENGINE_FIXTURE_DIR"] = str(directory)
        down = compose_command(docker, "down", "--volumes", "--remove-orphans")
        run_command(down, environment=environment)
        policy = client(directory)
        stage = "fixture-startup"
        try:
            run_command(
                compose_command(docker, "up", "--detach", "--wait"),
                environment=environment,
            )
            stage = "trusted allow decision"
            expect_allowed(decide(policy))
            expect_unavailable(decide(client(directory, trusted=False)))

            denied = decide(policy, "deny")
            if (
                denied.allowed
                or denied.reason_code != "policy.fixture-denied"
                or denied.policy_snapshot_ref
                != "policy://tenant-a/snapshots/bundle-generation-1"
            ):
                raise RuntimeError("policy compatibility returned the wrong denial")
            for scenario in ("stale-digest", "cross-tenant", "cross-snapshot"):
                expect_unavailable(decide(policy, scenario))
            expect_unavailable(decide(client(directory, path="/redirect")))
            expect_unavailable(
                decide(client(directory, path="/wrong-content-type"))
            )
            expect_unavailable(
                decide(
                    client(
                        directory,
                        path="/oversized",
                        max_response_bytes=1024,
                    )
                )
            )

            (directory / "generation").write_text("generation-b", encoding="ascii")
            (directory / "client-token").write_text(
                tokens["generation-b"], encoding="ascii"
            )
            stage = "rotated bearer decision"
            expect_allowed(decide(policy))
            (directory / "client-token").write_text(
                tokens["generation-a"], encoding="ascii"
            )
            expect_unavailable(decide(policy))
            (directory / "client-token").write_text(
                tokens["generation-b"], encoding="ascii"
            )

            run_command(
                compose_command(docker, "stop", "policy-engine"),
                environment=environment,
            )
            expect_unavailable(decide(policy))
            run_command(
                compose_command(docker, "up", "--detach", "--wait", "policy-engine"),
                environment=environment,
            )
            stage = "policy service recovery"
            expect_allowed(decide(policy))

            audits = audit_documents(directory)
            if len(audits) != 11 or any(
                set(entry) != {"recordedAt", "route", "generation", "outcome"}
                for entry in audits
            ):
                raise RuntimeError("policy compatibility audit was not minimized")
            if [entry.get("outcome") for entry in audits] != [
                "allowed",
                "denied",
                "invalid-response",
                "invalid-response",
                "invalid-response",
                "redirect",
                "allowed",
                "oversized-response",
                "allowed",
                "authentication-denied",
                "allowed",
            ]:
                raise RuntimeError("policy compatibility audit was inconsistent")

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
            report_text = json.dumps(report, sort_keys=True)
            audit_text = (directory / "audit.jsonl").read_text(encoding="utf-8")
            server_key = (directory / "server.key").read_text(encoding="ascii")
            protected_values = tuple(tokens.values()) + (
                server_key,
                ENDPOINT,
                "operator-a",
                "tenant-a",
                RESOURCE_UID,
                "developer",
                "approver",
            )
            if any(
                value in report_text or value in audit_text
                for value in protected_values
            ):
                raise RuntimeError("policy compatibility evidence exposed protected data")
            validate_report(report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
            return report
        except Exception as error:
            raise RuntimeError(f"{stage}: policy.compatibility.failed") from error
        finally:
            run_command(down, environment=environment)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "policy-engine-compatibility-report.json",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    docker = os.environ.get("IIP_DOCKER_BIN", "docker")
    try:
        report = run_profile(docker, arguments.report.resolve())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        arguments.report.unlink(missing_ok=True)
        print(f"Policy engine compatibility failed: {error}", file=sys.stderr)
        return 1
    summary = report["spec"]["summary"]  # type: ignore[index]
    print(
        "Policy engine compatibility passed: "
        f"{summary['passedChecks']}/{summary['totalChecks']} checks; "  # type: ignore[index]
        "CA-verified TLS, exact digest/snapshot binding, credential rotation, and recovery"
    )
    print(f"report: {arguments.report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
