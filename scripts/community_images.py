"""Resolve explicitly selected immutable images on one bound local Docker daemon.

This host-side helper has no installation-file, provider, or serving authority.
Matching a registry digest to a local image ID is integrity binding, not proof
of publisher identity, a verified signature, or release qualification.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import time
from typing import Mapping
from urllib.parse import urlsplit


DEFAULT_IMAGES = {
    "IIP_COMMUNITY_POSTGRES_IMAGE": "docker.io/library/postgres@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15",
    "IIP_COMMUNITY_COLLECTOR_IMAGE": "docker.io/otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5",
    "IIP_COMMUNITY_PROMETHEUS_IMAGE": "docker.io/prom/prometheus@sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893",
    "IIP_COMMUNITY_GRAFANA_IMAGE": "docker.io/grafana/grafana@sha256:e932bd6ed0e026595b08483cd0141e5103e1ab7ff8604839ff899b8dc54cabcb",
}
_ID = re.compile(r"sha256:[0-9a-f]{64}")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_PATH_COMPONENT = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
_ARCHITECTURES = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}
_INSPECTION_TIMEOUT_SECONDS = 15
_PULL_TIMEOUT_SECONDS = 240
_MAX_OUTPUT_BYTES = 64 * 1024
_DAEMON_FORMAT = '{"os":{{json .OSType}},"architecture":{{json .Architecture}}}'
_IMAGE_FORMAT = ('{"os":{{json .Os}},"architecture":{{json .Architecture}},'
                 '"id":{{json .Id}},"repoDigests":{{json .RepoDigests}}}')


class CommunityImageError(ValueError):
    """Stable errors omit registry responses, command output and credentials."""


def require_digest_reference(reference: str) -> str:
    """Require an explicit lower-case registry/repository@sha256 reference."""
    if not isinstance(reference, str) or not 1 <= len(reference) <= 512:
        raise CommunityImageError("community.images.reference-invalid")
    parts = reference.split("@")
    if len(parts) != 2 or not _ID.fullmatch(parts[1]):
        raise CommunityImageError("community.images.reference-invalid")
    repository, _ = parts
    components = repository.split("/")
    if len(components) < 2 or len(repository) > 255:
        raise CommunityImageError("community.images.reference-invalid")
    registry = components[0]
    host, separator, port = registry.partition(":")
    if (len(host) > 253 or not ("." in host or separator or host == "localhost")
            or any(not _HOST_LABEL.fullmatch(label) for label in host.split("."))
            or separator and (not port.isascii() or not port.isdecimal()
                              or not 1 <= int(port) <= 65535 or str(int(port)) != port)
            or any(not _PATH_COMPONENT.fullmatch(part) for part in components[1:])):
        raise CommunityImageError("community.images.reference-invalid")
    return reference


def selected_image_references(image: str) -> dict[str, str]:
    selected = {"IIP_COMMUNITY_IMAGE": require_digest_reference(image), **DEFAULT_IMAGES}
    for reference in selected.values():
        require_digest_reference(reference)
    return selected


def _connection(executable: str, endpoint: str, environment: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    if (not isinstance(executable, str) or not executable.startswith("/")
            or os.path.normpath(executable) != executable
            or any(ord(character) < 32 or ord(character) == 127 for character in executable)):
        raise CommunityImageError("community.images.connection-invalid")
    if not isinstance(endpoint, str) or any(ord(character) < 33 or ord(character) == 127 for character in endpoint):
        raise CommunityImageError("community.images.connection-invalid")
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        raise CommunityImageError("community.images.connection-invalid") from None
    if (parsed.scheme != "unix" or parsed.netloc or parsed.query or parsed.fragment
            or not parsed.path.startswith("/") or parsed.path.startswith("//")
            or os.path.normpath(parsed.path) != parsed.path or "%" in parsed.path
            or endpoint != f"unix://{parsed.path}"):
        raise CommunityImageError("community.images.connection-invalid")
    if not isinstance(environment, Mapping) or any(
        not isinstance(key, str) or not key or "=" in key or "\0" in key
        or not isinstance(value, str) or "\0" in value
        for key, value in environment.items()
    ):
        raise CommunityImageError("community.images.environment-invalid")
    excluded = {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_DEFAULT_PLATFORM"}
    sanitized = {
        key: value for key, value in environment.items()
        if not key.startswith(("IIP_", "COMPOSE_", "PG")) and key not in excluded
    }
    return [executable, "--host", endpoint], sanitized


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise CommunityImageError("community.images.output-invalid")
        document[key] = value
    return document


def _docker_json(command: list[str], environment: dict[str, str]) -> object:
    """Read only bounded formatted stdout; never capture Docker's error text."""
    process: subprocess.Popen | None = None
    try:
        deadline = time.monotonic() + _INSPECTION_TIMEOUT_SECONDS
        process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert process.stdout is not None
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise CommunityImageError("community.images.command-failed")
                chunk = os.read(process.stdout.fileno(), min(4096, _MAX_OUTPUT_BYTES + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _MAX_OUTPUT_BYTES:
                    raise CommunityImageError("community.images.output-invalid")
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise CommunityImageError("community.images.command-failed")
        return json.loads(output.decode("utf-8"), object_pairs_hook=_object)
    except CommunityImageError:
        raise
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError, RecursionError):
        raise CommunityImageError("community.images.command-failed") from None
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


def _native_architecture(document: object) -> str:
    if (not isinstance(document, dict) or set(document) != {"os", "architecture"}
            or document.get("os") != "linux" or not isinstance(document.get("architecture"), str)
            or document["architecture"] not in _ARCHITECTURES):
        raise CommunityImageError("community.images.platform-unsupported")
    return _ARCHITECTURES[document["architecture"]]


def _canonical_repository_digest(reference: str) -> str:
    """Docker Hub inspect aliases only; selection itself permits no shorthand."""
    if not isinstance(reference, str) or len(reference) > 512:
        raise CommunityImageError("community.images.identity-invalid")
    repository, separator, digest = reference.partition("@")
    if not separator or not _ID.fullmatch(digest):
        raise CommunityImageError("community.images.identity-invalid")
    first, slash, remainder = repository.partition("/")
    if first == "index.docker.io":
        repository = "docker.io/" + remainder
    elif not slash:
        repository = "docker.io/library/" + repository
    elif "." not in first and ":" not in first and first != "localhost":
        repository = "docker.io/" + repository
    if repository.startswith("docker.io/") and repository.count("/") == 1:
        repository = "docker.io/library/" + repository.removeprefix("docker.io/")
    try:
        return require_digest_reference(repository + "@" + digest)
    except CommunityImageError:
        raise CommunityImageError("community.images.identity-invalid") from None


def _resolved_id(document: object, reference: str, architecture: str) -> str:
    if (not isinstance(document, dict)
            or set(document) != {"os", "architecture", "id", "repoDigests"}
            or document.get("os") != "linux"
            or not isinstance(document.get("architecture"), str)
            or _ARCHITECTURES.get(document["architecture"]) != architecture
            or not isinstance(document.get("id"), str) or not _ID.fullmatch(document["id"])
            or not isinstance(document.get("repoDigests"), list)
            or not 1 <= len(document["repoDigests"]) <= 128):
        raise CommunityImageError("community.images.identity-invalid")
    bindings = {_canonical_repository_digest(item) for item in document["repoDigests"]}
    if _canonical_repository_digest(reference) not in bindings:
        raise CommunityImageError("community.images.identity-invalid")
    return document["id"]


def resolve_images(
    image: str, *, executable: str, endpoint: str,
    environment: Mapping[str, str], pull: bool = False,
) -> dict[str, str]:
    """Return five exact native image IDs; registry access requires explicit pull.

    The caller must already hold the installation lock and have bound this
    exact local Docker connection. No image builds, containers, installations,
    recovery state, or caller environment are modified by this helper.
    """
    if type(pull) is not bool:
        raise CommunityImageError("community.images.pull-intent-invalid")
    selected = selected_image_references(image)
    command, sanitized = _connection(executable, endpoint, environment)
    architecture = _native_architecture(_docker_json(
        [*command, "info", "--format", _DAEMON_FORMAT], dict(sanitized),
    ))
    resolved: dict[str, str] = {}
    for key, reference in selected.items():
        if pull:
            try:
                subprocess.run(
                    [*command, "pull", "--platform", f"linux/{architecture}", reference],
                    env=dict(sanitized), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=True, timeout=_PULL_TIMEOUT_SECONDS,
                )
            except (OSError, ValueError, subprocess.SubprocessError):
                raise CommunityImageError("community.images.command-failed") from None
        document = _docker_json(
            [*command, "image", "inspect", "--format", _IMAGE_FORMAT, reference], dict(sanitized),
        )
        resolved[key] = _resolved_id(document, reference, architecture)
    return resolved
