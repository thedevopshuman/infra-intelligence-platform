#!/usr/bin/env python3
"""Package the committed Helm chart with the same revision's license notices."""

from __future__ import annotations

import argparse
import copy
import gzip
import io
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from typing import Sequence


CHART_SOURCE = "deploy/helm/infra-intelligence"
PREFIX = "infra-intelligence/"
LICENSE_FILES = ("LICENSE", "NOTICE")


def package_chart(repository: Path, revision: str, destination: Path) -> None:
    """Read only committed Git objects; never stage or extract repository files."""
    if re.fullmatch(r"[0-9a-f]{40,64}", revision) is None:
        raise ValueError("release.chart.revision.invalid")

    def git(*arguments: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        ).stdout

    timestamp = int(git("show", "--no-patch", "--format=%ct", revision))
    notices = {
        filename: git("show", f"{revision}:{filename}")
        for filename in LICENSE_FILES
    }
    chart = git(
        "archive", "--format=tar", f"--prefix={PREFIX}",
        f"{revision}:{CHART_SOURCE}",
    )
    with tarfile.open(fileobj=io.BytesIO(chart), mode="r:") as source:
        members = source.getmembers()
        names: set[str] = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not (
                    member.name.startswith(PREFIX)
                    or (member.name == PREFIX.rstrip("/") and member.isdir())
                )
                or ".." in path.parts
                or path.is_absolute()
                or not (member.isfile() or member.isdir())
                or member.name in names
                or member.name in {PREFIX + name for name in LICENSE_FILES}
            ):
                raise ValueError("release.chart.archive.invalid")
            names.add(member.name)

        # An explicit timestamp and empty gzip filename keep the source artifact
        # reproducible, independent of output filename and working-tree metadata.
        with destination.open("xb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=timestamp) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for member in members:
                        packaged = copy.copy(member)
                        packaged.mtime = timestamp
                        packaged.pax_headers = {}
                        archive.addfile(
                            packaged, source.extractfile(member) if member.isfile() else None,
                        )
                    for filename, content in notices.items():
                        member = tarfile.TarInfo(PREFIX + filename)
                        member.mode = 0o644
                        member.mtime = timestamp
                        member.size = len(content)
                        archive.addfile(member, io.BytesIO(content))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        package_chart(Path.cwd(), arguments.revision, arguments.output)
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError):
        parser.exit(2, "ERROR: release.chart.packaging.failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
