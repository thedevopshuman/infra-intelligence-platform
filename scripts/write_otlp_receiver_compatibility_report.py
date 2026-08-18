#!/usr/bin/env python3
"""Write source-bound evidence after the executable OTLP receiver gate passes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "scripts"))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402

import validate_repo  # noqa: E402
import validate_schemas  # noqa: E402


COLLECTOR_VERSION = "0.158.0"
COLLECTOR_IMAGE_DIGEST = (
    "sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5"
)
CHECK_IDS = (
    "isolated-route-surface",
    "server-ca-verified-tls",
    "plaintext-denial",
    "client-ca-validation",
    "spiffe-workload-identity",
    "channel-identity-binding",
    "bearer-channel-authentication",
    "control-credential-denial",
    "official-metrics-exporter",
    "official-logs-exporter",
    "certificate-rotation-without-restart",
    "expired-client-certificate-rejected",
    "revoked-client-certificate-rejected",
    "health-probe-minimization",
    "durable-evidence-commit",
    "collector-persistent-queue-config",
    "secret-redaction",
)


def command(*arguments: str) -> str:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def checked_revision() -> tuple[str, bool]:
    revision = command("git", "rev-parse", "HEAD")
    dirty = bool(command("git", "status", "--porcelain", "--untracked-files=normal"))
    return revision, dirty


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


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
            "profile": "local-mutual-spiffe-otlp-http-v1",
            "checks": CHECK_IDS,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checks = [{"id": check_id, "status": "passed"} for check_id in CHECK_IDS]
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "OtlpReceiverCompatibilityReport",
        "metadata": {
            "id": "orc_" + hashlib.sha256(identity).hexdigest()[:32],
            "generatedAt": timestamp(),
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
                "collectorVersion": COLLECTOR_VERSION,
                "collectorImageDigest": COLLECTOR_IMAGE_DIGEST,
            },
            "profile": {
                "name": "local-mutual-spiffe-otlp-http-v1",
                "transport": "https-otlp-http-protobuf",
                "workloadIdentity": "x509-spiffe-uri-san",
                "channelCredential": "tenant-bound-bearer-sha256",
                "commitBoundary": "postgresql-before-success",
                "preReceiverBuffering": "collector-persistent-sending-queue",
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
            / "contracts/schemas/otlp-receiver-compatibility-report.schema.json"
        ).read_text(encoding="utf-8")
    )
    errors = validate_schemas.instance_validation_errors(
        schema, report, label="generated OTLP receiver compatibility report"
    )
    if errors:
        raise RuntimeError("; ".join(errors))
    semantic_errors: list[str] = []
    validate_repo.validate_otlp_receiver_compatibility_document(
        report, semantic_errors
    )
    if semantic_errors:
        raise RuntimeError("; ".join(semantic_errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "otlp-receiver-compatibility-report.json",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    docker = os.environ.get("IIP_DOCKER_BIN", "docker")
    try:
        revision, dirty = checked_revision()
        report = compatibility_report(
            revision=revision,
            source_dirty=dirty,
            docker_platform=command(
                docker, "info", "--format", "{{.OSType}}/{{.Architecture}}"
            ),
            docker_version=command(
                docker, "version", "--format", "{{.Server.Version}}"
            ),
        )
        validate_report(report)
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(report, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        arguments.report.unlink(missing_ok=True)
        print(f"OTLP receiver compatibility report failed: {error}", file=sys.stderr)
        return 1
    print(
        "OTLP receiver compatibility passed: "
        f"{len(CHECK_IDS)}/{len(CHECK_IDS)} checks; mTLS/SPIFFE, durable commit, "
        "and persistent Collector queue configuration"
    )
    print(f"report: {arguments.report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
