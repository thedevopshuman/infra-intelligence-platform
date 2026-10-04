#!/usr/bin/env python3
"""Manually diagnose only signatures on two exact published public image digests.

This does not reconstruct or qualify a release bundle, build, sign, or publish.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_publication  # noqa: E402
from release_signature_verification import (  # noqa: E402
    DIGEST,
    ReleaseSignatureError,
    SubprocessCosignRunner,
)


REPOSITORIES = (
    ("control-plane-image", "docker.io/thedevopshuman/iip"),
    ("plugin-mediation-bridge-image", "docker.io/thedevopshuman/iip-bridge"),
)
SIGNER_PREFIX = (
    "https://github.com/thedevopshuman/infra-intelligence-platform/"
    ".github/workflows/release.yml@refs/tags/"
)
ISSUER = "https://token.actions.githubusercontent.com"
LIMITATION = (
    "Signature-only diagnostic; NOT bundle qualification or release publication. "
    "No images were built, signed, or published by this command."
)
STABLE_RUNNER_ERRORS = frozenset({
    "release-signature.tool.invalid",
    "release-signature.tool.unavailable",
    "release-signature.tool.output-invalid",
    "release-signature.tool.version-mismatch",
    "release-signature.signature.rejected",
    "release-signature.policy.invalid",
})


class DiagnosticArgumentsError(ValueError):
    """Invalid input without reflecting caller-controlled values."""


class DiagnosticParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise DiagnosticArgumentsError()


def _error_code(error: ReleaseSignatureError) -> str:
    code = str(error)
    return code if code in STABLE_RUNNER_ERRORS else "release-signature.diagnostic.failed"


def main(argv: Sequence[str] | None = None) -> int:
    parser = DiagnosticParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--control-plane-digest", required=True)
    parser.add_argument("--bridge-digest", required=True)
    parser.add_argument("--cosign", default="scripts/cosign_container.sh")
    try:
        arguments = parser.parse_args(argv)
        if (not 2 <= len(arguments.tag) <= 129 or not arguments.tag.startswith("v")
                or release_publication.RELEASE_VERSION.fullmatch(arguments.tag[1:]) is None):
            raise DiagnosticArgumentsError()
        digests = (arguments.control_plane_digest, arguments.bridge_digest)
        if any(DIGEST.fullmatch(digest) is None for digest in digests):
            raise DiagnosticArgumentsError()
    except DiagnosticArgumentsError:
        print("release-signature.diagnostic.arguments-invalid", file=sys.stderr)
        print(LIMITATION)
        return 2

    try:
        runner = SubprocessCosignRunner(arguments.cosign)
        if runner.version() != release_publication.COSIGN_VERSION:
            raise ReleaseSignatureError("release-signature.tool.version-mismatch")
    except ReleaseSignatureError as error:
        print(_error_code(error), file=sys.stderr)
        print(LIMITATION)
        return 2

    trust = {
        "mode": "keyless",
        "identities": [{
            "id": "github-release-workflow",
            "certificateIdentity": SIGNER_PREFIX + arguments.tag,
            "certificateOidcIssuer": ISSUER,
        }],
    }
    failed = False
    for (role, repository), digest in zip(REPOSITORIES, digests):
        try:
            result = runner.verify(
                reference=repository + "@" + digest,
                repository=repository,
                digest=digest,
                trust=trust,
                transparency_mode="required",
            )
            print(f"{role} {digest} verified-count={result.signature_count}")
        except ReleaseSignatureError as error:
            failed = True
            print(f"{role} {digest} {_error_code(error)}", file=sys.stderr)
    print(LIMITATION)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
