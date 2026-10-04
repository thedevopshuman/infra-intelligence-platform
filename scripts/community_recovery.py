"""Encrypted, offline, same-deployment recovery for the community installation.

This operational helper never stops an installation, overwrites a destination,
starts restored services, contacts a provider, or uploads customer data.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

import community_stack as stack
from community_docker import local_docker_binding
from community_transport import TRANSPORT_FILES

ROOT = stack.ROOT
VOLUME_HELPER = ROOT / "deploy/community/recovery_volume.py"
VOLUMES = ("postgres-data", "collector-queue", "prometheus-data", "grafana-data")
ALL_VOLUMES = (*VOLUMES, "database-secrets", "database-trust", "receiver-secrets",
               "collector-secrets", "prometheus-config", "grafana-config")
SERVICES = ("initialize", "migrate", "api", "workflow-worker", "ai-usage-receiver",
            "postgres", "otel-collector", "prometheus", "grafana")
DEFAULT_MAX_BYTES = 8 * 1024**3
from community_backup_crypto import MAX_PLAINTEXT_BYTES

MAX_BYTES = MAX_PLAINTEXT_BYTES
SHA = re.compile(r"[a-f0-9]{64}")
IMAGE = re.compile(r"sha256:[a-f0-9]{64}")


class RecoveryError(ValueError):
    """A stable, value-minimized operational failure."""


def deployment_digest() -> str:
    """Cold recovery is not a migration across installer/deployment versions."""
    paths = [stack.COMPOSE, ROOT / "Dockerfile", *sorted((ROOT / "deploy/community").glob("*")),
             *sorted((ROOT / "scripts").glob("community_*.py"))]
    values = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths if path.is_file() and not path.is_symlink()}
    return hashlib.sha256(stack.compact(values).encode()).hexdigest()


def atomic_document(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".recovery-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(stack.compact(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        stack._sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


class RecoveryDocker:
    def __init__(self, state: Path):
        executable, endpoint, self.environment = local_docker_binding(state, dict(os.environ))
        self.prefix = [executable, "--host", endpoint]

    def text(self, *arguments: str) -> str:
        try:
            result = subprocess.run([*self.prefix, *arguments], env=self.environment,
                capture_output=True, text=True, check=True, timeout=30)
            if len(result.stdout) > 131072:
                raise RecoveryError("community.recovery.docker-output-invalid")
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise RecoveryError("community.recovery.docker-command-failed") from error

    def containers(self, project: str) -> list[str]:
        result = self.text("ps", "--all", "--quiet", "--filter", f"label=com.docker.compose.project={project}").splitlines()
        if any(not re.fullmatch(r"[a-f0-9]{12,64}", identifier) for identifier in result):
            raise RecoveryError("community.recovery.container-invalid")
        return result

    def require_stopped(self, project: str) -> None:
        if self.containers(project):
            raise RecoveryError("community.recovery.down-required")

    def image(self, identifier: str) -> tuple[str, str, str]:
        values = self.text("image", "inspect", "--format", "{{.Id}}|{{.Os}}|{{.Architecture}}", identifier).split("|")
        if len(values) != 3 or not IMAGE.fullmatch(values[0]) or values[1] != "linux" or values[2] not in ("amd64", "arm64"):
            raise RecoveryError("community.recovery.image-invalid")
        return tuple(values)

    def require_images(self, snapshot: dict) -> None:
        validate_snapshot(snapshot)
        platform = self.text("info", "--format", "{{.OSType}}|{{.Architecture}}").split("|")
        if len(platform) == 2:
            platform[1] = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform[1], platform[1])
        if platform != [snapshot["os"], snapshot["architecture"]]:
            raise RecoveryError("community.recovery.platform-mismatch")
        for identifier in set(snapshot["images"].values()):
            if self.image(identifier) != (identifier, snapshot["os"], snapshot["architecture"]):
                raise RecoveryError("community.recovery.image-mismatch")

    def volume_names(self) -> set[str]:
        return set(self.text("volume", "ls", "--format", "{{.Name}}").splitlines())

    def require_volume(self, project: str, name: str) -> str:
        selected = f"{project}_{name}"
        metadata = json.loads(self.text("volume", "inspect", "--format", "{{json .}}", selected))
        labels = metadata.get("Labels") or {}
        if (metadata.get("Name") != selected or metadata.get("Driver") != "local"
                or metadata.get("Options") not in (None, {}) or metadata.get("Scope") != "local"
                or labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.volume") != name):
            raise RecoveryError("community.recovery.volume-ownership-invalid")
        # A differently named project may have mounted this volume manually.
        if self.text("ps", "--all", "--quiet", "--filter", f"volume={selected}"):
            raise RecoveryError("community.recovery.volume-in-use")
        return selected

    def helper(self, image: str, mounts: list[str], command: list[str], *, output=None, user: str = "0:0") -> str:
        name = "iip-recovery-" + secrets.token_hex(12)
        arguments = [*self.prefix, "run", "--rm", "--pull", "never", "--name", name,
            "--network", "none", "--read-only", "--user", user,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "64", "--memory", "256m", "--tmpfs", "/tmp:mode=1777,size=64m",
            "--label", "io.iip.community-recovery-helper=true", "--env", "LC_ALL=C"]
        for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
                         "http_proxy", "https_proxy", "all_proxy", "ftp_proxy"):
            arguments += ["--env", f"{variable}="]
        arguments += ["--env", "NO_PROXY=*", "--env", "no_proxy=*"]
        if user == "0:0":
            arguments += ["--cap-add", "DAC_OVERRIDE", "--cap-add", "CHOWN", "--cap-add", "FOWNER"]
        for mount in mounts:
            if any(character in mount for character in ("\n", "\r")):
                raise RecoveryError("community.recovery.mount-invalid")
            arguments += ["--mount", mount]
        arguments += ["--entrypoint", command[0], image, *command[1:]]
        try:
            result = subprocess.run(arguments, env=self.environment, stdout=output or subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=True, timeout=900)
            return "" if output else result.stdout.decode("utf-8")
        except (OSError, UnicodeError, subprocess.SubprocessError) as error:
            raise RecoveryError("community.recovery.helper-failed") from error
        finally:
            # Exact random helper allocated by this operation only; never a serving container.
            # A timeout kills the client, not necessarily its container. Failure
            # to prove cleanup blocks backup publication / keeps restore fenced.
            if self.text("ps", "--all", "--quiet", "--filter", f"name=^{name}$"):
                self.text("rm", "--force", name)
                if self.text("ps", "--all", "--quiet", "--filter", f"name=^{name}$"):
                    raise RecoveryError("community.recovery.helper-cleanup-required")


def validate_snapshot(snapshot: dict) -> None:
    if (not isinstance(snapshot, dict) or set(snapshot) != {"format", "deployment", "os", "architecture", "images"}
            or type(snapshot["format"]) is not int or snapshot["format"] != 1 or snapshot["deployment"] != deployment_digest()
            or snapshot["os"] != "linux" or snapshot["architecture"] not in ("amd64", "arm64")
            or not isinstance(snapshot["images"], dict) or set(snapshot["images"]) != set(SERVICES)
            or any(not isinstance(value, str) or not IMAGE.fullmatch(value) for value in snapshot["images"].values())
            or len({snapshot["images"][name] for name in SERVICES[:5]}) != 1):
        raise RecoveryError("community.recovery.same-deployment-required")


def record_runtime(state: Path) -> None:
    """Capture actual container images while successful up still has provenance."""
    project = stack.read_protected(state / "installation.json")["project"]
    docker = RecoveryDocker(state)
    images: dict[str, str] = {}
    for identifier in docker.containers(project):
        result = docker.text("inspect", "--format",
            '{{.Image}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.project"}}', identifier).split("|")
        if len(result) != 3 or result[2] != project or result[1] not in SERVICES or result[1] in images:
            raise RecoveryError("community.recovery.runtime-incomplete")
        images[result[1]] = result[0]
    if set(images) != set(SERVICES):
        raise RecoveryError("community.recovery.runtime-incomplete")
    identities = {docker.image(value) for value in set(images.values())}
    if len({(item[1], item[2]) for item in identities}) != 1:
        raise RecoveryError("community.recovery.platform-mismatch")
    _, system, architecture = next(iter(identities))
    snapshot = {"format": 1, "deployment": deployment_digest(), "os": system,
                "architecture": architecture, "images": images}
    validate_snapshot(snapshot)
    atomic_document(state / "runtime-images.json", snapshot)


def recovered_environment(state: Path) -> dict[str, str]:
    path = state / "recovery-images.json"
    if not path.exists() and not path.is_symlink():
        return {}
    snapshot = stack.read_protected(path)
    validate_snapshot(snapshot)
    return {variable: snapshot["images"][service] for service, variable in (
        ("api", "IIP_COMMUNITY_IMAGE"), ("postgres", "IIP_COMMUNITY_POSTGRES_IMAGE"),
        ("otel-collector", "IIP_COMMUNITY_COLLECTOR_IMAGE"),
        ("prometheus", "IIP_COMMUNITY_PROMETHEUS_IMAGE"), ("grafana", "IIP_COMMUNITY_GRAFANA_IMAGE"))}


def _volume_module():
    spec = importlib.util.spec_from_file_location("iip_recovery_volume", VOLUME_HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _private_bytes(path: Path, *, maximum: int = 16 * 1024**2) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid() or metadata.st_nlink != 1 or metadata.st_size > maximum):
            raise RecoveryError("community.recovery.file-protection-required")
        return stream.read(maximum + 1)


def _write_bytes(path: Path, content: bytes) -> None:
    _private_parents(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _private_parents(path: Path) -> None:
    """Create each new directory privately, including intermediate parents."""
    if not path.exists() and not path.is_symlink():
        _private_parents(path.parent)
        path.mkdir(mode=0o700)
    stack._private_directory(path)


def _digest(path: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024**2):
            size += len(chunk)
            digest.update(chunk)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _state_paths(state: Path) -> list[str]:
    from community_trust import backup_paths

    stack._private_directory(state / "transport")
    stack._private_directory(state / "config")
    paths = ["installation.json", "credentials.json", *(f"transport/{name}" for name in TRANSPORT_FILES),
             *backup_paths(state)]
    generations = sorted((state / "config").iterdir())
    if not 1 <= len(generations) <= 256:
        raise RecoveryError("community.recovery.configuration-limit")
    for directory in generations:
        if not SHA.fullmatch(directory.name):
            raise RecoveryError("community.recovery.configuration-invalid")
        stack._private_directory(directory)
        names = [*(f"{name}.json" for name in stack.INPUT_FILES), "dashboard.json", "collector.yaml"]
        if set(path.name for path in directory.iterdir()) != set(names):
            raise RecoveryError("community.recovery.configuration-invalid")
        inputs = {name: stack.read_protected(directory / f"{name}.json") for name in stack.INPUT_FILES}
        if stack.generation_digest(directory, inputs) != directory.name:
            raise RecoveryError("community.recovery.configuration-invalid")
        paths.extend(f"config/{directory.name}/{name}" for name in names)
    return paths


def _bound(max_bytes: int) -> None:
    if type(max_bytes) is not int or not 1024**2 <= max_bytes <= MAX_BYTES:
        raise RecoveryError("community.recovery.size-limit-invalid")


def _safe_mount(path: Path) -> str:
    value = str(path.absolute())
    if any(character in value for character in (",", "\n", "\r")):
        raise RecoveryError("community.recovery.mount-invalid")
    return value


def _check_database(docker: RecoveryDocker, project: str, images: dict) -> None:
    volume = docker.require_volume(project, "postgres-data")
    output = docker.helper(images["postgres"], [f"type=volume,src={volume},dst=/volume,readonly,volume-nocopy"],
                           ["pg_controldata", "/volume/18/docker"], user="70:70")
    if not re.search(r"^Database cluster state:\s+shut down\s*$", output, re.MULTILINE):
        raise RecoveryError("community.recovery.clean-database-shutdown-required")


def backup(state: Path, destination: Path, key: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    from community_backup_crypto import encrypt_file
    from community_trust import require_settled

    _bound(max_bytes)
    state, destination, key = state.absolute(), destination.absolute(), key.absolute()
    if (state / ".recovery-incomplete").exists() or (state / ".recovery-incomplete").is_symlink():
        raise RecoveryError("community.recovery.incomplete")
    stack.installed_environment(state, validate_contracts=False)
    require_settled(state)
    stack._private_directory(destination.parent)
    if key == destination:
        raise RecoveryError("community.recovery.destination-invalid")
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(state.resolve()):
        raise RecoveryError("community.recovery.destination-invalid")
    snapshot = stack.read_protected(state / "runtime-images.json")
    validate_snapshot(snapshot)
    docker = RecoveryDocker(state)
    project = stack.project_name(state)
    docker.require_stopped(project)
    docker.require_images(snapshot)
    for volume in VOLUMES:
        docker.require_volume(project, volume)
    _check_database(docker, project, snapshot["images"])
    with tempfile.TemporaryDirectory(prefix=".iip-backup-", dir=destination.parent) as temporary:
        work = Path(temporary).resolve()
        members: dict[str, dict] = {}
        for relative in _state_paths(state):
            path = work / "state" / relative
            _write_bytes(path, _private_bytes(state / relative))
            members[f"state/{relative}"] = _digest(path)
        remaining = max_bytes - sum(item["bytes"] for item in members.values())
        for name in VOLUMES:
            path = work / f"{name}.tar"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                docker.helper(snapshot["images"]["api"], [
                    f"type=volume,src={project}_{name},dst=/volume,readonly,volume-nocopy",
                    f"type=bind,src={_safe_mount(VOLUME_HELPER)},dst=/recovery.py,readonly"],
                    ["python", "/recovery.py", "export", "--max-bytes", str(remaining)], output=stream)
                stream.flush()
                os.fsync(stream.fileno())
            _volume_module().inspect_archive(path, max_bytes=remaining)
            members[path.name] = _digest(path)
            remaining -= members[path.name]["bytes"]
            if remaining < 1024**2:
                raise RecoveryError("community.recovery.size-limit")
        docker.require_stopped(project)
        for name in VOLUMES:
            docker.require_volume(project, name)
        # Host CLI lock plus stopped volumes make this a single offline point.
        for relative in _state_paths(state):
            if _private_bytes(state / relative) != _private_bytes(work / "state" / relative):
                raise RecoveryError("community.recovery.source-changed")
        manifest = {"format": "iip-community-backup-v1", "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourceProject": project, "sourceDaemon": stack.read_protected(state / "daemon.json"),
            "runtime": snapshot, "members": members}
        stack.write_protected(work / "manifest.json", manifest)
        payload = work / "payload.tar"
        descriptor = os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream, tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for relative in ["manifest.json", *sorted(members)]:
                path = work / relative
                entry = tarfile.TarInfo(relative)
                entry.size, entry.mode = path.stat().st_size, 0o600
                with path.open("rb") as source:
                    archive.addfile(entry, source)
        # Do not resolve caller paths: crypto walks every ancestor without
        # following symlinks, and publishes by atomic no-clobber link.
        encrypt_file(payload, destination, key, max_bytes=max_bytes)


def _validate_outer_archive(payload: Path, max_bytes: int) -> None:
    """Reject extension headers and hidden/trailing data before tarfile parsing."""
    if payload.stat().st_size > max_bytes:
        raise RecoveryError("community.recovery.size-limit")
    with payload.open("rb") as source:
        count = 0
        while True:
            header = source.read(tarfile.BLOCKSIZE)
            if len(header) != tarfile.BLOCKSIZE:
                raise RecoveryError("community.recovery.archive-invalid")
            if header == bytes(tarfile.BLOCKSIZE):
                if not count or source.read(tarfile.BLOCKSIZE) != bytes(tarfile.BLOCKSIZE):
                    raise RecoveryError("community.recovery.archive-invalid")
                while trailing := source.read(1024**2):
                    if any(trailing):
                        raise RecoveryError("community.recovery.archive-invalid")
                if source.tell() % tarfile.BLOCKSIZE:
                    raise RecoveryError("community.recovery.archive-invalid")
                return
            count += 1
            if count > 4096 or header[257:265] != b"ustar\x0000" or header[156:157] != tarfile.REGTYPE:
                raise RecoveryError("community.recovery.archive-invalid")
            entry = tarfile.TarInfo.frombuf(header, encoding="utf-8", errors="strict")
            if entry.tobuf(format=tarfile.USTAR_FORMAT, encoding="utf-8", errors="strict") != header:
                raise RecoveryError("community.recovery.archive-invalid")
            if entry.size < 0 or entry.size > max_bytes:
                raise RecoveryError("community.recovery.size-limit")
            remaining = entry.size
            while remaining:
                chunk = source.read(min(remaining, 1024**2))
                if not chunk:
                    raise RecoveryError("community.recovery.archive-invalid")
                remaining -= len(chunk)
            padding = (-entry.size) % tarfile.BLOCKSIZE
            if source.read(padding) != bytes(padding):
                raise RecoveryError("community.recovery.archive-invalid")


def _unpack(payload: Path, work: Path, max_bytes: int) -> dict:
    _validate_outer_archive(payload, max_bytes)
    members: dict[str, dict] = {}
    with tarfile.open(payload, "r:") as archive:
        total = 0
        for entry in archive:
            parts = PurePosixPath(entry.name).parts
            if (not entry.isfile() or entry.pax_headers or not parts or any(part in ("..", ".") for part in parts)
                    or entry.name.startswith("/") or "\\" in entry.name or entry.name in members
                    or PurePosixPath(entry.name).as_posix() != entry.name
                    or entry.mode != 0o600 or entry.size < 0 or len(members) >= 4096
                    or entry.uid != 0 or entry.gid != 0 or entry.uname or entry.gname
                    or entry.linkname or len(entry.name.encode("utf-8")) > 255
                    or any(ord(character) < 32 or ord(character) == 127 for character in entry.name)):
                raise RecoveryError("community.recovery.archive-invalid")
            if entry.name != "manifest.json" and entry.name not in {f"{name}.tar" for name in VOLUMES} and not entry.name.startswith("state/"):
                raise RecoveryError("community.recovery.archive-invalid")
            total += entry.size
            if total > max_bytes or (entry.name.startswith("state/") or entry.name == "manifest.json") and entry.size > 16 * 1024**2:
                raise RecoveryError("community.recovery.size-limit")
            destination = work / entry.name
            _private_parents(destination.parent)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as stream, archive.extractfile(entry) as source:
                shutil.copyfileobj(source, stream, 1024**2)
                stream.flush()
                os.fsync(stream.fileno())
            members[entry.name] = _digest(destination)
    manifest = stack.read_protected(work / "manifest.json")
    if (set(manifest) != {"format", "createdAt", "sourceProject", "sourceDaemon", "runtime", "members"}
            or manifest["format"] != "iip-community-backup-v1"
            or not isinstance(manifest["sourceProject"], str)
            or not re.fullmatch(r"iip-community-[a-f0-9]{10}", manifest["sourceProject"])
            or {key: value for key, value in members.items() if key != "manifest.json"} != manifest["members"]):
        raise RecoveryError("community.recovery.manifest-invalid")
    try:
        created = datetime.fromisoformat(manifest["createdAt"])
        daemon = manifest["sourceDaemon"]
        if (created.tzinfo is None or not isinstance(daemon, dict)
                or set(daemon) != {"format", "endpoint", "daemonId"}
                or type(daemon["format"]) is not int or daemon["format"] != 1
                or not isinstance(daemon["endpoint"], str)
                or not re.fullmatch(r"unix:///[^\s\x00-\x1f]+", daemon["endpoint"])
                or not isinstance(daemon["daemonId"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", daemon["daemonId"])):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise RecoveryError("community.recovery.manifest-invalid") from None
    validate_snapshot(manifest["runtime"])
    state = work / "state"
    expected = {f"state/{relative}" for relative in _state_paths(state)} | {f"{name}.tar" for name in VOLUMES}
    if set(manifest["members"]) != expected:
        raise RecoveryError("community.recovery.manifest-invalid")
    for name in VOLUMES:
        _volume_module().inspect_archive(work / f"{name}.tar", max_bytes=max_bytes)
    installation = stack.read_protected(state / "installation.json")
    if installation["project"] != manifest["sourceProject"]:
        raise RecoveryError("community.recovery.source-invalid")
    # Rebind only the temporary copy for structural checks; expiry never blocks rescue.
    installation["project"] = stack.project_name(state.absolute())
    atomic_document(state / "installation.json", installation)
    stack.installed_environment(state, validate_contracts=False)
    return manifest


def restore(destination: Path, source: Path, key: Path, *, source_fenced: bool,
            max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    from community_backup_crypto import decrypt_file

    _bound(max_bytes)
    destination = destination.absolute()
    stack._private_directory(destination.parent)
    if not source_fenced:
        raise RecoveryError("community.recovery.source-fencing-required")
    if destination.exists() or destination.is_symlink():
        raise RecoveryError("community.recovery.destination-exists")
    try:
        relative = destination.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        if len(relative.parts) < 2 or relative.parts[0] != ".iip":
            raise RecoveryError("community.recovery.build-context-prohibited")
    # Serialize fresh destinations without creating an apparent installation first.
    with stack.installation_lock(destination.parent):
        with tempfile.TemporaryDirectory(prefix=".iip-restore-", dir=destination.parent) as temporary:
            work = Path(temporary).resolve()
            payload = work / "payload.tar"
            decrypt_file(source.absolute(), payload, key.absolute(), max_bytes=max_bytes)
            manifest = _unpack(payload, work, max_bytes)
            staged = work / "state"
            docker = RecoveryDocker(staged)
            docker.require_images(manifest["runtime"])
            project = stack.project_name(destination)
            docker.require_stopped(project)
            # Also fence the old project when visible on this selected local daemon.
            docker.require_stopped(manifest["sourceProject"])
            if {f"{project}_{name}" for name in ALL_VOLUMES} & docker.volume_names():
                raise RecoveryError("community.recovery.target-volumes-exist")
            installation = stack.read_protected(staged / "installation.json")
            installation["project"] = project
            atomic_document(staged / "installation.json", installation)
            stack.write_protected(staged / "recovery-images.json", manifest["runtime"])
            stack.write_protected(staged / "runtime-images.json", manifest["runtime"])
            stack.write_protected(staged / "recovery-provenance.json", {
                "format": 1, "sourceProject": manifest["sourceProject"],
                "sourceDaemon": manifest["sourceDaemon"], "backupCreatedAt": manifest["createdAt"],
                "sourceFencingConfirmed": True,
            })
            _write_bytes(staged / ".recovery-incomplete", b"Restore in progress; do not start.\n")
            # mkdir is the no-overwrite reservation. On failure keep its marker and
            # exact newly allocated volumes for diagnosis; never destroy customer state.
            destination.mkdir(mode=0o700)
            _write_bytes(destination / ".recovery-incomplete", b"Restore in progress; do not start.\n")
            # Persist the fence before moving any usable installation metadata.
            stack._sync_directory(destination)
            stack._sync_directory(destination.parent)
            for path in staged.iterdir():
                if path.name != ".recovery-incomplete":
                    os.rename(path, destination / path.name)
            stack._sync_directory(destination)
            for name in VOLUMES:
                volume = f"{project}_{name}"
                docker.text("volume", "create", "--label", f"com.docker.compose.project={project}",
                    "--label", f"com.docker.compose.volume={name}", "--label", "io.iip.recovery=true", volume)
                docker.require_volume(project, name)
                docker.helper(manifest["runtime"]["images"]["api"], [
                    f"type=volume,src={volume},dst=/volume,volume-nocopy",
                    f"type=bind,src={_safe_mount(VOLUME_HELPER)},dst=/recovery.py,readonly",
                    f"type=bind,src={_safe_mount(work / (name + '.tar'))},dst=/archive,readonly"],
                    ["python", "/recovery.py", "import", "--archive", "/archive", "--max-bytes", str(max_bytes)])
            _check_database(docker, project, manifest["runtime"]["images"])
            docker.require_stopped(project)
            (destination / ".recovery-incomplete").unlink()
            stack._sync_directory(destination)
            stack._sync_directory(destination.parent)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    key_parser = subparsers.add_parser("keygen", help="create a new protected binary encryption key")
    key_parser.add_argument("--key", type=Path, required=True)
    for command in ("backup", "restore"):
        selected = subparsers.add_parser(command)
        selected.add_argument("--state", type=Path, required=True,
                              help="existing source (backup) or never-existing destination (restore)")
        selected.add_argument("--archive", type=Path, required=True)
        selected.add_argument("--key", type=Path, required=True)
        selected.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                              help="maximum plaintext bytes, default 8 GiB; includes tar overhead")
        if command == "restore":
            selected.add_argument("--source-fenced", action="store_true",
                                  help="confirm the old installation cannot receive traffic or restart")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "keygen":
            from community_backup_crypto import create_key
            create_key(arguments.key.absolute())
            print("A new protected backup key was created. Keep it separate from archives; it is never printed.")
        elif arguments.command == "backup":
            with stack.installation_lock(arguments.state.absolute()):
                backup(arguments.state, arguments.archive, arguments.key, max_bytes=arguments.max_bytes)
            print("Encrypted offline backup complete. Source remains stopped; no data was uploaded.")
        else:
            restore(arguments.state, arguments.archive, arguments.key,
                    source_fenced=arguments.source_fenced, max_bytes=arguments.max_bytes)
            print("Restored into fresh stopped state and volumes. Keep the source fenced; revalidate before explicit startup.")
        return 0
    except (ValueError, OSError, TypeError, KeyError, RecursionError, tarfile.TarError):
        print("ERROR: community.recovery.failed; verify protection, key, archive, offline state, exact images/deployment, and capacity. Partial restores remain fenced for diagnosis.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
