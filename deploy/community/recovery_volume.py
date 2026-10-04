"""Networkless, bounded cold-volume archive transport.

This operational helper runs with one stopped named volume mounted at /volume.
Only regular files and directories cross the boundary; USTAR extension records,
links, special files, and privilege-bearing permission bits are not supported.
The host authenticates the encrypted backup and binds exact Docker volume/image
identities before invoking this helper. An archive is never an execution input.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import stat
import sys
import tarfile
import tempfile
from typing import BinaryIO, Iterator


DEFAULT_MAX_BYTES = 8 * 1024**3
HARD_MAX_BYTES = 32 * 1024**3
MAX_USTAR_FILE_BYTES = 8**11 - 1
DEFAULT_MAX_MEMBERS = 200_000
ALLOWED_IDS = frozenset((0, 70, 472, 10001, 65534))
CHUNK_SIZE = 1024 * 1024
ERROR_CODE = "community.recovery.volume.invalid"


class VolumeArchiveError(ValueError):
    """A deliberately value-free public operational failure."""

    def __init__(self) -> None:
        super().__init__(ERROR_CODE)


def _limits(max_bytes: int, max_members: int) -> int:
    if (type(max_bytes) is not int or not 0 <= max_bytes <= HARD_MAX_BYTES
            or type(max_members) is not int or not 1 <= max_members <= DEFAULT_MAX_MEMBERS):
        raise VolumeArchiveError()
    return max_bytes + max_members * 1024 + tarfile.RECORDSIZE


def _name(value: str, *, directory: bool) -> str:
    if directory and value.endswith("/"):
        value = value[:-1]
    if value == "." and directory:
        return value
    if (not value or value.startswith("/") or "\\" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or len(value.encode("utf-8")) > 255 or value.count("/") > 63):
        raise VolumeArchiveError()
    return value


def _metadata(mode: int, uid: int, gid: int) -> None:
    if mode < 0 or mode & ~0o777 or uid not in ALLOWED_IDS or gid not in ALLOWED_IDS:
        raise VolumeArchiveError()


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def _open_root(root: str | os.PathLike[str]) -> int:
    before = os.lstat(root)
    if not stat.S_ISDIR(before.st_mode):
        raise VolumeArchiveError()
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    if _identity(before) != _identity(os.fstat(descriptor)):
        os.close(descriptor)
        raise VolumeArchiveError()
    return descriptor


def export_volume(
    root: str | os.PathLike[str], output: BinaryIO, *,
    max_bytes: int = DEFAULT_MAX_BYTES, max_members: int = DEFAULT_MAX_MEMBERS,
) -> dict[str, int]:
    """Stream a sorted, uncompressed USTAR snapshot of an already stopped volume.

    The root '.' record is included in the member count. File data is bounded by
    max_bytes; header/padding overhead is independently bounded by max_members.
    Sparse files are copied densely and count by logical size. A single USTAR
    file cannot exceed MAX_USTAR_FILE_BYTES, even with a larger aggregate cap.
    An export failure can leave partial output; the host must never publish it.
    """
    _limits(max_bytes, max_members)
    count = 0
    size = 0
    discovered = 1  # Include the volume root before discovering any children.

    def visit(archive: tarfile.TarFile, descriptor: int, name: str) -> None:
        nonlocal count, size, discovered
        before = os.fstat(descriptor)
        directory = stat.S_ISDIR(before.st_mode)
        if not directory and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
            raise VolumeArchiveError()
        _name(name, directory=directory)
        _metadata(stat.S_IMODE(before.st_mode), before.st_uid, before.st_gid)
        count += 1
        size += 0 if directory else before.st_size
        if (count > max_members or size > max_bytes or before.st_size < 0
                or (not directory and before.st_size > MAX_USTAR_FILE_BYTES)):
            raise VolumeArchiveError()
        member = tarfile.TarInfo(name)
        member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
        member.mode = stat.S_IMODE(before.st_mode)
        member.uid, member.gid = before.st_uid, before.st_gid
        member.mtime = int(before.st_mtime)
        if not 0 <= member.mtime < 8**11:
            raise VolumeArchiveError()
        member.size = 0 if directory else before.st_size
        if directory:
            archive.addfile(member)
            children = []
            # Reserve every discovered entry, including pending sibling
            # directories, before sorting. A hostile wide tree must not allocate
            # an unbounded list before the archive's member budget is checked.
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    discovered += 1
                    if discovered > max_members:
                        raise VolumeArchiveError()
                    children.append(entry.name)
            for child in sorted(children):
                child_name = child if name == "." else name + "/" + child
                observed = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(observed.st_mode):
                    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                elif stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1:
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                else:
                    raise VolumeArchiveError()
                child_descriptor = os.open(child, flags, dir_fd=descriptor)
                try:
                    if _identity(observed) != _identity(os.fstat(child_descriptor)):
                        raise VolumeArchiveError()
                    visit(archive, child_descriptor, child_name)
                finally:
                    os.close(child_descriptor)
        else:
            with os.fdopen(os.dup(descriptor), "rb") as source:
                archive.addfile(member, source)
        if _identity(before) != _identity(os.fstat(descriptor)):
            raise VolumeArchiveError()

    try:
        descriptor = _open_root(root)
        try:
            with tarfile.open(fileobj=output, mode="w|", format=tarfile.USTAR_FORMAT,
                              encoding="utf-8", errors="strict") as archive:
                visit(archive, descriptor, ".")
        finally:
            os.close(descriptor)
    except (OSError, tarfile.TarError, UnicodeError, ValueError, OverflowError):
        raise VolumeArchiveError() from None
    return {"members": count, "fileBytes": size}


class _Reader:
    def __init__(self, source: BinaryIO, maximum: int) -> None:
        self.source, self.maximum, self.count = source, maximum, 0
        self.digest = hashlib.sha256()

    def read(self, size: int) -> bytes:
        requested = min(size, self.maximum - self.count + 1)
        value = self.source.read(requested)
        if not isinstance(value, bytes) or len(value) > requested:
            raise VolumeArchiveError()
        self.count += len(value)
        if self.count > self.maximum:
            raise VolumeArchiveError()
        self.digest.update(value)
        return value

    def exact(self, size: int) -> bytes:
        pieces = []
        remaining = size
        while remaining:
            value = self.read(remaining)
            if not value:
                raise VolumeArchiveError()
            pieces.append(value)
            remaining -= len(value)
        return b"".join(pieces)


def _scan(source: BinaryIO, *, max_bytes: int, max_members: int,
          consume=None) -> tuple[dict[str, int], bytes]:
    """Read raw headers so tarfile cannot silently resolve extension records."""
    reader = _Reader(source, _limits(max_bytes, max_members))
    names: dict[str, bool] = {}
    total = 0
    while True:
        header = reader.exact(tarfile.BLOCKSIZE)
        if header == bytes(tarfile.BLOCKSIZE):
            if not names or reader.exact(tarfile.BLOCKSIZE) != bytes(tarfile.BLOCKSIZE):
                raise VolumeArchiveError()
            while trailing := reader.read(CHUNK_SIZE):
                if any(trailing):
                    raise VolumeArchiveError()
            if reader.count % tarfile.BLOCKSIZE:
                raise VolumeArchiveError()
            return {"members": len(names), "fileBytes": total}, reader.digest.digest()
        if (header[257:265] != b"ustar\x0000" or header[156:157] not in (tarfile.REGTYPE, tarfile.DIRTYPE)
                or any(header[157:257]) or any(header[265:329]) or any(header[500:512])):
            raise VolumeArchiveError()
        member = tarfile.TarInfo.frombuf(header, encoding="utf-8", errors="strict")
        # Reject ignored bytes after NUL terminators, alternate numeric/header
        # encodings, and metadata that frombuf would silently normalize away.
        if member.tobuf(format=tarfile.USTAR_FORMAT, encoding="utf-8", errors="strict") != header:
            raise VolumeArchiveError()
        directory = member.type == tarfile.DIRTYPE
        # Check the physical name as well: frombuf normalizes directory slashes.
        raw_name = header[:100].split(b"\0", 1)[0].decode("utf-8")
        prefix = header[345:500].split(b"\0", 1)[0].decode("utf-8")
        if prefix:
            raw_name = prefix + "/" + raw_name
        name = _name(raw_name, directory=directory)
        if name != member.name or name in names or len(names) >= max_members:
            raise VolumeArchiveError()
        _metadata(member.mode, member.uid, member.gid)
        if (not 0 <= member.mtime < 8**11 or member.size < 0 or member.devmajor != 0
                or member.devminor != 0 or (directory and member.size != 0)):
            raise VolumeArchiveError()
        if (not names and (name != "." or not directory)) or (names and name == "."):
            raise VolumeArchiveError()
        if name != ".":
            parent = name.rsplit("/", 1)[0] if "/" in name else "."
            if names.get(parent) is not True:
                raise VolumeArchiveError()
        total += member.size
        if total > max_bytes:
            raise VolumeArchiveError()
        names[name] = directory
        member.name = name
        target = consume(member) if consume else None
        try:
            remaining = member.size
            while remaining:
                value = reader.exact(min(remaining, CHUNK_SIZE))
                if target is not None:
                    target.write(value)
                remaining -= len(value)
            if member.size % tarfile.BLOCKSIZE:
                padding = reader.exact(tarfile.BLOCKSIZE - member.size % tarfile.BLOCKSIZE)
                if any(padding):
                    raise VolumeArchiveError()
        finally:
            if target is not None:
                target.close()


@contextlib.contextmanager
def _source(value: BinaryIO | str | os.PathLike[str]) -> Iterator[BinaryIO]:
    if isinstance(value, (str, os.PathLike)):
        descriptor = os.open(value, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or observed.st_mode & 0o077:
                raise VolumeArchiveError()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                yield stream
        finally:
            os.close(descriptor)
    else:
        yield value


def inspect_archive(
    archive: BinaryIO | str | os.PathLike[str], *,
    max_bytes: int = DEFAULT_MAX_BYTES, max_members: int = DEFAULT_MAX_MEMBERS,
) -> dict[str, int]:
    """Fully inspect the current stream, or a protected regular archive path."""
    try:
        with _source(archive) as source:
            return _scan(source, max_bytes=max_bytes, max_members=max_members)[0]
    except (OSError, tarfile.TarError, UnicodeError, ValueError, OverflowError):
        raise VolumeArchiveError() from None


def _parent(root: int, name: str) -> tuple[int, str]:
    pieces = name.split("/")
    descriptor = os.dup(root)
    try:
        for piece in pieces[:-1]:
            child = os.open(piece, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, pieces[-1]
    except BaseException:
        os.close(descriptor)
        raise


class _RestoredFile:
    def __init__(self, descriptor: int, member: tarfile.TarInfo) -> None:
        self.stream = os.fdopen(descriptor, "wb")
        self.member = member

    def write(self, value: bytes) -> None:
        self.stream.write(value)

    def close(self) -> None:
        try:
            self.stream.flush()
            descriptor = self.stream.fileno()
            os.fchown(descriptor, self.member.uid, self.member.gid)
            os.fchmod(descriptor, self.member.mode)
            os.utime(descriptor, (self.member.mtime, self.member.mtime))
            os.fsync(descriptor)
        finally:
            self.stream.close()


def import_volume(
    root: str | os.PathLike[str], archive: BinaryIO | str | os.PathLike[str], *,
    max_bytes: int = DEFAULT_MAX_BYTES, max_members: int = DEFAULT_MAX_MEMBERS,
) -> dict[str, int]:
    """Validate all input before restoring only into an empty, exact volume root.

    A non-seekable input is spooled privately in /tmp. Mounted read-only archive
    paths avoid that copy. The validation and restoration passes must match
    byte-for-byte by a streaming SHA-256, not only by member/byte counts. Files
    use O_EXCL/O_NOFOLLOW and manual writes, never tar extraction. On an I/O
    failure or source mutation the owned destination remains diagnostic;
    this helper never deletes data or retries over a partially restored volume.
    """
    maximum = _limits(max_bytes, max_members)
    try:
        with _source(archive) as source, contextlib.ExitStack() as stack:
            if not source.seekable():
                spool = stack.enter_context(tempfile.TemporaryFile(mode="w+b", dir="/tmp"))
                reader = _Reader(source, maximum)
                while value := reader.read(CHUNK_SIZE):
                    spool.write(value)
                spool.seek(0)
                source = spool
            start = source.tell()
            expected = _scan(source, max_bytes=max_bytes, max_members=max_members)
            source.seek(start)
            descriptor = _open_root(root)
            stack.callback(os.close, descriptor)
            with os.scandir(descriptor) as entries:
                if next(entries, None) is not None:
                    raise VolumeArchiveError()
            os.fchmod(descriptor, 0o700)
            directories: list[tarfile.TarInfo] = []

            def consume(member: tarfile.TarInfo) -> _RestoredFile | None:
                if member.isdir():
                    if member.name != ".":
                        parent, filename = _parent(descriptor, member.name)
                        try:
                            os.mkdir(filename, mode=0o700, dir_fd=parent)
                        finally:
                            os.close(parent)
                    directories.append(member)
                    return None
                parent, filename = _parent(descriptor, member.name)
                try:
                    target = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                finally:
                    os.close(parent)
                try:
                    stream = _RestoredFile(target, member)
                except BaseException:
                    os.close(target)
                    raise
                return stream

            result = _scan(source, max_bytes=max_bytes, max_members=max_members, consume=consume)
            if result != expected:
                raise VolumeArchiveError()
            for member in reversed(directories):
                if member.name == ".":
                    child = os.dup(descriptor)
                else:
                    parent, filename = _parent(descriptor, member.name)
                    try:
                        child = os.open(filename, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    finally:
                        os.close(parent)
                try:
                    os.fchown(child, member.uid, member.gid)
                    os.fchmod(child, 0o700 if member.name == "." else member.mode)
                    os.utime(child, (member.mtime, member.mtime))
                    os.fsync(child)
                finally:
                    os.close(child)
            return result[0]
    except (OSError, tarfile.TarError, UnicodeError, ValueError, OverflowError):
        raise VolumeArchiveError() from None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, ERROR_CODE + "\n")


def main() -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("operation", choices=("export", "import", "check"))
    parser.add_argument("--archive", choices=("/archive",))
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-members", type=int, default=DEFAULT_MAX_MEMBERS)
    arguments = parser.parse_args()
    try:
        limits = {"max_bytes": arguments.max_bytes, "max_members": arguments.max_members}
        if arguments.operation == "export":
            if arguments.archive:
                raise VolumeArchiveError()
            export_volume("/volume", sys.stdout.buffer, **limits)
        else:
            source = arguments.archive or sys.stdin.buffer
            report = (import_volume("/volume", source, **limits) if arguments.operation == "import"
                      else inspect_archive(source, **limits))
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (VolumeArchiveError, BrokenPipeError):
        print(ERROR_CODE, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
