#!/usr/bin/env python3
"""Write ephemeral certificates for the real PostgreSQL transport gate."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from scripts.compatibility_tls import write_tls_material
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from compatibility_tls import write_tls_material


def write_fixture(directory: Path) -> None:
    """Create one trusted server identity and one unrelated trust root."""

    directory = directory.resolve()
    if not directory.is_dir() or any(directory.iterdir()):
        raise ValueError("PostgreSQL TLS fixture directory must be empty")

    # The trusted connection uses the DNS name localhost. Deliberately exclude
    # 127.0.0.1 from the certificate so the integration gate can prove that
    # verify-full rejects the same listener when addressed by the wrong name.
    write_tls_material(
        directory,
        common_name="localhost",
        dns_name="localhost",
        ip_address="127.0.0.2",
    )

    untrusted = directory / "untrusted"
    untrusted.mkdir(mode=0o755)
    write_tls_material(
        untrusted,
        common_name="untrusted.localhost",
        dns_name="untrusted.localhost",
        ip_address="127.0.0.3",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    write_fixture(arguments.directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
