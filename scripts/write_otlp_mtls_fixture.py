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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    write_fixture(arguments.directory.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
