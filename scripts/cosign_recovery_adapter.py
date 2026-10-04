#!/usr/bin/env python3
"""Read-only Cosign compatibility adapter for the original v0.84.2 release.

Run from the separately approved tooling checkout; never modify the original
release checkout. Only the exact Docker Hub claim alias from ADR 0165 changes,
and only after pinned Cosign successfully verifies the fixed release identity.
This adapter neither creates release reports nor signs or publishes artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Sequence
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/cosign_container.sh"
VERSION = "3.1.2"
IDENTITY = (
    "https://github.com/thedevopshuman/infra-intelligence-platform/"
    ".github/workflows/release.yml@refs/tags/v0.84.2"
)
ISSUER = "https://token.actions.githubusercontent.com"
TARGETS = {
    "docker.io/thedevopshuman/iip":
        "sha256:13d15527b3ed7c65ba34ed2b102f15bfbe29a33106ef0ec6953d9bad3361bcfc",
    "docker.io/thedevopshuman/iip-bridge":
        "sha256:57fad1274d105348fd9ee2ec9ff8177b62ddc5d56688943d31c56b940e2b5565",
}
VERIFY_PREFIX = (
    "verify", "--output", "json", "--certificate-identity", IDENTITY,
    "--certificate-oidc-issuer", ISSUER,
)
SIGNATURE_TYPE = "https://sigstore.dev/cosign/sign/v1"
MAX_JSON_BYTES = 2_097_152
MAX_SIGNATURES = 16


class RecoveryAdapterError(ValueError):
    """Stable failures; provider output is not an error message."""


def _arguments(arguments: tuple[str, ...]) -> tuple[str, str] | None:
    if arguments == ("version", "--json"):
        return None
    if len(arguments) == len(VERIFY_PREFIX) + 1 and arguments[:-1] == VERIFY_PREFIX:
        for repository, digest in TARGETS.items():
            if arguments[-1] == repository + "@" + digest:
                return repository, digest
    raise RecoveryAdapterError("release-recovery.cosign.arguments-invalid")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    del value
    raise ValueError("non-JSON constant")


def _document(encoded: bytes, *, limit: int = MAX_JSON_BYTES) -> object:
    if not encoded or len(encoded) > limit:
        raise RecoveryAdapterError("release-recovery.cosign.output-invalid")
    try:
        return json.loads(encoded, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise RecoveryAdapterError("release-recovery.cosign.output-invalid") from None


def _normalize(encoded: bytes, repository: str, digest: str) -> object:
    document = _document(encoded)
    if not isinstance(document, list) or not 1 <= len(document) <= MAX_SIGNATURES:
        raise RecoveryAdapterError("release-recovery.cosign.output-invalid")
    for item in document:
        critical = item.get("critical") if isinstance(item, dict) else None
        identity = critical.get("identity") if isinstance(critical, dict) else None
        image = critical.get("image") if isinstance(critical, dict) else None
        claim = identity.get("docker-reference") if isinstance(identity, dict) else None
        if isinstance(claim, str) and claim.startswith("index.docker.io/"):
            claim = "docker.io/" + claim.removeprefix("index.docker.io/")
        if (claim not in (repository, repository + "@" + digest)
                or not isinstance(image, dict)
                or image.get("docker-manifest-digest") != digest
                or critical.get("type") != SIGNATURE_TYPE):
            raise RecoveryAdapterError("release-recovery.cosign.output-invalid")
        identity["docker-reference"] = claim
    return document


def _docker_host() -> str | None:
    value = os.environ.get("IIP_RECOVERY_DOCKER_HOST")
    if value is None:
        return None
    try:
        if (not value.startswith("unix:///") or len(value) > 2048
                or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
                or any(character in value for character in ("?", "#", "%"))):
            raise ValueError
        parsed = urlsplit(value)
        path = Path(parsed.path)
        if (parsed.scheme != "unix" or parsed.netloc or parsed.query or parsed.fragment
                or not path.is_absolute() or not stat.S_ISSOCK(path.stat().st_mode)):
            raise ValueError
    except (OSError, ValueError):
        raise RecoveryAdapterError("release-recovery.cosign.docker-host-invalid") from None
    return value


def _run(arguments: tuple[str, ...]) -> bytes:
    # Verification never receives ambient registry credentials, signing tokens,
    # trust overrides, or the wrapper's optional Docker-executable override.
    docker_host = _docker_host()
    with tempfile.TemporaryDirectory(prefix="iip-recovery-cosign-") as config:
        environment = {"PATH": os.environ.get("PATH", os.defpath), "DOCKER_CONFIG": config}
        if docker_host is not None:
            environment["DOCKER_HOST"] = docker_host
        try:
            result = subprocess.run(
                (str(WRAPPER), *arguments), cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                timeout=25 if arguments[0] == "version" else 170, env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            raise RecoveryAdapterError("release-recovery.cosign.unavailable") from None
    if result.returncode != 0:
        raise RecoveryAdapterError("release-recovery.cosign.verification-failed")
    return result.stdout


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = tuple(sys.argv[1:] if argv is None else argv)
        target = _arguments(arguments)
        encoded = _run(arguments)
        if target is None:
            document = _document(encoded, limit=65_536)
            if (not isinstance(document, dict)
                    or document.get("gitVersion", document.get("version")) not in (VERSION, "v" + VERSION)):
                raise RecoveryAdapterError("release-recovery.cosign.version-mismatch")
        else:
            document = _normalize(encoded, *target)
        output = json.dumps(document, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        if len(output.encode("utf-8")) > MAX_JSON_BYTES:
            raise RecoveryAdapterError("release-recovery.cosign.output-invalid")
    except RecoveryAdapterError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError):
        print("release-recovery.cosign.failed", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
