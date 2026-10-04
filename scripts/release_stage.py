#!/usr/bin/env python3
"""Create and restore exact, source-bound release-bundle checkpoints.

Checkpoints are transport integrity, not publisher signatures or promotion
evidence. Trusted archive and manifest hashes must come from the owning run.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zlib
from typing import Any, BinaryIO, Mapping

from jsonschema import Draft202012Validator, FormatChecker

if __package__:
    from . import release_bundle
else:
    import release_bundle


ROOT = Path(__file__).resolve().parents[1]
HEX = re.compile(r"[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{40,64}")
MAX_FILES = 11
MAX_METADATA_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + MAX_FILES * 1024 + 1024 * 1024
MANIFEST = "release-manifest.json"
CHECKSUMS = "SHA256SUMS"


class StageError(RuntimeError):
    """Stable errors; never print paths or untrusted archive/process contents."""


def fail(code: str) -> None:
    raise StageError("release-stage." + code)


def _git(*arguments: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(ROOT), *arguments],
                                capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        fail("source.unavailable")
    if result.returncode:
        fail("source.unavailable")
    return result.stdout.strip()


def _source_identity(revision: str) -> str:
    if not isinstance(revision, str) or REVISION.fullmatch(revision) is None:
        fail("revision.invalid")
    if _git("rev-parse", "HEAD") != revision:
        fail("source.revision-mismatch")
    if _git("status", "--porcelain=v1", "--untracked-files=normal"):
        fail("source.dirty")
    try:
        version = tomllib.loads(_git("show", f"{revision}:pyproject.toml"))["project"]["version"]
    except (KeyError, TypeError, ValueError):
        fail("source.version-invalid")
    if not isinstance(version, str) or release_bundle.SEMVER.fullmatch(version) is None:
        fail("source.version-invalid")
    return version


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    document = {}
    for key, value in pairs:
        if key in document:
            fail("manifest.duplicate-key")
        document[key] = value
    return document


def _manifest(content: bytes, revision: str, version: str) -> tuple[dict, dict[str, dict]]:
    if len(content) > MAX_METADATA_BYTES:
        fail("manifest.byte-limit")
    try:
        document = json.loads(content, object_pairs_hook=_pairs,
                              parse_constant=lambda _: fail("manifest.invalid"))
        schema = json.loads((ROOT / "contracts/schemas/release-manifest.schema.json").read_text())
    except (ValueError, UnicodeError, RecursionError):
        fail("manifest.invalid")
    if next(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document), None) is not None:
        fail("manifest.invalid")
    metadata = document["metadata"]
    if metadata["revision"] != revision or metadata["version"] != version:
        fail("manifest.source-mismatch")
    records = {item["path"]: item for item in document["spec"]["artifacts"]}
    if len(records) != len(document["spec"]["artifacts"]) or set(records) & {MANIFEST, CHECKSUMS}:
        fail("manifest.inventory-invalid")
    if any(not 1 <= item["sizeBytes"] <= MAX_ARTIFACT_BYTES for item in records.values()):
        fail("manifest.artifact-byte-limit")
    return document, records


def _open_regular(path: Path, maximum: int = MAX_ARTIFACT_BYTES) -> BinaryIO:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    status = os.fstat(descriptor)
    if (not stat.S_ISREG(status.st_mode) or status.st_nlink != 1
            or not 1 <= status.st_size <= maximum):
        os.close(descriptor)
        fail("file.regular-bounded-single-link-required")
    return os.fdopen(descriptor, "rb")


def _hash(handle: BinaryIO, maximum: int) -> str:
    digest, size = hashlib.sha256(), 0
    while True:
        chunk = handle.read(min(1024 * 1024, maximum - size + 1))
        if not chunk:
            return digest.hexdigest()
        size += len(chunk)
        if size > maximum:
            fail("file.byte-limit")
        digest.update(chunk)


def _prefix(version: str, revision: str) -> str:
    return f"iip-{version}-{revision[:12]}"


def _closed_bundle(bundle: Path, revision: str, version: str) -> tuple[dict, dict[str, dict], str]:
    if not stat.S_ISDIR(bundle.lstat().st_mode):
        fail("bundle.directory-required")
    with _open_regular(bundle / MANIFEST, MAX_METADATA_BYTES) as handle:
        content = handle.read(MAX_METADATA_BYTES + 1)
    manifest, records = _manifest(content, revision, version)
    expected = set(records) | {MANIFEST, CHECKSUMS}
    names = set()
    with os.scandir(bundle) as entries:
        for entry in entries:
            if len(names) >= MAX_FILES or entry.name in names:
                fail("bundle.file-limit")
            names.add(entry.name)
    if names != expected:
        fail("bundle.inventory-mismatch")
    total = 0
    for name in names:
        limit = MAX_METADATA_BYTES if name in (MANIFEST, CHECKSUMS) else MAX_ARTIFACT_BYTES
        with _open_regular(bundle / name, limit) as handle:
            size = os.fstat(handle.fileno()).st_size
            if name in records and size != records[name]["sizeBytes"]:
                fail("bundle.artifact-size-mismatch")
            total += size
    if total > MAX_TOTAL_BYTES:
        fail("bundle.total-byte-limit")
    try:
        verified = release_bundle.verify_bundle(bundle)
    except release_bundle.ReleaseBundleError:
        fail("bundle.verification-failed")
    if verified != manifest:
        fail("bundle.changed")
    return manifest, records, hashlib.sha256(content).hexdigest()


class _HashingReader:
    def __init__(self, handle: BinaryIO) -> None:
        self.handle, self.digest = handle, hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        content = self.handle.read(size)
        self.digest.update(content)
        return content


def _result(archive: Path, archive_hash: str, manifest_hash: str, bundle: Path, manifest: Mapping[str, Any]) -> dict:
    return {"archive": str(archive), "fileSha256": archive_hash, "manifestSha256": manifest_hash,
            "bundle": str(bundle), "version": manifest["metadata"]["version"],
            "chartVersion": manifest["metadata"]["chartVersion"]}


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def pack_bundle(bundle: Path, output: Path, revision: str) -> dict:
    temporary = None
    try:
        version = _source_identity(revision)
        bundle, output = Path(bundle).absolute(), Path(output).absolute()
        prefix = _prefix(version, revision)
        if output.name != prefix + ".tar.gz":
            fail("archive.basename-mismatch")
        if output.parent.resolve().is_relative_to(bundle.resolve()):
            fail("archive.outside-bundle-required")
        if output.exists() or output.is_symlink():
            fail("output.exists")
        manifest, records, manifest_hash = _closed_bundle(bundle, revision, version)
        hashes = {name: item["sha256"] for name, item in records.items()}
        hashes[MANIFEST] = manifest_hash
        with _open_regular(bundle / CHECKSUMS, MAX_METADATA_BYTES) as handle:
            hashes[CHECKSUMS] = _hash(handle, MAX_METADATA_BYTES)
        descriptor, name = tempfile.mkstemp(prefix=".iip-release-stage-", dir=output.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=6) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as packed:
                    # The independently trusted manifest is always read first on restore.
                    for name in [MANIFEST, CHECKSUMS, *sorted(records)]:
                        with _open_regular(bundle / name) as handle:
                            member = tarfile.TarInfo(f"{prefix}/{name}")
                            member.size = os.fstat(handle.fileno()).st_size
                            member.mode = 0o644
                            member.uid = member.gid = member.mtime = 0
                            reader = _HashingReader(handle)
                            packed.addfile(member, reader)
                            if reader.digest.hexdigest() != hashes[name] or handle.read(1):
                                fail("bundle.changed")
            raw.flush()
            os.fsync(raw.fileno())
        if _source_identity(revision) != version:
            fail("source.changed")
        with _open_regular(temporary, MAX_ARCHIVE_BYTES) as handle:
            archive_hash = _hash(handle, MAX_ARCHIVE_BYTES)
        os.link(temporary, output, follow_symlinks=False)
        _sync_directory(output.parent)
        return _result(output, archive_hash, manifest_hash, bundle, manifest)
    except FileExistsError:
        fail("output.exists")
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, EOFError, zlib.error, RecursionError):
        fail("pack.failed")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                fail("temporary-cleanup.failed")


def _exact_read(handle: BinaryIO, size: int) -> bytes:
    content = handle.read(size)
    if len(content) != size:
        fail("archive.truncated")
    return content


def _unpack_stream(source: BinaryIO, staged: Path, revision: str, version: str,
                   manifest_hash: str) -> dict:
    prefix, names, total = _prefix(version, revision), set(), 0
    records: dict[str, dict] = {}
    manifest = None
    consumed = 0
    with gzip.GzipFile(fileobj=source, mode="rb") as compressed:
        while True:
            header = _exact_read(compressed, 512)
            consumed += 512
            if header == bytes(512):
                if _exact_read(compressed, 512) != bytes(512):
                    fail("archive.termination-invalid")
                consumed += 512
                padding = (-consumed) % 10240
                if any(_exact_read(compressed, padding)) or compressed.read(1):
                    fail("archive.trailing-content")
                break
            member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
            if (member.type != tarfile.REGTYPE or member.pax_headers or member.size < 1
                    or len(names) >= MAX_FILES or member.name in names):
                fail("archive.member-invalid")
            filename = member.name.removeprefix(prefix + "/")
            if member.name != prefix + "/" + filename or "/" in filename or filename in ("", ".", ".."):
                fail("archive.path-invalid")
            if manifest is None and filename != MANIFEST:
                fail("archive.manifest-first-required")
            if manifest is not None and filename not in set(records) | {CHECKSUMS}:
                fail("archive.inventory-mismatch")
            maximum = MAX_METADATA_BYTES if filename in (MANIFEST, CHECKSUMS) else MAX_ARTIFACT_BYTES
            if member.size > maximum:
                fail("archive.member-byte-limit")
            total += member.size
            if total > MAX_TOTAL_BYTES:
                fail("archive.total-byte-limit")
            if filename == MANIFEST:
                content = _exact_read(compressed, member.size)
                if hashlib.sha256(content).hexdigest() != manifest_hash:
                    fail("manifest.trusted-digest-mismatch")
                manifest, records = _manifest(content, revision, version)
                with (staged / filename).open("xb") as target:
                    os.chmod(staged / filename, 0o600)
                    target.write(content)
                    target.flush()
                    os.fsync(target.fileno())
            else:
                if filename in records and member.size != records[filename]["sizeBytes"]:
                    fail("archive.artifact-size-mismatch")
                digest, remaining = hashlib.sha256(), member.size
                with (staged / filename).open("xb") as target:
                    os.chmod(staged / filename, 0o600)
                    while remaining:
                        chunk = _exact_read(compressed, min(1024 * 1024, remaining))
                        remaining -= len(chunk)
                        target.write(chunk)
                        digest.update(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if filename in records and digest.hexdigest() != records[filename]["sha256"]:
                    fail("archive.artifact-digest-mismatch")
            consumed += member.size
            padding = (-member.size) % 512
            if any(_exact_read(compressed, padding)):
                fail("archive.padding-invalid")
            consumed += padding
            names.add(member.name)
    if manifest is None or names != {prefix + "/" + name for name in set(records) | {MANIFEST, CHECKSUMS}}:
        fail("archive.inventory-mismatch")
    return manifest


def _publish_directory(staged: Path, destination: Path) -> None:
    # Exclusive reservation, never rename over even an empty existing directory.
    destination.mkdir(mode=0o700)
    descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        marker = os.open(".stage-incomplete", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o600, dir_fd=descriptor)
        os.close(marker)
        os.fsync(descriptor)
        # SHA256SUMS last ensures even the legacy bundle verifier fails during publication.
        for path in sorted(staged.iterdir(), key=lambda path: (path.name == CHECKSUMS, path.name)):
            os.link(path, path.name, dst_dir_fd=descriptor, follow_symlinks=False)
            path.unlink()
        os.fsync(descriptor)
        try:
            release_bundle.verify_bundle(destination)
        except release_bundle.ReleaseBundleError:
            fail("output.verification-failed")
        os.unlink(".stage-incomplete", dir_fd=descriptor)
        os.fsync(descriptor)
        _sync_directory(destination.parent)
    finally:
        os.close(descriptor)


def unpack_bundle(archive: Path, expected_sha256: str, expected_manifest_sha256: str,
                  revision: str, output_parent: Path) -> dict:
    try:
        if any(not isinstance(value, str) or HEX.fullmatch(value) is None
               for value in (expected_sha256, expected_manifest_sha256)):
            fail("trusted-digest.invalid")
        version = _source_identity(revision)
        archive, output_parent = Path(archive).absolute(), Path(output_parent).absolute()
        prefix = _prefix(version, revision)
        if archive.name != prefix + ".tar.gz":
            fail("archive.basename-mismatch")
        destination = output_parent / prefix
        if destination.exists() or destination.is_symlink():
            fail("output.exists")
        with _open_regular(archive, MAX_ARCHIVE_BYTES) as source:
            # No gzip or tar parsing is performed before this trusted digest check.
            if _hash(source, MAX_ARCHIVE_BYTES) != expected_sha256:
                fail("archive.trusted-digest-mismatch")
            source.seek(0)
            with tempfile.TemporaryDirectory(prefix=".iip-release-stage-", dir=output_parent) as temporary:
                staged = Path(temporary) / prefix
                staged.mkdir(mode=0o700)
                manifest = _unpack_stream(source, staged, revision, version, expected_manifest_sha256)
                checked, _, manifest_hash = _closed_bundle(staged, revision, version)
                if checked != manifest or manifest_hash != expected_manifest_sha256:
                    fail("manifest.changed")
                source.seek(0)
                if _hash(source, MAX_ARCHIVE_BYTES) != expected_sha256:
                    fail("archive.changed")
                if _source_identity(revision) != version:
                    fail("source.changed")
                _publish_directory(staged, destination)
        return _result(archive, expected_sha256, expected_manifest_sha256, destination, manifest)
    except FileExistsError:
        fail("output.exists")
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, EOFError, zlib.error, RecursionError):
        fail("unpack.failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("pack")
    pack.add_argument("--bundle", required=True, type=Path)
    pack.add_argument("--output", required=True, type=Path)
    pack.add_argument("--revision", required=True)
    unpack = commands.add_parser("unpack")
    unpack.add_argument("--archive", required=True, type=Path)
    unpack.add_argument("--expected-sha256", required=True)
    unpack.add_argument("--expected-manifest-sha256", required=True)
    unpack.add_argument("--revision", required=True)
    unpack.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "pack":
            result = pack_bundle(args.bundle, args.output, args.revision)
        else:
            result = unpack_bundle(args.archive, args.expected_sha256, args.expected_manifest_sha256,
                                   args.revision, args.output)
    except StageError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
