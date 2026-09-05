#!/usr/bin/env python3
"""Write ephemeral mTLS material for the isolated OTLP Docker gate."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from scripts.compatibility_tls import write_tls_material
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from compatibility_tls import write_tls_material


AUTHORIZED_SPIFFE_ID = "spiffe://customer.example/observability/collector"
UNAUTHORIZED_SPIFFE_ID = "spiffe://customer.example/observability/other"


def write_fixture(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("OTLP mTLS fixture directory must be empty")
    os.chmod(directory, 0o755)
    write_tls_material(
        directory,
        common_name="otlp-receiver.fixture",
        dns_name="otlp-receiver.fixture",
        client_identities={
            "collector-a": AUTHORIZED_SPIFFE_ID,
            "collector-b": AUTHORIZED_SPIFFE_ID,
            "other-workload": UNAUTHORIZED_SPIFFE_ID,
        },
        expired_client_identities={"collector-expired": AUTHORIZED_SPIFFE_ID},
        revoked_client_identities={"collector-revoked": AUTHORIZED_SPIFFE_ID},
        intermediate_client_ca=True,
        rotated_crl_identity_names=("collector-b",),
    )
    untrusted = directory / "untrusted"
    untrusted.mkdir()
    os.chmod(untrusted, 0o755)
    write_tls_material(
        untrusted,
        common_name="untrusted.fixture",
        dns_name="untrusted.fixture",
        client_identities={"collector": AUTHORIZED_SPIFFE_ID},
    )


def activate_rotated_crl(directory: Path) -> None:
    """Atomically promote the newer fixture CRL at the mounted receiver path."""

    current = directory / "ca.crl"
    rotated = directory / "rotated-ca.crl"
    if not current.is_file() or not rotated.is_file():
        raise ValueError("OTLP mTLS rotation fixture is incomplete")
    payload = rotated.read_bytes()
    temporary = directory / ".ca.crl.next"
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o644)
    os.replace(temporary, current)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--activate-rotated-crl",
        action="store_true",
        help="atomically replace ca.crl with the generated rotated CRL",
    )
    arguments = parser.parse_args()
    directory = arguments.directory.resolve()
    if arguments.activate_rotated_crl:
        activate_rotated_crl(directory)
    else:
        write_fixture(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
