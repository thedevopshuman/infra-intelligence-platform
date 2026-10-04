#!/usr/bin/env python3
"""Build and inspect a source-only community installation kit, without Docker."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zlib
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_STREAM_BYTES = 256 * 1024 * 1024
MAX_CONTENT_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 10_000
MAX_PROJECT_BYTES = 64 * 1024
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
_ROOTS = frozenset({
    "src", "scripts", "deploy", "requirements", "contracts", "docs", "api",
    "sdks", "plugins", "instrumentation", "tests",
})
_TOP_FILES = frozenset({
    ".dockerignore", ".gitignore", ".editorconfig", "AGENTS.md", "README.md",
    "CONTRIBUTING.md", "LICENSE", "NOTICE", "SECURITY.md", "SUPPORT.md",
    "Dockerfile", "Makefile", "pyproject.toml",
})
_EXCLUDED = frozenset({
    "node_modules", "__pycache__", "dist", "build", "coverage",
})
_MIGRATIONS = (
    "0001_resource_event_substrate.sql", "0002_resource_relationship_index.sql",
    "0003_operational_workflows.sql", "0004_reconciliation_snapshots.sql",
    "0005_projection_rebuild_source.sql", "0006_source_checkpoint_provider_cursors.sql",
    "0007_investigation_lifecycle.sql", "0008_action_execution_lifecycle.sql",
    "0009_investigation_jobs.sql", "0010_event_outbox_quarantine.sql",
    "0011_event_outbox_slo_window.sql", "0012_investigation_job_slo_window.sql",
    "0013_evidence_artifact_retention.sql", "0014_durable_plugin_invocations.sql",
    "0015_plugin_invocation_lifecycle.sql", "0016_telemetry_export_health.sql",
    "0017_telemetry_export_slo_samples.sql", "0018_ai_usage_ledger.sql",
    "0019_ai_cost_ledger.sql", "0020_ai_savings_ledger.sql",
    "0021_ai_attribution_ledger.sql", "0022_ai_retry_savings_rule.sql",
    "0023_ai_model_suitability.sql", "0024_ai_invocation_correlation.sql",
    "0025_delivered_outbox_retention.sql", "0026_ai_history_availability.sql",
)
# This is the minimum operating closure, not a promise that arbitrary Python
# supplied by an untrusted publisher is executable. Build includes *all* files
# in each allowed committed source root. Runtime smoke tests prove that closure.
REQUIRED_KIT_PATHS = frozenset({
    "Dockerfile", ".dockerignore", "Makefile", "pyproject.toml", "README.md",
    "LICENSE", "NOTICE", "SECURITY.md", "SUPPORT.md",
    "requirements/verify.in", "requirements/verify.txt",
    "scripts/installation_kit.py",
    *{f"scripts/community_{name}.py" for name in (
        "stack", "transport", "collector", "dashboard", "docker", "recovery",
        "backup_crypto", "trust",
    )},
    "deploy/docker-compose.community.yml",
    *{f"deploy/community/{name}" for name in (
        "collector.yaml", "dashboards.yaml", "datasources.yaml", "initialize.py",
        "pg_hba.conf", "prometheus.yml", "recovery_volume.py",
    )},
    "deploy/grafana/dashboards/iip-community-ai-finops.json",
    "docs/operations/community-installation.md",
    "docs/operations/community-installation-kit.md",
    "docs/operations/installation-options.md",
    "docs/operations/community-recovery.md",
    "docs/operations/community-trust-rotation.md",
    "src/iip/bootstrap.py", "src/iip/domain/models.py",
    "src/iip/adapters/otlp_ai_usage_receiver.py",
    "src/iip/application/attribute_ai_usage.py",
    "src/iip/application/calculate_ai_cost.py",
    "src/iip/application/evaluate_ai_savings.py",
    "src/iip/application/qualify_ai_price_catalog.py",
    "src/iip/adapters/postgres/__main__.py",
    "src/iip/surfaces/http.py", "src/iip/surfaces/worker.py",
    "src/iip/surfaces/otlp_receiver.py", "src/iip/surfaces/maintenance.py",
    *{f"src/iip/surfaces/static/{name}" for name in ("index.html", "app.css", "app.js")},
    *{f"src/iip/adapters/postgres/migrations/{name}" for name in _MIGRATIONS},
})


class InstallationKitError(ValueError):
    """Stable errors contain no source contents, credentials or command output."""


def _prefix(version: str) -> str:
    if not isinstance(version, str) or len(version) > 64 or not _VERSION.fullmatch(version):
        raise InstallationKitError("installation-kit.version.invalid")
    return f"infra-intelligence-community-{version}"


def _allowed(relative: str) -> bool:
    if relative in _TOP_FILES:
        return True
    parts = relative.split("/")
    return (
        len(parts) > 1 and parts[0] in _ROOTS
        and all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*", part)
                and part not in _EXCLUDED for part in parts)
        and not relative.endswith((".pyc", ".log", ".key", ".pem", ".p12"))
    )


class _BoundedReader:
    def __init__(self, source: BinaryIO) -> None:
        self.source = source
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        remaining = MAX_STREAM_BYTES - self.count
        size = min(size if size >= 0 else remaining + 1, remaining + 1)
        data = self.source.read(size)
        self.count += len(data)
        if self.count > MAX_STREAM_BYTES:
            raise InstallationKitError("installation-kit.archive.limit")
        return data


def inspect_installation_kit(path: Path, version: str) -> Mapping[str, int]:
    """Stream a bounded archive without extraction or executing packaged code."""
    prefix = _prefix(version)
    files: set[str] = set()
    entries: dict[str, bool] = {}
    implicit_directories: set[str] = set()
    total = 0
    project: bytes | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode)
                    or not 0 < metadata.st_size <= MAX_ARCHIVE_BYTES):
                raise InstallationKitError("installation-kit.archive.invalid")
            with gzip.GzipFile(fileobj=source, mode="rb") as compressed:
                stream = _BoundedReader(compressed)
                with tarfile.open(fileobj=stream, mode="r|") as archive:
                    for member in archive:
                        name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
                        parts = name.split("/")
                        if (
                            len(entries) >= MAX_MEMBERS or len(name) > 512
                            or not name or parts[0] != prefix
                            or any(part in {"", ".", ".."} for part in parts)
                            or "\\" in name or name in entries
                            or not (member.isfile() or member.isdir())
                            or member.pax_headers or member.sparse is not None
                            or member.linkname or member.mode & ~0o777
                            or member.size < 0 or member.size > MAX_MEMBER_BYTES
                            or member.isdir() and member.size != 0
                        ):
                            raise InstallationKitError("installation-kit.archive.invalid")
                        relative = "/".join(parts[1:])
                        if member.isfile() and not _allowed(relative):
                            raise InstallationKitError("installation-kit.archive.path-invalid")
                        if member.isdir() and relative and not (
                            relative in _ROOTS or _allowed(relative)
                        ):
                            raise InstallationKitError("installation-kit.archive.path-invalid")
                        parents = {"/".join(parts[:index]) for index in range(1, len(parts))}
                        if any(entries.get(parent) is False for parent in parents) or (
                            member.isfile() and name in implicit_directories
                        ):
                            raise InstallationKitError("installation-kit.archive.parent-invalid")
                        entries[name] = member.isdir()
                        implicit_directories.update(parents)
                        if member.isdir():
                            continue
                        total += member.size
                        if total > MAX_CONTENT_BYTES:
                            raise InstallationKitError("installation-kit.archive.limit")
                        if relative in REQUIRED_KIT_PATHS and member.size == 0:
                            raise InstallationKitError("installation-kit.archive.incomplete")
                        if relative == "pyproject.toml" and member.size > MAX_PROJECT_BYTES:
                            raise InstallationKitError("installation-kit.archive.limit")
                        body = archive.extractfile(member)
                        if body is None:
                            raise InstallationKitError("installation-kit.archive.invalid")
                        with body:
                            if relative == "pyproject.toml":
                                project = body.read()
                            else:
                                while body.read(64 * 1024):
                                    pass
                        files.add(relative)
                    # A second tar stream or hidden non-padding suffix must not
                    # escape validation after the first end-of-archive marker.
                    while suffix := archive.fileobj.read(64 * 1024):
                        if any(suffix):
                            raise InstallationKitError("installation-kit.archive.invalid")
                # Consume the gzip trailer, catching truncation/CRC failure and
                # limiting padding or additional compressed members as well.
                while stream.read(64 * 1024):
                    pass
        if not REQUIRED_KIT_PATHS.issubset(files) or project is None:
            raise InstallationKitError("installation-kit.archive.incomplete")
        if tomllib.loads(project.decode("utf-8")).get("project", {}).get("version") != version:
            raise InstallationKitError("installation-kit.version.mismatch")
    except InstallationKitError:
        raise
    except (OSError, EOFError, tarfile.TarError, UnicodeError, ValueError,
            AttributeError, KeyError, TypeError, OverflowError, zlib.error):
        raise InstallationKitError("installation-kit.archive.invalid") from None
    return {"files": len(files), "contentBytes": total}


def _git(root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments], check=True, capture_output=True,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        raise InstallationKitError("installation-kit.source.invalid") from None


def _add_blobs(root: Path, archive: tarfile.TarFile, prefix: str,
               selected: list[tuple[str, int, str]]) -> None:
    """One read-only Git process; bounded individual blobs, no worktree reads."""
    process = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        total = 0
        for name, mode, digest in sorted(selected):
            process.stdin.write(digest.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline(256).decode("ascii").strip().split()
            if len(header) != 3 or header[:2] != [digest, "blob"]:
                raise InstallationKitError("installation-kit.source.invalid")
            size = int(header[2])
            total += size
            if size < 0 or size > MAX_MEMBER_BYTES or total > MAX_CONTENT_BYTES:
                raise InstallationKitError("installation-kit.archive.limit")
            body = process.stdout.read(size)
            if len(body) != size or process.stdout.read(1) != b"\n":
                raise InstallationKitError("installation-kit.source.invalid")
            member = tarfile.TarInfo(f"{prefix}/{name}")
            member.mode, member.size = mode, size
            archive.addfile(member, io.BytesIO(body))
        process.stdin.close()
        if process.wait() != 0:
            raise InstallationKitError("installation-kit.source.invalid")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()


def _clean_revision(root: Path) -> str:
    top = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip())
    if top.resolve() != root.resolve() or _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise InstallationKitError("installation-kit.source.clean-required")
    return _git(root, "rev-parse", "HEAD").decode("ascii").strip()


def build_installation_kit(root: Path, output: Path, version: str) -> None:
    """Archive only clean committed blobs; publish atomically without replacing."""
    prefix = _prefix(version)
    if output.exists() or output.is_symlink():
        raise InstallationKitError("installation-kit.output.exists")
    revision = _clean_revision(root)
    selected: list[tuple[str, int, str]] = []
    for entry in _git(root, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if not entry:
            continue
        try:
            header, raw_name = entry.split(b"\t", 1)
            mode, kind, digest = header.decode("ascii").split()
            name = raw_name.decode("utf-8")
        except (UnicodeError, ValueError):
            raise InstallationKitError("installation-kit.source.invalid") from None
        if name.split("/")[0] not in _ROOTS and name not in _TOP_FILES:
            continue
        if not _allowed(name) or kind != "blob" or mode not in {"100644", "100755"}:
            raise InstallationKitError("installation-kit.source.path-invalid")
        selected.append((name, 0o755 if mode == "100755" else 0o644, digest))
    if not REQUIRED_KIT_PATHS.issubset({name for name, _, _ in selected}):
        raise InstallationKitError("installation-kit.source.incomplete")
    if len(selected) + 1 > MAX_MEMBERS:
        raise InstallationKitError("installation-kit.archive.limit")
    try:
        with tempfile.TemporaryDirectory(prefix="iip-installation-kit-") as temporary:
            candidate = Path(temporary) / "installation.tar.gz"
            with candidate.open("wb") as destination, gzip.GzipFile(
                filename="", fileobj=destination, mode="wb", mtime=0,
            ) as compressed, tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                top = tarfile.TarInfo(prefix)
                top.type, top.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(top)
                _add_blobs(root, archive, prefix, selected)
            inspect_installation_kit(candidate, version)
            if _clean_revision(root) != revision:
                raise InstallationKitError("installation-kit.source.changed")
            # Same-directory temporary publication permits an atomic hard link
            # even when the source scratch directory is on another filesystem.
            descriptor, name = tempfile.mkstemp(prefix=".iip-kit-", dir=output.parent)
            staged = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as destination, candidate.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
                os.link(staged, output)
            finally:
                staged.unlink()
    except InstallationKitError:
        raise
    except FileExistsError:
        raise InstallationKitError("installation-kit.output.exists") from None
    except (OSError, ValueError, tarfile.TarError):
        raise InstallationKitError("installation-kit.build.failed") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--root", type=Path, default=ROOT)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--version", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("archive", type=Path)
    inspect.add_argument("--version", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "build":
            build_installation_kit(arguments.root, arguments.output, arguments.version)
            print("installation-kit.build.complete")
        else:
            print(json.dumps(inspect_installation_kit(arguments.archive, arguments.version), sort_keys=True))
        return 0
    except InstallationKitError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
