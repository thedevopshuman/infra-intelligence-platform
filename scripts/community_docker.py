"""Bind a community installation to one exact local Docker daemon."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


_BINDING_FILE = "daemon.json"
_INSPECTION_TIMEOUT_SECONDS = 15
_MAX_DOCKER_OUTPUT_BYTES = 4096
_MAX_BINDING_BYTES = 4096
_DAEMON_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")


class CommunityDockerError(ValueError):
    """Stable configuration failure that never includes Docker output."""


def _private_state_directory(state: Path) -> None:
    try:
        metadata = state.lstat()
    except OSError as error:
        raise CommunityDockerError("community.docker.state-invalid") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise CommunityDockerError("community.docker.state-invalid")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CommunityDockerError("community.docker.binding-invalid")
        result[key] = value
    return result


def _binding_document(endpoint: str, daemon_id: str) -> dict[str, object]:
    return {"format": 1, "endpoint": endpoint, "daemonId": daemon_id}


def _read_binding(state: Path) -> dict[str, object] | None:
    path = state / _BINDING_FILE
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CommunityDockerError("community.docker.binding-invalid") from error

    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or metadata.st_size > _MAX_BINDING_BYTES
            ):
                raise CommunityDockerError("community.docker.binding-invalid")
            document = json.load(source, object_pairs_hook=_object)
    except CommunityDockerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CommunityDockerError("community.docker.binding-invalid") from error

    if (
        not isinstance(document, dict)
        or set(document) != {"format", "endpoint", "daemonId"}
        or document.get("format") != 1
        or not isinstance(document.get("endpoint"), str)
        or not isinstance(document.get("daemonId"), str)
        or _DAEMON_ID.fullmatch(document["daemonId"]) is None
    ):
        raise CommunityDockerError("community.docker.binding-invalid")
    return document


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CommunityDockerError("community.docker.binding-write-failed") from error


def _write_binding_once(state: Path, document: dict[str, object]) -> None:
    """Atomically create the binding without replacing an existing identity."""
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".daemon-", dir=state)
    except OSError as error:
        raise CommunityDockerError("community.docker.binding-write-failed") from error

    temporary_path = Path(temporary)
    linked = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary_path, state / _BINDING_FILE, follow_symlinks=False)
            linked = True
        except FileExistsError:
            existing = _read_binding(state)
            if existing != document:
                raise CommunityDockerError("community.docker.binding-mismatch")
        temporary_path.unlink()
        if linked:
            _sync_directory(state)
    except CommunityDockerError:
        raise
    except OSError as error:
        raise CommunityDockerError("community.docker.binding-write-failed") from error
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _sanitize_environment(base_environment: dict[str, str]) -> dict[str, str]:
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in base_environment.items()):
        raise CommunityDockerError("community.docker.environment-invalid")
    if base_environment.get("DOCKER_HOST"):
        raise CommunityDockerError("community.docker.host-override-prohibited")
    return {
        key: value
        for key, value in base_environment.items()
        if not key.startswith(("IIP_", "COMPOSE_", "PG"))
    }


def _docker_json(
    command: list[str], environment: dict[str, str]
) -> object:
    try:
        completed = subprocess.run(
            command,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=_INSPECTION_TIMEOUT_SECONDS,
        )
        if not isinstance(completed.stdout, str) or len(completed.stdout.encode("utf-8")) > _MAX_DOCKER_OUTPUT_BYTES:
            raise CommunityDockerError("community.docker.command-failed")
        return json.loads(completed.stdout)
    except CommunityDockerError:
        raise
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        raise CommunityDockerError("community.docker.command-failed") from error


def _is_socket(path: str) -> bool:
    """Return whether the endpoint is a trusted local Docker socket."""
    metadata = os.stat(path)
    return (
        stat.S_ISSOCK(metadata.st_mode)
        and metadata.st_uid in {0, os.getuid()}
        and stat.S_IMODE(metadata.st_mode) & stat.S_IWOTH == 0
    )


def _require_local_socket(endpoint: object) -> str:
    if not isinstance(endpoint, str) or any(ord(character) < 33 or ord(character) == 127 for character in endpoint):
        raise CommunityDockerError("community.docker.local-daemon-required")
    parsed = urlsplit(endpoint)
    path = parsed.path
    if (
        parsed.scheme != "unix"
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not path.startswith("/")
        or path.startswith("//")
        or os.path.normpath(path) != path
        or "%" in path
        or endpoint != f"unix://{path}"
    ):
        raise CommunityDockerError("community.docker.local-daemon-required")
    try:
        if not _is_socket(path):
            raise CommunityDockerError("community.docker.local-daemon-required")
    except CommunityDockerError:
        raise
    except OSError as error:
        raise CommunityDockerError("community.docker.local-daemon-required") from error
    return endpoint


def local_docker_binding(
    state: Path, base_environment: dict[str, str]
) -> tuple[str, str, dict[str, str]]:
    """Resolve, bind, and return one local daemon without installation secrets.

    The caller must hold the installation CLI lock for the full operation. It
    may add protected installation values only after this function returns.
    """
    state = state.absolute()
    _private_state_directory(state)
    existing = _read_binding(state)
    environment = _sanitize_environment(dict(base_environment))
    discovered = shutil.which("docker", path=environment.get("PATH", os.defpath))
    if discovered is None:
        raise CommunityDockerError("community.docker.unavailable")
    # A relative PATH entry must not resolve to a different binary when the
    # caller later runs Compose with a repository working directory.
    executable = os.path.abspath(discovered)

    endpoint = _require_local_socket(
        _docker_json(
            [executable, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            dict(environment),
        )
    )
    # Context selects the socket exactly once. All subsequent commands receive
    # the explicit endpoint and cannot be redirected through ambient settings.
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        environment.pop(key, None)
    daemon_id = _docker_json(
        [executable, "--host", endpoint, "info", "--format", "{{json .ID}}"],
        dict(environment),
    )
    if not isinstance(daemon_id, str) or _DAEMON_ID.fullmatch(daemon_id) is None:
        raise CommunityDockerError("community.docker.daemon-identity-invalid")

    current = _binding_document(endpoint, daemon_id)
    if existing is None:
        _write_binding_once(state, current)
    elif existing != current:
        raise CommunityDockerError("community.docker.binding-mismatch")
    return executable, endpoint, environment
