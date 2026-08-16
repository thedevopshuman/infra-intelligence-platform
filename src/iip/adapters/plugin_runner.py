"""Signed, digest-pinned OCI plugin execution with a deny-by-default sandbox."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import selectors
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from iip.application.investigate import canonical_digest
from iip.application.ports import ActorContext


_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
_KEY_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}")
_PLUGIN_ID = re.compile(r"[a-z][a-z0-9-]{2,63}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
_REQUEST_ID = re.compile(r"pin_[a-f0-9]{32}")
_SESSION_ID = re.compile(r"psn_[a-f0-9]{32}")
_CAPABILITY = re.compile(r"[a-z][a-z0-9-]{2,63}")
_METHOD = re.compile(r"[a-z][a-z0-9.-]{1,63}")
_OCI_REFERENCE = re.compile(
    r"(?:[a-z0-9][a-z0-9._:/-]{0,957}@)?sha256:[a-f0-9]{64}"
)


class PluginRunnerError(RuntimeError):
    """Fail-closed runner failure carrying only a stable external code."""


@dataclass(frozen=True)
class PluginRunnerConfiguration:
    docker_binary: str = "docker"
    memory_megabytes: int = 256
    cpu_count: float = 0.5
    pids_limit: int = 64
    tmpfs_megabytes: int = 16
    max_input_bytes: int = 1_048_576

    def validate(self) -> None:
        if (
            not isinstance(self.docker_binary, str)
            or not self.docker_binary
            or "\x00" in self.docker_binary
            or isinstance(self.memory_megabytes, bool)
            or not isinstance(self.memory_megabytes, int)
            or not 32 <= self.memory_megabytes <= 4096
            or isinstance(self.cpu_count, bool)
            or not isinstance(self.cpu_count, (int, float))
            or not 0.1 <= self.cpu_count <= 8
            or isinstance(self.pids_limit, bool)
            or not isinstance(self.pids_limit, int)
            or not 8 <= self.pids_limit <= 1024
            or isinstance(self.tmpfs_megabytes, bool)
            or not isinstance(self.tmpfs_megabytes, int)
            or not 1 <= self.tmpfs_megabytes <= 256
            or isinstance(self.max_input_bytes, bool)
            or not isinstance(self.max_input_bytes, int)
            or not 1024 <= self.max_input_bytes <= 16_777_216
        ):
            raise PluginRunnerError("plugin.runner.configuration-invalid")


@dataclass(frozen=True)
class TrustedPluginKey:
    key_id: str
    publisher: str
    public_key: Ed25519PublicKey


class PluginTrustStore:
    """Closed publisher/key map built from non-secret Ed25519 public keys."""

    def __init__(self, keys: tuple[TrustedPluginKey, ...]) -> None:
        if not keys or len(keys) > 256:
            raise PluginRunnerError("plugin.trust.configuration-invalid")
        indexed: dict[str, TrustedPluginKey] = {}
        for key in keys:
            if key.key_id in indexed:
                raise PluginRunnerError("plugin.trust.configuration-invalid")
            indexed[key.key_id] = key
        self._keys = indexed

    @classmethod
    def from_json(cls, raw: str) -> "PluginTrustStore":
        try:
            document = json.loads(raw)
            if not isinstance(document, dict) or set(document) != {"keys"}:
                raise ValueError
            entries = document["keys"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
                raise ValueError
            keys = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {
                    "keyId",
                    "publisher",
                    "publicKey",
                }:
                    raise ValueError
                key_id = entry["keyId"]
                publisher = entry["publisher"]
                encoded = entry["publicKey"]
                if (
                    not isinstance(key_id, str)
                    or _KEY_ID.fullmatch(key_id) is None
                    or not isinstance(publisher, str)
                    or not 1 <= len(publisher) <= 128
                    or not isinstance(encoded, str)
                ):
                    raise ValueError
                public_bytes = _decode_base64url(encoded, expected_bytes=32)
                keys.append(
                    TrustedPluginKey(
                        key_id,
                        publisher,
                        Ed25519PublicKey.from_public_bytes(public_bytes),
                    )
                )
            return cls(tuple(keys))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise PluginRunnerError("plugin.trust.configuration-invalid") from None

    def verify(self, manifest: Mapping[str, object]) -> str:
        try:
            metadata = manifest["metadata"]
            spec = manifest["spec"]
            if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
                raise ValueError
            artifact = spec["artifact"]
            signature = artifact["signature"] if isinstance(artifact, Mapping) else None
            if (
                not isinstance(artifact, Mapping)
                or set(artifact) != {"type", "reference", "digest", "signature"}
                or not isinstance(signature, Mapping)
                or set(signature) != {"algorithm", "keyId", "value"}
                or artifact.get("type") != "oci-image"
                or signature.get("algorithm") != "ed25519"
            ):
                raise ValueError
            plugin_id = metadata["id"]
            version = metadata["version"]
            publisher = metadata["publisher"]
            protocol = spec["protocolVersion"]
            reference = artifact["reference"]
            digest = artifact["digest"]
            key_id = signature["keyId"]
            value = signature["value"]
            if (
                not isinstance(plugin_id, str)
                or _PLUGIN_ID.fullmatch(plugin_id) is None
                or not isinstance(version, str)
                or _VERSION.fullmatch(version) is None
                or not isinstance(publisher, str)
                or not isinstance(reference, str)
                or len(reference) > 1024
                or _OCI_REFERENCE.fullmatch(reference) is None
                or not isinstance(digest, str)
                or _DIGEST.fullmatch(digest) is None
                or not (reference == digest or reference.endswith("@" + digest))
                or not isinstance(key_id, str)
                or not isinstance(value, str)
                or protocol != "1.0"
            ):
                raise ValueError
            trusted = self._keys.get(key_id)
            if trusted is None or trusted.publisher != publisher:
                raise PluginRunnerError("plugin.signature.untrusted")
            signed = {
                "apiVersion": "iip.plugin-signature/v1",
                "pluginId": plugin_id,
                "pluginVersion": version,
                "protocolVersion": protocol,
                "artifact": {
                    "type": "oci-image",
                    "reference": reference,
                    "digest": digest,
                },
            }
            trusted.public_key.verify(
                _decode_base64url(value, expected_bytes=64),
                _canonical_bytes(signed),
            )
            return reference
        except PluginRunnerError:
            raise
        except (InvalidSignature, KeyError, TypeError, ValueError):
            raise PluginRunnerError("plugin.signature.invalid") from None


class PluginContainerTransport(Protocol):
    def run(
        self,
        image_reference: str,
        payload: bytes,
        *,
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> bytes:
        """Execute one invocation in a bounded container and return stdout."""


class DockerCliPluginTransport:
    """Docker CLI transport with no network, mounts, capabilities, or writable root."""

    STDERR_LIMIT = 8192

    def __init__(self, configuration: PluginRunnerConfiguration) -> None:
        configuration.validate()
        self._configuration = configuration

    def run(
        self,
        image_reference: str,
        payload: bytes,
        *,
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> bytes:
        with tempfile.TemporaryDirectory(prefix="iip-plugin-") as directory:
            cidfile = Path(directory) / "container.id"
            command = [
                self._configuration.docker_binary,
                "run",
                "--rm",
                "--interactive",
                "--pull=never",
                "--network=none",
                "--read-only",
                "--user=65532:65532",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--label=iip.plugin-runner=true",
                f"--pids-limit={self._configuration.pids_limit}",
                f"--memory={self._configuration.memory_megabytes}m",
                f"--memory-swap={self._configuration.memory_megabytes}m",
                f"--cpus={self._configuration.cpu_count}",
                "--ulimit=nofile=256:256",
                (
                    "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size="
                    f"{self._configuration.tmpfs_megabytes}m"
                ),
                "--log-driver=none",
                f"--cidfile={cidfile}",
                image_reference,
            ]
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=_docker_environment(),
                )
            except OSError:
                raise PluginRunnerError("plugin.runtime.unavailable") from None
            try:
                assert process.stdin is not None
                try:
                    process.stdin.write(payload)
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    raise PluginRunnerError("plugin.runtime.failed") from None
                stdout, _stderr = self._bounded_output(
                    process,
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=max_output_bytes,
                )
                if process.returncode != 0:
                    raise PluginRunnerError("plugin.runtime.failed")
                return stdout
            except PluginRunnerError:
                self._stop(process, cidfile)
                raise
            finally:
                if process.poll() is None:
                    self._stop(process, cidfile)

    def _bounded_output(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> tuple[bytes, bytes]:
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
        limits = {
            process.stdout: max_output_bytes,
            process.stderr: self.STDERR_LIMIT,
        }
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_seconds
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PluginRunnerError("plugin.runtime.deadline-exceeded")
                events = selector.select(min(remaining, 0.25))
                if not events and process.poll() is not None:
                    events = [(key, selectors.EVENT_READ) for key in selector.get_map().values()]
                for key, _ in events:
                    stream = key.fileobj
                    try:
                        chunk = os.read(stream.fileno(), 65_536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    streams[stream].extend(chunk)
                    if len(streams[stream]) > limits[stream]:
                        raise PluginRunnerError("plugin.runtime.output-limit-exceeded")
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise PluginRunnerError("plugin.runtime.deadline-exceeded") from None
            return bytes(streams[process.stdout]), bytes(streams[process.stderr])
        finally:
            selector.close()

    def _stop(self, process: subprocess.Popen[bytes], cidfile: Path) -> None:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            container_id = cidfile.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if re.fullmatch(r"[a-f0-9]{12,64}", container_id):
            try:
                subprocess.run(
                    [self._configuration.docker_binary, "kill", container_id],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                    env=_docker_environment(),
                )
            except (OSError, subprocess.TimeoutExpired):
                pass


class InMemoryPluginExecutionLedger:
    """Atomic process-local request accounting for the standalone runner."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._claimed: dict[str, set[str]] = {}

    def claim(self, session_id: str, request_id: str, maximum: int) -> None:
        with self._lock:
            requests = self._claimed.setdefault(session_id, set())
            if request_id in requests:
                raise PluginRunnerError("plugin.request.duplicate")
            if len(requests) >= maximum:
                raise PluginRunnerError("plugin.request.limit-exceeded")
            requests.add(request_id)


