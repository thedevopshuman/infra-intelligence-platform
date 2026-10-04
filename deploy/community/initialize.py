"""One-shot volume ownership setup; no serving process runs as root.

Only exact transport files and checked-in public configuration are copied.
This container never mounts the installer's credentials/configuration state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path


TRANSPORT_FILES = (
    "ca.crt",
    "postgres.crt", "postgres.key",
    "receiver.crt", "receiver.key",
    "collector.crt", "collector.key",
    "collector-input.crt", "collector-input.key",
    "receiver-health.crt", "receiver-health.key",
)
TRANSPORT_GROUP_FILES = {
    "database": ("ca.crt", "postgres.crt", "postgres.key"),
    "trust": ("ca.crt",),
    "receiver": ("ca.crt", "receiver.crt", "receiver.key", "receiver-health.crt", "receiver-health.key"),
    "collector": ("ca.crt", "collector.crt", "collector.key", "collector-input.crt", "collector-input.key"),
}
OWNER_IDS = {
    "database": (70, 70),
    "trust": (10001, 10001),
    "receiver": (10001, 10001),
    "collector": (10001, 10001),
    "database-data": (70, 70),
    "queue": (10001, 10001),
    "prometheus-data": (65534, 65534),
    "grafana-data": (472, 472),
    "prometheus": (65534, 65534),
    "grafana": (472, 472),
}
TRANSPORT_RECEIPT = ".transport-generation"
MAX_TRANSPORT_FILE_BYTES = 65_536
_GENERATION = re.compile(r"[a-f0-9]{64}")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_directory(path: Path) -> int:
    try:
        observed = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        current = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (observed.st_dev, observed.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError
        return descriptor
    except Exception:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
        raise ValueError("community.volume.invalid") from None


def directory(name: str, uid: int, gid: int, *, root: Path = Path("/volumes")) -> Path:
    path = root / name
    descriptor = _open_directory(path)
    try:
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o700)
        current = os.fstat(descriptor)
        observed = path.lstat()
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o700
            or (current.st_uid, current.st_gid) != (uid, gid)
            or (observed.st_dev, observed.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError
        return path
    except Exception:
        raise ValueError("community.volume.invalid") from None
    finally:
        os.close(descriptor)


def _sync_directory(path: Path) -> None:
    descriptor = _open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_exact(source: Path, destination: Path, uid: int, gid: int) -> None:
    source_stat = source.lstat()
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size > 1_048_576:
        raise ValueError("community.volume.input.invalid")
    write_exact(destination, source.read_bytes(), uid, gid)


def write_exact(destination: Path, value: bytes, uid: int, gid: int) -> None:
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise ValueError
        os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(value)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), uid, gid)
            os.fsync(stream.fileno())
            current = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(current.st_mode)
                or stat.S_IMODE(current.st_mode) != 0o600
                or (current.st_uid, current.st_gid) != (uid, gid)
                or current.st_nlink != 1
                or current.st_size != len(value)
            ):
                raise ValueError
    except Exception:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)
        raise ValueError("community.volume.write.invalid") from None


def transport_generation(values: Mapping[str, bytes]) -> str:
    if set(values) != set(TRANSPORT_FILES):
        raise ValueError("community.transport.input.invalid")
    hashes = {name: hashlib.sha256(values[name]).hexdigest() for name in TRANSPORT_FILES}
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _read_transport_source(source: Path) -> dict[str, bytes]:
    descriptor = _open_directory(source)
    try:
        before = os.fstat(descriptor)
        if (
            stat.S_IMODE(before.st_mode) not in (0o500, 0o700)
            or before.st_uid < 0
            or before.st_gid < 0
            or before.st_nlink < 1
        ):
            raise ValueError
        names = set(os.listdir(descriptor))
        if names != set(TRANSPORT_FILES):
            raise ValueError
        result: dict[str, bytes] = {}
        for name in TRANSPORT_FILES:
            leaf = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            with os.fdopen(leaf, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                    or (info.st_uid, info.st_gid) != (before.st_uid, before.st_gid)
                    or info.st_nlink != 1
                    or not 1 <= info.st_size <= MAX_TRANSPORT_FILE_BYTES
                ):
                    raise ValueError
                value = stream.read(MAX_TRANSPORT_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
                named = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if len(value) != info.st_size or _identity(info) != _identity(after) or _identity(info) != _identity(named):
                    raise ValueError
                result[name] = value
        after = os.fstat(descriptor)
        observed = source.lstat()
        if _identity(before) != _identity(after) or _identity(before) != _identity(observed):
            raise ValueError
        return result
    except Exception:
        raise ValueError("community.transport.input.invalid") from None
    finally:
        os.close(descriptor)


def _read_projected(path: Path, uid: int, gid: int, *, maximum: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or (info.st_uid, info.st_gid) != (uid, gid)
                or info.st_nlink != 1
                or not 1 <= info.st_size <= maximum
            ):
                raise ValueError
            value = stream.read(maximum + 1)
            current = os.fstat(stream.fileno())
            observed = path.lstat()
            if len(value) != info.st_size or _identity(info) != _identity(current) or _identity(info) != _identity(observed):
                raise ValueError
            return value
    except Exception:
        raise ValueError("community.transport.projection.invalid") from None


def _transport_groups(owners: Mapping[str, tuple[int, int]]) -> tuple[tuple[str, int, int, tuple[str, ...]], ...]:
    try:
        return tuple((name, *owners[name], files) for name, files in TRANSPORT_GROUP_FILES.items())
    except (KeyError, TypeError):
        raise ValueError("community.volume.owner.invalid") from None


def invalidate_transport_receipts(
    volumes_root: Path,
    *,
    owners: Mapping[str, tuple[int, int]] = OWNER_IDS,
) -> None:
    failed = False
    for name, uid, gid, _ in _transport_groups(owners):
        try:
            destination = directory(name, uid, gid, root=volumes_root)
            receipt = destination / TRANSPORT_RECEIPT
            receipt.unlink(missing_ok=True)
            _sync_directory(destination)
        except Exception:
            failed = True
    if failed:
        raise ValueError("community.transport.receipt.invalidate-failed")


def project_transport(
    source: Path,
    volumes_root: Path,
    generation: str,
    *,
    owners: Mapping[str, tuple[int, int]] = OWNER_IDS,
) -> None:
    invalidate_transport_receipts(volumes_root, owners=owners)
    try:
        if not isinstance(generation, str) or _GENERATION.fullmatch(generation) is None:
            raise ValueError
        values = _read_transport_source(source)
        if not hmac.compare_digest(transport_generation(values), generation):
            raise ValueError

        destinations: list[tuple[Path, int, int, tuple[str, ...]]] = []
        for name, uid, gid, files in _transport_groups(owners):
            destination = directory(name, uid, gid, root=volumes_root)
            for filename in files:
                write_exact(destination / filename, values[filename], uid, gid)
            destinations.append((destination, uid, gid, files))

        for destination, uid, gid, files in destinations:
            for filename in files:
                projected = _read_projected(
                    destination / filename,
                    uid,
                    gid,
                    maximum=MAX_TRANSPORT_FILE_BYTES,
                )
                if not hmac.compare_digest(projected, values[filename]):
                    raise ValueError

        receipt = (generation + "\n").encode()
        for destination, uid, gid, _ in destinations:
            write_exact(destination / TRANSPORT_RECEIPT, receipt, uid, gid)
        for destination, uid, gid, _ in destinations:
            if not hmac.compare_digest(
                _read_projected(destination / TRANSPORT_RECEIPT, uid, gid, maximum=65),
                receipt,
            ):
                raise ValueError
            _sync_directory(destination)
    except Exception:
        try:
            invalidate_transport_receipts(volumes_root, owners=owners)
        except Exception:
            pass
        raise ValueError("community.transport.projection.invalid") from None


def initialize(
    source: Path,
    public: Path,
    volumes_root: Path,
    collector_configuration: Path,
    dashboard: Path,
    environment: Mapping[str, str],
    *,
    owners: Mapping[str, tuple[int, int]] = OWNER_IDS,
) -> None:
    # A prior successful generation must never survive any later failed run.
    invalidate_transport_receipts(volumes_root, owners=owners)
    generation = environment.get("IIP_COMMUNITY_TRANSPORT_GENERATION", "")
    if not isinstance(generation, str) or _GENERATION.fullmatch(generation) is None:
        raise ValueError("community.transport.generation.invalid")

    database_uid, database_gid = owners["database"]
    database = directory("database", database_uid, database_gid, root=volumes_root)
    copy_exact(public / "pg_hba.conf", database / "pg_hba.conf", database_uid, database_gid)
    write_exact(
        database / "password",
        environment["IIP_COMMUNITY_POSTGRES_PASSWORD"].encode(),
        database_uid,
        database_gid,
    )
    for name in ("database-data", "queue", "prometheus-data", "grafana-data"):
        uid, gid = owners[name]
        directory(name, uid, gid, root=volumes_root)

    prometheus_uid, prometheus_gid = owners["prometheus"]
    prometheus = directory("prometheus", prometheus_uid, prometheus_gid, root=volumes_root)
    copy_exact(public / "prometheus.yml", prometheus / "prometheus.yml", prometheus_uid, prometheus_gid)

    collector_uid, collector_gid = owners["collector"]
    collector = directory("collector", collector_uid, collector_gid, root=volumes_root)
    copy_exact(collector_configuration, collector / "collector.yaml", collector_uid, collector_gid)

    grafana_uid, grafana_gid = owners["grafana"]
    grafana = directory("grafana", grafana_uid, grafana_gid, root=volumes_root)
    for subdirectory, filename in (("datasources", "datasources.yaml"), ("dashboards", "dashboards.yaml")):
        child = grafana / subdirectory
        child.mkdir(mode=0o700, exist_ok=True)
        descriptor = _open_directory(child)
        try:
            os.fchown(descriptor, grafana_uid, grafana_gid)
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
        copy_exact(public / filename, child / filename, grafana_uid, grafana_gid)
    copy_exact(dashboard, grafana / "dashboard.json", grafana_uid, grafana_gid)
    write_exact(
        grafana / "admin-password",
        environment["IIP_COMMUNITY_GRAFANA_PASSWORD"].encode(),
        grafana_uid,
        grafana_gid,
    )

    # This is the final write boundary: receipts commit the exact projected
    # transport only after every other initializer projection has completed.
    project_transport(source, volumes_root, generation, owners=owners)


def main() -> None:
    initialize(
        Path("/transport"),
        Path("/configuration"),
        Path("/volumes"),
        Path("/collector.yaml"),
        Path("/dashboard.json"),
        os.environ,
    )


if __name__ == "__main__":
    main()
