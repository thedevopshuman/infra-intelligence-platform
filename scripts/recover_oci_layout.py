#!/usr/bin/env python3
"""Validate and repack a complete local OCI layout without fetching or extracting.

The resulting deterministic archive is new packaging of the published blobs;
it does not reproduce or claim the bytes of a lost original archive.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO, Callable, Mapping

if __package__:
    from .release_bundle import IN_TOTO, OCI_INDEX, OCI_MANIFEST, ReleaseBundleError, inspect_oci_image
else:
    from release_bundle import IN_TOTO, OCI_INDEX, OCI_MANIFEST, ReleaseBundleError, inspect_oci_image


CONFIG = "application/vnd.oci.image.config.v1+json"
EMPTY = "application/vnd.oci.empty.v1+json"
LAYERS = frozenset({"application/vnd.oci.image.layer.v1.tar" + suffix for suffix in ("", "+gzip", "+zstd")})
MEDIA_TYPES = LAYERS | {OCI_INDEX, OCI_MANIFEST, CONFIG, EMPTY, IN_TOTO}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_FILES = 4096
MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_BLOB_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
PLATFORMS = frozenset({"linux/amd64", "linux/arm64"})


class RecoveryError(RuntimeError):
    """Stable diagnostics without untrusted registry content or local paths."""


def fail(code: str) -> None:
    raise RecoveryError("release.recovery." + code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail("json.duplicate-key")
        result[key] = value
    return result


def _json(content: bytes) -> dict[str, Any]:
    try:
        document = json.loads(content, object_pairs_hook=_pairs,
                              parse_constant=lambda _: fail("json.invalid"))
    except (ValueError, UnicodeError, RecursionError):
        fail("json.invalid")
    if not isinstance(document, dict):
        fail("json.invalid")
    return document


def _directory(parent: int | None, name: str) -> int:
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    return descriptor


def _names(directory: int) -> set[str]:
    names: set[str] = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(names) >= MAX_FILES or entry.name in names:
                fail("layout.file-limit")
            names.add(entry.name)
    return names


def _open_file(directory: int, name: str) -> BinaryIO:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        os.close(descriptor)
        fail("layout.regular-unlinked-file-required")
    if status.st_size > MAX_BLOB_BYTES:
        os.close(descriptor)
        fail("layout.blob-limit")
    return os.fdopen(descriptor, "rb")


def _read_json(directory: int, name: str) -> tuple[dict[str, Any], int, str]:
    with _open_file(directory, name) as handle:
        return _read_json_handle(handle)


def _read_json_handle(handle: BinaryIO) -> tuple[dict[str, Any], int, str]:
    content = handle.read(MAX_METADATA_BYTES + 1)
    if len(content) > MAX_METADATA_BYTES:
        fail("json.byte-limit")
    return _json(content), len(content), hashlib.sha256(content).hexdigest()


class _HashingReader:
    def __init__(self, handle: BinaryIO) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        content = self.handle.read(size)
        self.digest.update(content)
        self.size += len(content)
        return content


def _descriptor(value: Any) -> tuple[str, str, int]:
    if not isinstance(value, dict) or set(value) - {
        "digest", "size", "mediaType", "platform", "annotations", "artifactType", "data",
    }:
        fail("descriptor.invalid-or-external")
    digest, media, size = value.get("digest"), value.get("mediaType"), value.get("size")
    if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
        fail("descriptor.digest-invalid")
    if media not in MEDIA_TYPES:
        fail("descriptor.media-unsupported")
    if type(size) is not int or not 0 <= size <= MAX_BLOB_BYTES:
        fail("descriptor.size-invalid")
    if "data" in value:
        # OCI permits embedded base64 content (Buildx uses it for empty config).
        # It is not a replacement for the complete on-disk blob graph below.
        encoded = value["data"]
        if (not isinstance(encoded, str) or size > MAX_METADATA_BYTES
                or len(encoded) > ((MAX_METADATA_BYTES + 2) // 3) * 4):
            fail("descriptor.inline-data-invalid")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            fail("descriptor.inline-data-invalid")
        if (len(content) != size or base64.b64encode(content).decode("ascii") != encoded
                or "sha256:" + hashlib.sha256(content).hexdigest() != digest):
            fail("descriptor.inline-data-mismatch")
    for key in ("annotations", "platform"):
        if key in value and not isinstance(value[key], dict):
            fail("descriptor.invalid-or-external")
    return digest, media, size


def _validate_graph(open_blob: Callable[[str], BinaryIO], sizes: Mapping[str, int],
                    root: dict[str, Any]) -> dict[str, tuple[str, int]]:
    visited: dict[str, tuple[str, int]] = {}
    pending: list[tuple[dict, int]] = [(root, 0)]
    while pending:
        descriptor, depth = pending.pop()
        if depth > 16:
            fail("graph.depth-limit")
        digest, media, size = _descriptor(descriptor)
        name = digest.removeprefix("sha256:")
        previous = visited.get(name)
        if previous is not None:
            if previous != (media, size):
                fail("descriptor.inconsistent")
            continue
        if name not in sizes:
            fail("graph.blob-missing")
        with open_blob(name) as handle:
            if sizes[name] != size:
                fail("descriptor.size-mismatch")
            checksum = hashlib.sha256()
            content = bytearray()
            remaining = size
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    fail("descriptor.size-mismatch")
                remaining -= len(chunk)
                checksum.update(chunk)
                if media not in LAYERS:
                    if len(content) + len(chunk) > MAX_METADATA_BYTES:
                        fail("json.byte-limit")
                    content.extend(chunk)
            if handle.read(1):
                fail("descriptor.size-mismatch")
            if checksum.hexdigest() != name:
                fail("graph.digest-mismatch")
        visited[name] = (media, size)
        if media in LAYERS:
            continue
        document = _json(bytes(content))
        children: list[dict] = []
        if media in (OCI_INDEX, OCI_MANIFEST):
            if document.get("schemaVersion") != 2 or document.get("mediaType", media) != media:
                fail("graph.document-invalid")
            if "subject" in document:
                children.append(document["subject"])
        if media == OCI_INDEX:
            manifests = document.get("manifests")
            if not isinstance(manifests, list) or not 1 <= len(manifests) <= MAX_FILES:
                fail("graph.index-invalid")
            for child in manifests:
                if _descriptor(child)[1] not in (OCI_INDEX, OCI_MANIFEST):
                    fail("graph.index-child-invalid")
            children.extend(manifests)
        elif media == OCI_MANIFEST:
            config = document.get("config")
            if _descriptor(config)[1] not in (CONFIG, EMPTY):
                fail("graph.config-media-invalid")
            layers = document.get("layers")
            if not isinstance(layers, list) or len(layers) > MAX_FILES:
                fail("graph.layers-invalid")
            for layer in layers:
                if _descriptor(layer)[1] not in LAYERS | {IN_TOTO}:
                    fail("graph.layer-media-invalid")
            children.extend([config, *layers])
        pending.extend((child, depth + 1) for child in children)
        if len(pending) > MAX_FILES * 4:
            fail("graph.descriptor-limit")
    if set(visited) != set(sizes):
        fail("graph.unreferenced-blob")
    return visited


def _layout_index(layout: dict[str, Any], index: dict[str, Any], expected_digest: str) -> dict:
    if layout != {"imageLayoutVersion": "1.0.0"}:
        fail("layout.version-invalid")
    manifests = index.get("manifests")
    if (set(index) - {"schemaVersion", "mediaType", "manifests", "annotations"}
            or index.get("schemaVersion") != 2 or index.get("mediaType", OCI_INDEX) != OCI_INDEX
            or not isinstance(manifests, list) or len(manifests) != 1):
        fail("layout.single-index-required")
    digest, media, _ = _descriptor(manifests[0])
    if digest != expected_digest or media != OCI_INDEX:
        fail("layout.expected-index-mismatch")
    return manifests[0]


def _inspect_attestations(archive: Path, expected_digest: str) -> Mapping[str, Any]:
    try:
        result = inspect_oci_image(archive)
    except ReleaseBundleError:
        fail("image.attestation-or-platform-invalid")
    if result["indexDigest"] != expected_digest or {item["name"] for item in result["platforms"]} != PLATFORMS:
        fail("image.exact-platforms-required")
    return result


def verify_archive(archive: Path, expected_digest: str) -> Mapping[str, Any]:
    """Revalidate a recovered uncompressed OCI tar checkpoint without extraction.

    The outer archive checksum is not a substitute for this complete embedded
    graph validation. Only the regular members emitted by this packer are read.
    """
    if not isinstance(expected_digest, str) or DIGEST.fullmatch(expected_digest) is None:
        fail("expected-index.invalid")
    archive = Path(archive)
    try:
        descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size > MAX_TOTAL_BYTES + 2 * MAX_METADATA_BYTES + (MAX_FILES + 2) * 1024 + 10240):
                fail("archive.regular-bounded-file-required")
            with tarfile.open(fileobj=source, mode="r:") as packed:
                members: dict[str, tarfile.TarInfo] = {}
                sizes: dict[str, int] = {}
                total, last_end = 0, 0
                for member in packed:
                    name = member.name
                    if (len(members) >= MAX_FILES + 2 or name in members
                            or member.type != tarfile.REGTYPE or member.pax_headers
                            or member.offset_data - member.offset != 512
                            or not 0 <= member.size <= MAX_BLOB_BYTES):
                        fail("archive.members-invalid")
                    if name not in {"index.json", "oci-layout"}:
                        match = re.fullmatch(r"blobs/sha256/([0-9a-f]{64})", name)
                        if match is None:
                            fail("archive.member-path-invalid")
                        sizes[match.group(1)] = member.size
                        total += member.size
                        if total > MAX_TOTAL_BYTES:
                            fail("layout.total-byte-limit")
                    elif member.size > MAX_METADATA_BYTES:
                        fail("json.byte-limit")
                    members[name] = member
                    last_end = member.offset_data + ((member.size + 511) // 512) * 512
                if not sizes or not {"index.json", "oci-layout"}.issubset(members):
                    fail("archive.required-members-missing")
                # Reject hidden concatenated/trailing archives and non-zero tails.
                expected_size = ((last_end + 1024 + 10239) // 10240) * 10240
                source.seek(last_end)
                if before.st_size != expected_size or any(source.read(11264)):
                    fail("archive.termination-invalid")

                def open_member(name: str) -> BinaryIO:
                    handle = packed.extractfile(members[name])
                    if handle is None:
                        fail("archive.member-invalid")
                    return handle

                with open_member("oci-layout") as handle:
                    layout, _, _ = _read_json_handle(handle)
                with open_member("index.json") as handle:
                    index, _, _ = _read_json_handle(handle)
                root = _layout_index(layout, index, expected_digest)
                _validate_graph(lambda name: open_member("blobs/sha256/" + name), sizes, root)
            result = _inspect_attestations(archive, expected_digest)
            after = os.stat(archive, follow_symlinks=False)
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if identity(before) != identity(after) or not stat.S_ISREG(after.st_mode):
                fail("archive.changed-during-verification")
            return result
    except (OSError, tarfile.TarError, ValueError, KeyError, TypeError, RecursionError):
        fail("archive.invalid")


def recover_layout(layout: Path, expected_digest: str, output: Path) -> Mapping[str, Any]:
    """Validate all bytes, then exclusively publish one deterministic OCI tar.

    Symlink-free directory-relative descriptors confine reads to the supplied
    layout. Packaging re-hashes every file to detect changes after validation.
    No layer is decompressed and no archive is extracted or executed.
    """
    if not isinstance(expected_digest, str) or DIGEST.fullmatch(expected_digest) is None:
        fail("expected-index.invalid")
    layout, output = Path(layout).absolute(), Path(output).absolute()
    if output.is_relative_to(layout):
        fail("output.outside-layout-required")
    if output.exists() or output.is_symlink():
        fail("output.exists")
    temporary: Path | None = None
    try:
        with ExitStack() as stack:
            root = _directory(None, str(layout))
            stack.callback(os.close, root)
            if _names(root) != {"index.json", "oci-layout", "blobs"}:
                fail("layout.members-invalid")
            blob_parent = _directory(root, "blobs")
            stack.callback(os.close, blob_parent)
            if _names(blob_parent) != {"sha256"}:
                fail("layout.algorithm-invalid")
            blobs = _directory(blob_parent, "sha256")
            stack.callback(os.close, blobs)
            available = _names(blobs)
            if not available or any(re.fullmatch(r"[0-9a-f]{64}", name) is None for name in available):
                fail("layout.blob-name-invalid")
            total = 0
            sizes = {}
            for name in available:
                with _open_file(blobs, name) as handle:
                    sizes[name] = os.fstat(handle.fileno()).st_size
                    total += sizes[name]
                    if total > MAX_TOTAL_BYTES:
                        fail("layout.total-byte-limit")
            layout_document, layout_size, layout_hash = _read_json(root, "oci-layout")
            index, index_size, index_hash = _read_json(root, "index.json")
            root_descriptor = _layout_index(layout_document, index, expected_digest)
            graph = _validate_graph(lambda name: _open_file(blobs, name), sizes, root_descriptor)
            records = [("index.json", root, "index.json", index_size, index_hash),
                       ("oci-layout", root, "oci-layout", layout_size, layout_hash)]
            records.extend((f"blobs/sha256/{name}", blobs, name, size, name)
                           for name, (_, size) in sorted(graph.items()))
            descriptor, temporary_name = tempfile.mkstemp(prefix=".iip-oci-recovery-", suffix=".tar", dir=output.parent)
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as target:
                with tarfile.open(fileobj=target, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    for archive_name, directory, name, size, checksum in records:
                        with _open_file(directory, name) as handle:
                            if os.fstat(handle.fileno()).st_size != size:
                                fail("layout.changed-during-packaging")
                            member = tarfile.TarInfo(archive_name)
                            member.size, member.mode = size, 0o644
                            member.uid = member.gid = member.mtime = 0
                            reader = _HashingReader(handle)
                            archive.addfile(member, reader)
                            if (reader.size != size or reader.digest.hexdigest() != checksum
                                    or handle.read(1)):
                                fail("layout.changed-during-packaging")
                target.flush()
                os.fsync(target.fileno())
            result = verify_archive(temporary, expected_digest)
            # A hard-link publication is atomic and cannot overwrite a racing file.
            os.link(temporary, output, follow_symlinks=False)
            return result
    except FileExistsError:
        fail("output.exists")
    except (OSError, tarfile.TarError, ValueError, KeyError, TypeError, RecursionError):
        fail("layout-or-output.invalid")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                fail("temporary-cleanup.failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", required=True, type=Path)
    parser.add_argument("--expected-index-digest", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = recover_layout(args.layout, args.expected_index_digest, args.output)
    except RecoveryError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    print("Recovered OCI archive contains verified published blobs; archive packaging is newly generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