class SignedDockerPluginRunner:
    """Verify trust/session scope, then run exactly one isolated stdio request."""

    def __init__(
        self,
        trust_store: PluginTrustStore,
        configuration: PluginRunnerConfiguration | None = None,
        transport: PluginContainerTransport | None = None,
        ledger: InMemoryPluginExecutionLedger | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._configuration = configuration or PluginRunnerConfiguration()
        self._configuration.validate()
        self._trust_store = trust_store
        self._transport = transport or DockerCliPluginTransport(self._configuration)
        self._ledger = ledger or InMemoryPluginExecutionLedger()
        self._now = now
        self._monotonic = monotonic

    def run(
        self,
        actor: ActorContext,
        manifest: Mapping[str, object],
        session: Mapping[str, object],
        invocation: Mapping[str, object],
        capability_token: str,
    ) -> Mapping[str, object]:
        image_reference = self._trust_store.verify(manifest)
        metadata, spec, session_metadata, session_spec = self._validate_scope(
            actor, manifest, session, invocation, capability_token
        )
        permissions = manifest["spec"]["permissions"]
        if not isinstance(permissions, Mapping) or any(
            permissions.get(name) for name in ("network", "secrets", "actions")
        ):
            raise PluginRunnerError("plugin.permission.unsupported")
        limits = session_spec["limits"]
        self._ledger.claim(
            session_metadata["id"], metadata["id"], limits["maxRequests"]
        )
        payload = _canonical_bytes(invocation) + b"\n"
        if len(payload) > self._configuration.max_input_bytes:
            raise PluginRunnerError("plugin.input.too-large")
        start = self._monotonic()
        output_bytes = self._transport.run(
            image_reference,
            payload,
            timeout_seconds=limits["maxWallTimeSeconds"],
            max_output_bytes=limits["maxOutputBytes"],
        )
        elapsed = max(0, round((self._monotonic() - start) * 1000))
        try:
            output = json.loads(output_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PluginRunnerError("plugin.output.invalid") from None
        if not isinstance(output, dict):
            raise PluginRunnerError("plugin.output.invalid")
        plugin_metadata = manifest["metadata"]
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationResult",
            "metadata": {
                "id": metadata["id"],
                "sessionId": session_metadata["id"],
                "tenantId": actor.tenant_id,
                "pluginId": plugin_metadata["id"],
                "pluginVersion": plugin_metadata["version"],
                "completedAt": self._now().isoformat().replace("+00:00", "Z"),
            },
            "spec": {
                "status": "succeeded",
                "outputDigest": canonical_digest(output),
                "output": output,
                "usage": {
                    "wallTimeMillis": min(elapsed, 3_600_000),
                    "outputBytes": len(output_bytes),
                },
            },
        }

    def _validate_scope(
        self,
        actor: ActorContext,
        manifest: Mapping[str, object],
        session: Mapping[str, object],
        invocation: Mapping[str, object],
        capability_token: str,
    ) -> tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object], Mapping[str, object]]:
        try:
            metadata = invocation["metadata"]
            spec = invocation["spec"]
            session_metadata = session["metadata"]
            session_spec = session["spec"]
            manifest_spec = manifest["spec"]
            manifest_metadata = manifest["metadata"]
            if not all(
                isinstance(value, Mapping)
                for value in (
                    metadata,
                    spec,
                    session_metadata,
                    session_spec,
                    manifest_spec,
                    manifest_metadata,
                )
            ):
                raise ValueError
            entrypoint = manifest_spec.get("entrypoint")
            if (
                invocation.get("apiVersion") != "iip.platform/v1alpha1"
                or invocation.get("kind") != "PluginInvocation"
                or set(invocation) != {"apiVersion", "kind", "metadata", "spec"}
                or set(metadata)
                != {"id", "sessionId", "tenantId", "actorId", "createdAt", "deadline"}
                or set(spec)
                != {"manifestDigest", "capability", "method", "input"}
                or session.get("apiVersion") != "iip.platform/v1alpha1"
                or session.get("kind") != "PluginSession"
                or session.get("status") != "ready"
                or not isinstance(entrypoint, Mapping)
                or entrypoint.get("transport") != "stdio"
                or not isinstance(metadata.get("id"), str)
                or _REQUEST_ID.fullmatch(metadata["id"]) is None
                or not isinstance(session_metadata.get("id"), str)
                or _SESSION_ID.fullmatch(session_metadata["id"]) is None
                or metadata.get("sessionId") != session_metadata["id"]
                or metadata.get("tenantId") != actor.tenant_id
                or metadata.get("actorId") != actor.actor_id
                or session_metadata.get("tenantId") != actor.tenant_id
                or session_metadata.get("pluginId") != manifest_metadata.get("id")
                or session_metadata.get("pluginVersion")
                != manifest_metadata.get("version")
                or session_spec.get("protocolVersion")
                != manifest_spec.get("protocolVersion")
                or spec.get("manifestDigest") != canonical_digest(manifest)
                or session_spec.get("manifestDigest") != canonical_digest(manifest)
                or not isinstance(spec.get("capability"), str)
                or _CAPABILITY.fullmatch(spec["capability"]) is None
                or not isinstance(spec.get("method"), str)
                or _METHOD.fullmatch(spec["method"]) is None
                or not isinstance(spec.get("input"), Mapping)
                or not isinstance(capability_token, str)
                or not 32 <= len(capability_token) <= 8192
                or any(character.isspace() for character in capability_token)
                or session_spec.get("capabilityTokenDigest")
                != "sha256:"
                + hashlib.sha256(capability_token.encode("utf-8")).hexdigest()
            ):
                raise ValueError
            created = _timestamp(metadata["createdAt"])
            deadline = _timestamp(metadata["deadline"])
            expires = _timestamp(session_spec["expiresAt"])
            now = self._now()
            if not created <= now <= deadline <= expires:
                raise PluginRunnerError("plugin.request.expired")
            capability = spec["capability"]
            method = spec["method"]
            if capability not in session_spec["grantedCapabilities"]:
                raise PluginRunnerError("plugin.capability.not-granted")
            interfaces = manifest_spec.get("interfaces", [])
            if not any(
                isinstance(interface, Mapping)
                and interface.get("capability") == capability
                and interface.get("method") == method
                for interface in interfaces
            ):
                raise PluginRunnerError("plugin.method.not-declared")
            limits = session_spec["limits"]
            if (
                not isinstance(limits, Mapping)
                or isinstance(limits.get("maxRequests"), bool)
                or not isinstance(limits.get("maxRequests"), int)
                or not 1 <= limits["maxRequests"] <= 10_000
                or isinstance(limits.get("maxWallTimeSeconds"), bool)
                or not isinstance(limits.get("maxWallTimeSeconds"), int)
                or not 1 <= limits["maxWallTimeSeconds"] <= 3600
                or isinstance(limits.get("maxOutputBytes"), bool)
                or not isinstance(limits.get("maxOutputBytes"), int)
                or not 1024 <= limits["maxOutputBytes"] <= 16_777_216
            ):
                raise ValueError
            return metadata, spec, session_metadata, session_spec
        except PluginRunnerError:
            raise
        except (KeyError, TypeError, ValueError):
            raise PluginRunnerError("plugin.request.invalid") from None


def _canonical_bytes(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _decode_base64url(value: str, *, expected_bytes: int) -> bytes:
    if not isinstance(value, str) or "=" in value:
        raise ValueError
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )
    if len(decoded) != expected_bytes:
        raise ValueError
    return decoded


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _docker_environment() -> dict[str, str]:
    return {
        name: os.environ[name]
        for name in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT")
        if name in os.environ
    }
