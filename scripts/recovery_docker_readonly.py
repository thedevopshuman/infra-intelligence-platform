#!/usr/bin/env python3
"""Read-only Docker adapter for the original v0.84.2 publication verifier.

The historical publisher has an idempotent already-present path. This adapter
permits only its exact version/inspect calls; failed inspection can never fall
through to a push, copy, build, login or tag mutation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


REFERENCES = {
    "docker.io/thedevopshuman/iip":
        "sha256:13d15527b3ed7c65ba34ed2b102f15bfbe29a33106ef0ec6953d9bad3361bcfc",
    "docker.io/thedevopshuman/iip-bridge":
        "sha256:57fad1274d105348fd9ee2ec9ff8177b62ddc5d56688943d31c56b940e2b5565",
}


class RecoveryDockerError(ValueError):
    """Stable recovery diagnostic, never raw CLI output."""


def validate_arguments(arguments: list[str]) -> str | None:
    if arguments == ["buildx", "version"]:
        return None
    if (len(arguments) != 6 or arguments[:3] != ["buildx", "imagetools", "inspect"]
            or arguments[4:] != ["--format", "{{json .Manifest}}"]):
        raise RecoveryDockerError("release-recovery.docker.read-only-command-required")
    for repository, digest in REFERENCES.items():
        if arguments[3] in (repository + ":v0.84.2", repository + "@" + digest):
            return digest
    raise RecoveryDockerError("release-recovery.docker.reference-not-allowed")


def execute(arguments: list[str]) -> str:
    expected = validate_arguments(arguments)
    executable = os.environ.get("IIP_RECOVERY_BUILDX")
    command = ["docker", *arguments]
    if executable is not None:
        # Docker Desktop registers Buildx in the user's config. Use an explicit
        # installed binary when needed, never inherit that credential-bearing
        # config or its ambient plugin search paths.
        path = Path(executable)
        if (not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK)
                or any(ord(character) < 32 for character in executable)):
            raise RecoveryDockerError("release-recovery.docker.buildx-invalid")
        command = [executable, *arguments[1:]]
    with tempfile.TemporaryDirectory(prefix="iip-recovery-docker-") as directory:
        # No host Docker credentials or injected CLI/plugin paths are inherited.
        environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "SystemRoot")
                       if key in os.environ}
        environment["DOCKER_CONFIG"] = directory
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, check=False,
                                    timeout=180, env=environment)
        except (OSError, subprocess.SubprocessError):
            raise RecoveryDockerError("release-recovery.docker.unavailable") from None
    if result.returncode or len(result.stdout.encode()) > 2_097_152:
        raise RecoveryDockerError("release-recovery.docker.read-failed")
    if expected is None:
        # Keep the actual Buildx version, not Crane's version in a Docker field.
        if (len(result.stdout) > 1024 or not re.search(r"\bv?[0-9]+\.[0-9]+\.[0-9]+\b", result.stdout)
                or any(ord(character) < 32 and character not in "\r\n\t" for character in result.stdout)):
            raise RecoveryDockerError("release-recovery.docker.version-invalid")
        return result.stdout
    try:
        document = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RecoveryDockerError("release-recovery.docker.inspect-invalid") from None
    if not isinstance(document, dict) or document.get("digest") != expected:
        raise RecoveryDockerError("release-recovery.docker.digest-mismatch")
    # The old verifier consumes only the inspected digest; omit other metadata.
    return json.dumps({"digest": expected}) + "\n"


def main() -> int:
    try:
        result = execute(sys.argv[1:])
    except RecoveryDockerError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
