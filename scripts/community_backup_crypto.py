"""Bounded authenticated encryption for owner-controlled community backups.

Keys are exactly 32 random binary bytes, not passwords or JSON. Envelope v1 is
``b'IIPBACKUP\\x00\\x01' || nonce[12] || ciphertext[N] || GCM-tag[16]``.
The entire magic/version plus nonce header is authenticated as additional data.
Each encryption obtains a fresh random nonce; callers cannot supply one. The
format reveals payload length but contains no filenames, tenant IDs, or keys.

This operational helper uses streaming AES-256-GCM because archives need not
fit in memory. Unauthenticated plaintext stays in a private staging file and
is never published or consumed before final tag verification. Atomic publication
requires same-filesystem hard links; no destination is ever overwritten. Disk
failure/process death can leave private staging files, so encryption is not a
secure-erasure promise. Keep key custody separate from archive custody.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import stat
from typing import BinaryIO, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"IIPBACKUP\x00\x01"
NONCE_BYTES = 12
TAG_BYTES = 16
HEADER_BYTES = len(MAGIC) + NONCE_BYTES
ENVELOPE_OVERHEAD = HEADER_BYTES + TAG_BYTES
DEFAULT_MAX_BYTES = 8 * 1024 ** 3
MAX_ENVELOPE_BYTES = 32 * 1024 ** 3
MAX_PLAINTEXT_BYTES = MAX_ENVELOPE_BYTES - ENVELOPE_OVERHEAD
CHUNK_BYTES = 1024 * 1024


class BackupCryptoError(ValueError):
    """Stable error code; never includes a path, secret, or payload value."""


def _error(code: str) -> BackupCryptoError:
    return BackupCryptoError(f"community.backup.crypto.{code}")


def _limit(max_bytes: int) -> int:
    if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_PLAINTEXT_BYTES:
        raise _error("limit.invalid")
    return max_bytes


@contextmanager
def _parent(path: Path) -> Iterator[tuple[int, str]]:
    # Walk with descriptors rather than resolve(), which would accept symlinks.
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw.startswith("/") or "\x00" in raw:
        raise _error("path.invalid")
    parts = raw.split("/")[1:]
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise _error("path.invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for part in parts[:-1]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
            raise _error("directory.unprotected")
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _file_info(descriptor: int, *, maximum: int) -> os.stat_result:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
    ):
        raise _error("file.unprotected")
    if info.st_size > maximum:
        raise _error("size.exceeded")
    return info


def _unchanged(descriptor: int, before: os.stat_result, *, maximum: int) -> None:
    after = _file_info(descriptor, maximum=maximum)
    if (
        _identity(before) != _identity(after)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise _error("source.changed")


@contextmanager
def _source(path: Path, *, maximum: int) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    with _parent(path) as (directory, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = _file_info(descriptor, maximum=maximum)
            stream = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            yield stream, info


def _key(path: Path) -> bytes:
    with _source(path, maximum=32) as (stream, info):
        key = stream.read(33)
        if len(key) != 32:
            raise _error("key.invalid")
        _unchanged(stream.fileno(), info, maximum=32)
        return key


def _remove_owned(directory: int, name: str, identity: tuple[int, int]) -> None:
    """Remove only this operation's inode, never a replaced path or symlink."""
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode) and _identity(info) == identity:
            os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def _absent(directory: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise _error("destination.exists")


@contextmanager
def _staged(destination: Path) -> Iterator[BinaryIO]:
    with _parent(destination) as (directory, name):
        _absent(directory, name)
        staging = f".iip-backup-{secrets.token_hex(16)}.tmp"
        descriptor = os.open(
            staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=directory,
        )
        identity = _identity(os.fstat(descriptor))
        published = False
        successful = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                yield stream
                stream.flush()
                os.fsync(stream.fileno())
                _file_info(stream.fileno(), maximum=MAX_ENVELOPE_BYTES)
                current = os.stat(staging, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(current.st_mode) or _identity(current) != identity:
                    raise _error("destination.changed")
                # link() fails atomically if any destination already exists.
                os.link(staging, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                published = True
                _remove_owned(directory, staging, identity)
                os.fsync(directory)
                successful = True
        finally:
            if not successful and published:
                _remove_owned(directory, name, identity)
            _remove_owned(directory, staging, identity)


def create_key(path: Path) -> None:
    """Create a new exclusive mode-0600 binary key in an existing private dir."""
    try:
        with _parent(path) as (directory, name):
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
            identity = _identity(os.fstat(descriptor))
            successful = False
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(os.urandom(32))
                    stream.flush()
                    os.fsync(stream.fileno())
                    _file_info(stream.fileno(), maximum=32)
                    os.fsync(directory)
                    successful = True
            finally:
                if not successful:
                    _remove_owned(directory, name, identity)
    except BackupCryptoError:
        raise
    except Exception:
        raise _error("key.create-failed") from None


def encrypt_file(
    source: Path, destination: Path, key_path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES,
) -> None:
    """Encrypt at most max_bytes of plaintext without replacing any output."""
    try:
        maximum = _limit(max_bytes)
        key = _key(key_path)
        with _source(source, maximum=maximum) as (plain, before):
            nonce = os.urandom(NONCE_BYTES)
            header = MAGIC + nonce
            encryptor = Cipher(algorithms.AES256(key), modes.GCM(nonce)).encryptor()
            encryptor.authenticate_additional_data(header)
            with _staged(destination) as encrypted:
                encrypted.write(header)
                total = 0
                while block := plain.read(CHUNK_BYTES):
                    total += len(block)
                    if total > maximum:
                        raise _error("size.exceeded")
                    encrypted.write(encryptor.update(block))
                encrypted.write(encryptor.finalize())
                encrypted.write(encryptor.tag)
                _unchanged(plain.fileno(), before, maximum=maximum)
                if total != before.st_size:
                    raise _error("source.changed")
    except BackupCryptoError:
        raise
    except Exception:
        raise _error("encrypt.failed") from None


def decrypt_file(
    source: Path, destination: Path, key_path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES,
) -> None:
    """Authenticate the complete bounded envelope before publishing plaintext."""
    try:
        maximum = _limit(max_bytes)
        key = _key(key_path)
        with _source(source, maximum=maximum + ENVELOPE_OVERHEAD) as (encrypted, before):
            if before.st_size < ENVELOPE_OVERHEAD:
                raise _error("envelope.invalid")
            header = encrypted.read(HEADER_BYTES)
            if len(header) != HEADER_BYTES or header[:len(MAGIC)] != MAGIC:
                raise _error("envelope.invalid")
            nonce = header[len(MAGIC):]
            encrypted.seek(-TAG_BYTES, os.SEEK_END)
            tag = encrypted.read(TAG_BYTES)
            encrypted.seek(HEADER_BYTES)
            decryptor = Cipher(algorithms.AES256(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            remaining = before.st_size - ENVELOPE_OVERHEAD
            with _staged(destination) as plain:
                while remaining:
                    block = encrypted.read(min(CHUNK_BYTES, remaining))
                    if not block:
                        raise _error("envelope.invalid")
                    remaining -= len(block)
                    plain.write(decryptor.update(block))
                plain.write(decryptor.finalize())
                _unchanged(encrypted.fileno(), before, maximum=maximum + ENVELOPE_OVERHEAD)
    except BackupCryptoError:
        raise
    except InvalidTag:
        raise _error("authentication.failed") from None
    except Exception:
        raise _error("decrypt.failed") from None
