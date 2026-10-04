"""One-shot volume ownership setup; no serving process runs as root.

Only exact transport files and checked-in public configuration are copied.
This container never mounts the installer's credentials/configuration state.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def directory(name: str, uid: int, gid: int) -> Path:
    path = Path("/volumes") / name
    if path.is_symlink() or not path.is_dir():
        raise ValueError("community.volume.invalid")
    os.chown(path, uid, gid)
    os.chmod(path, 0o700)
    return path


def copy_exact(source: Path, destination: Path, uid: int, gid: int) -> None:
    source_stat = source.lstat()
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size > 1_048_576:
        raise ValueError("community.volume.input.invalid")
    write_exact(destination, source.read_bytes(), uid, gid)


def write_exact(destination: Path, value: bytes, uid: int, gid: int) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        os.fchmod(stream.fileno(), 0o600)
        os.fchown(stream.fileno(), uid, gid)


def main() -> None:
    source = Path("/transport")
    public = Path("/configuration")
    groups = (
        ("database", 70, 70, ("ca.crt", "postgres.crt", "postgres.key")),
        ("trust", 10001, 10001, ("ca.crt",)),
        ("receiver", 10001, 10001, ("ca.crt", "receiver.crt", "receiver.key", "receiver-health.crt", "receiver-health.key")),
        ("collector", 10001, 10001, ("ca.crt", "collector.crt", "collector.key", "collector-input.crt", "collector-input.key")),
    )
    for name, uid, gid, files in groups:
        destination = directory(name, uid, gid)
        for filename in files:
            copy_exact(source / filename, destination / filename, uid, gid)
    database = Path("/volumes/database")
    copy_exact(public / "pg_hba.conf", database / "pg_hba.conf", 70, 70)
    write_exact(database / "password", os.environ["IIP_COMMUNITY_POSTGRES_PASSWORD"].encode(), 70, 70)
    for name, uid, gid in (("database-data", 70, 70), ("queue", 10001, 10001), ("prometheus-data", 65534, 65534), ("grafana-data", 472, 472)):
        directory(name, uid, gid)
    for name, uid, gid, files in (
        ("prometheus", 65534, 65534, ("prometheus.yml",)),
    ):
        destination = directory(name, uid, gid)
        for filename in files:
            copy_exact(public / filename, destination / filename, uid, gid)
    copy_exact(Path("/collector.yaml"), Path("/volumes/collector/collector.yaml"), 10001, 10001)
    grafana = directory("grafana", 472, 472)
    for subdirectory, filename in (("datasources", "datasources.yaml"), ("dashboards", "dashboards.yaml")):
        child = grafana / subdirectory
        child.mkdir(mode=0o700, exist_ok=True)
        if child.is_symlink() or not child.is_dir():
            raise ValueError("community.volume.invalid")
        os.chown(child, 472, 472)
        os.chmod(child, 0o700)
        copy_exact(public / filename, child / filename, 472, 472)
    copy_exact(Path("/dashboard.json"), grafana / "dashboard.json", 472, 472)
    write_exact(grafana / "admin-password", os.environ["IIP_COMMUNITY_GRAFANA_PASSWORD"].encode(), 472, 472)


if __name__ == "__main__":
    main()
