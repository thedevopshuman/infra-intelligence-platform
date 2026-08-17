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
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from iip.application.investigate import canonical_digest
from iip.application.plugin_mediation import PluginMediationError, PluginMediationService
from iip.application.ports import (
    ActorContext,
    PersistenceError,
    PluginInvocationClaim,
    PluginInvocationLedger,
)


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
_TERMINAL_RUNTIME_ERRORS = frozenset(
    {
        "plugin.output.invalid",
        "plugin.runtime.cancelled",
        "plugin.runtime.deadline-exceeded",
        "plugin.runtime.failed",
        "plugin.runtime.output-limit-exceeded",
        "plugin.runtime.unavailable",
    }
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
    mediation_bridge_image_reference: str | None = None

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
            or self.mediation_bridge_image_reference is not None
            and (
                not isinstance(self.mediation_bridge_image_reference, str)
                or _OCI_REFERENCE.fullmatch(self.mediation_bridge_image_reference) is None
            )
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
                or set(signature)
                != {"profile", "algorithm", "keyId", "manifestDigest", "value"}
                or signature.get("profile") != "iip.plugin-signature/v2"
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
            manifest_digest = signature["manifestDigest"]
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
                or not isinstance(manifest_digest, str)
                or _DIGEST.fullmatch(manifest_digest) is None
                or manifest_digest != _unsigned_manifest_digest(manifest)
                or not isinstance(value, str)
                or protocol != "1.0"
            ):
                raise ValueError
            trusted = self._keys.get(key_id)
            if trusted is None or trusted.publisher != publisher:
                raise PluginRunnerError("plugin.signature.untrusted")
            signed = {
                "apiVersion": "iip.plugin-signature/v2",
                "pluginId": plugin_id,
                "pluginVersion": version,
                "protocolVersion": protocol,
                "manifestDigest": manifest_digest,
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
        cancellation_requested: Callable[[], bool] | None = None,
        mediation_handler: Callable[[Mapping[str, object]], Mapping[str, object]]
        | None = None,
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
        cancellation_requested: Callable[[], bool] | None = None,
        mediation_handler: Callable[[Mapping[str, object]], Mapping[str, object]]
        | None = None,
    ) -> bytes:
        with tempfile.TemporaryDirectory(prefix="iip-plugin-", dir="/tmp") as directory:
            cidfile = Path(directory) / "container.id"
            mediation_bridge = (
                _DockerPluginMediationBridge(self._configuration, mediation_handler)
                if mediation_handler is not None
                else None
            )
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
            ]
            if mediation_bridge is not None:
                mediation_bridge.start()
                command.append(mediation_bridge.plugin_mount)
            command.append(image_reference)
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=_docker_environment(),
                )
            except OSError:
                if mediation_bridge is not None:
                    mediation_bridge.close()
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
                    cancellation_requested=cancellation_requested,
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
                if mediation_bridge is not None:
                    mediation_bridge.close()

    def _bounded_output(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: int,
        max_output_bytes: int,
        cancellation_requested: Callable[[], bool] | None,
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
                if cancellation_requested is not None and cancellation_requested():
                    raise PluginRunnerError("plugin.runtime.cancelled")
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


class _DockerPluginMediationBridge:
    """Host-owned stdio bridge to a socket in a fresh Docker volume."""

    MAX_MESSAGE_BYTES = 4_194_304

    def __init__(
        self,
        configuration: PluginRunnerConfiguration,
        handler: Callable[[Mapping[str, object]], Mapping[str, object]],
    ) -> None:
        self._configuration = configuration
        self._handler = handler
        self._volume = "iip-plugin-mediation-" + uuid.uuid4().hex
        self._container_name = self._volume + "-bridge"
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None

    @property
    def plugin_mount(self) -> str:
        return (
            f"--mount=type=volume,source={self._volume},"
            "target=/run/iip-mediation,readonly"
        )

    def start(self) -> None:
        image = self._configuration.mediation_bridge_image_reference
        if image is None:
            raise PluginRunnerError("plugin.mediation.bridge-unavailable")
        self._docker(("volume", "create", self._volume), capture=False)
        try:
            self._initialize_volume(image)
        except PluginRunnerError:
            self.close()
            raise
        command = [
            self._configuration.docker_binary,
            "run",
            "--rm",
            "--interactive",
            f"--name={self._container_name}",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--user=65532:65532",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--label=iip.plugin-mediation-bridge=true",
            "--pids-limit=32",
            "--memory=64m",
            "--memory-swap=64m",
            "--cpus=0.25",
            "--ulimit=nofile=128:128",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=4m",
            "--log-driver=none",
            f"--mount=type=volume,source={self._volume},target=/run/iip-mediation",
            image,
        ]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_docker_environment(),
            )
            ready = self._read_ready(self._process)
            if ready != {"status": "ready"}:
                raise PluginRunnerError("plugin.mediation.bridge-unavailable")
            self._thread = threading.Thread(
                target=self._relay,
                name="iip-plugin-mediation-relay",
                daemon=True,
            )
            self._thread.start()
        except PluginRunnerError:
            self.close()
            raise
        except OSError:
            self.close()
            raise PluginRunnerError("plugin.mediation.bridge-unavailable") from None

    def close(self) -> None:
        if self._process is not None:
            self._docker(
                ("kill", self._container_name),
                capture=False,
                ignore_failure=True,
            )
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except OSError:
                    pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._docker(
            ("volume", "rm", "--force", self._volume),
            capture=False,
            ignore_failure=True,
        )

    def _relay(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        assert self._process.stdin is not None
        while self._process.poll() is None:
            try:
                line = self._process.stdout.readline(self.MAX_MESSAGE_BYTES + 2)
                if not line:
                    return
                if len(line) > self.MAX_MESSAGE_BYTES + 1 or not line.endswith(b"\n"):
                    return
                request = json.loads(line)
                if not isinstance(request, dict):
                    return
                response = self._handler(request)
                if not isinstance(response, Mapping):
                    return
                encoded = _canonical_bytes(response)
                if len(encoded) > self.MAX_MESSAGE_BYTES:
                    return
                self._process.stdin.write(encoded + b"\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError, TypeError, ValueError, json.JSONDecodeError):
                return

    def _initialize_volume(self, image: str) -> None:
        command = [
            self._configuration.docker_binary,
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--user=0:0",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=16",
            "--memory=32m",
            "--memory-swap=32m",
            "--cpus=0.1",
            "--log-driver=none",
            f"--mount=type=volume,source={self._volume},target=/run/iip-mediation",
            image,
            "--initialize",
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                env=_docker_environment(),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PluginRunnerError("plugin.mediation.bridge-unavailable") from None
        if completed.returncode != 0:
            raise PluginRunnerError("plugin.mediation.bridge-unavailable")

    @staticmethod
    def _read_ready(process: subprocess.Popen[bytes]) -> object:
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            events = selector.select(5)
            if not events:
                raise PluginRunnerError("plugin.mediation.bridge-unavailable")
            line = process.stdout.readline(1024)
            if not line.endswith(b"\n"):
                raise PluginRunnerError("plugin.mediation.bridge-unavailable")
            return json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PluginRunnerError("plugin.mediation.bridge-unavailable") from None
        finally:
            selector.close()

    def _docker(
        self,
        arguments: tuple[str, ...],
        *,
        capture: bool,
        ignore_failure: bool = False,
    ) -> None:
        try:
            completed = subprocess.run(
                [self._configuration.docker_binary, *arguments],
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                env=_docker_environment(),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PluginRunnerError("plugin.mediation.bridge-unavailable") from None
        if completed.returncode != 0 and not ignore_failure:
            raise PluginRunnerError("plugin.mediation.bridge-unavailable")


class InMemoryPluginExecutionLedger:
    """Process-local implementation of the durable claim contract for tests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[
            tuple[str, str],
            tuple[str, str, Mapping[str, object] | None],
        ] = {}
        self._statuses: dict[tuple[str, str], Mapping[str, object]] = {}
        self._cancellations: set[tuple[str, str]] = set()
        self._audits: list[Mapping[str, object]] = []

    def claim_plugin_invocation(
        self,
        actor: ActorContext,
        session: Mapping[str, object],
        invocation: Mapping[str, object],
        request_digest: str,
        claimed_at: str,
    ) -> PluginInvocationClaim:
        session_metadata = session.get("metadata")
        session_spec = session.get("spec")
        invocation_metadata = invocation.get("metadata")
        limits = session_spec.get("limits") if isinstance(session_spec, Mapping) else None
        if (
            not isinstance(session_metadata, Mapping)
            or not isinstance(invocation_metadata, Mapping)
            or not isinstance(limits, Mapping)
            or session_metadata.get("tenantId") != actor.tenant_id
            or invocation_metadata.get("tenantId") != actor.tenant_id
            or invocation_metadata.get("sessionId") != session_metadata.get("id")
            or canonical_digest(invocation) != request_digest
        ):
            raise PersistenceError("storage.input-invalid")
        session_id = str(session_metadata["id"])
        request_id = str(invocation_metadata["id"])
        maximum = limits.get("maxRequests")
        if isinstance(maximum, bool) or not isinstance(maximum, int):
            raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, request_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                existing_session, existing_digest, result = existing
                if existing_session != session_id or existing_digest != request_digest:
                    raise PersistenceError("storage.conflict")
                if result is None and key in self._cancellations:
                    return PluginInvocationClaim("cancellation-requested")
                return PluginInvocationClaim(
                    "completed" if result is not None else "in-progress",
                    dict(result) if result is not None else None,
                )
            count = sum(
                1
                for (tenant_id, _), (record_session, _, _) in self._records.items()
                if tenant_id == actor.tenant_id and record_session == session_id
            )
            try:
                created = _timestamp(invocation_metadata["createdAt"])
                deadline = _timestamp(invocation_metadata["deadline"])
                expires = _timestamp(session_spec["expiresAt"])
                claim_time = _timestamp(claimed_at)
            except (KeyError, TypeError, ValueError):
                raise PersistenceError("storage.input-invalid") from None
            if not created <= claim_time <= deadline <= expires:
                raise PersistenceError("plugin.request.expired")
            if count >= maximum:
                raise PersistenceError("plugin.request.limit-exceeded")
            self._records[key] = (session_id, request_digest, None)
            self._statuses[key] = self._claim_status(
                actor,
                session_metadata,
                invocation_metadata,
                request_digest,
                claimed_at,
            )
            return PluginInvocationClaim("claimed")

    def commit_plugin_invocation_result(
        self,
        actor: ActorContext,
        request_digest: str,
        result: Mapping[str, object],
    ) -> None:
        metadata = result.get("metadata")
        spec = result.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("tenantId") != actor.tenant_id
            or not isinstance(spec, Mapping)
        ):
            raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, str(metadata.get("id")))
        with self._lock:
            existing = self._records.get(key)
            if existing is None:
                raise PersistenceError("storage.not-found")
            session_id, existing_digest, current = existing
            if (
                existing_digest != request_digest
                or metadata.get("sessionId") != session_id
            ):
                raise PersistenceError("storage.conflict")
            value = dict(result)
            if current is not None and dict(current) != value:
                raise PersistenceError("storage.conflict")
            if current is not None:
                return
            if key in self._cancellations and spec.get("status") != "cancelled":
                raise PersistenceError("plugin.request.cancellation-pending")
            self._records[key] = (session_id, existing_digest, value)
            self._statuses[key] = self._terminal_status(self._statuses.get(key), value)

    def get_plugin_invocation_status(
        self, actor: ActorContext, request_id: str
    ) -> Mapping[str, object] | None:
        with self._lock:
            status = self._statuses.get((actor.tenant_id, request_id))
            return _copy_document(status) if status is not None else None

    def request_plugin_invocation_cancellation(
        self,
        actor: ActorContext,
        request_id: str,
        status: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        status_metadata = status.get("metadata")
        audit_metadata = audit_document.get("metadata")
        if (
            not isinstance(status_metadata, Mapping)
            or status_metadata.get("tenantId") != actor.tenant_id
            or not isinstance(audit_metadata, Mapping)
            or audit_metadata.get("tenantId") != actor.tenant_id
        ):
            raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, request_id)
        with self._lock:
            current = self._statuses.get(key)
            if current is None:
                raise PersistenceError("storage.not-found")
            current_spec = current.get("spec")
            if not isinstance(current_spec, Mapping):
                raise PersistenceError("storage.corrupt")
            if current_spec.get("state") != "claimed":
                return _copy_document(current)
            self._statuses[key] = _copy_document(status)
            self._cancellations.add(key)
            self._audits.append(_copy_document(audit_document))
            return _copy_document(status)

    def plugin_invocation_cancellation_requested(
        self, actor: ActorContext, request_id: str, request_digest: str
    ) -> bool:
        key = (actor.tenant_id, request_id)
        with self._lock:
            record = self._records.get(key)
            if record is None or record[1] != request_digest:
                raise PersistenceError("storage.not-found")
            return key in self._cancellations and record[2] is None

    def reconcile_plugin_invocation(
        self,
        actor: ActorContext,
        request_id: str,
        observed_at: str,
        result: Mapping[str, object],
        status: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        for document in (result, status, audit_document):
            metadata = document.get("metadata")
            if (
                not isinstance(metadata, Mapping)
                or metadata.get("tenantId") != actor.tenant_id
            ):
                raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, request_id)
        with self._lock:
            current = self._statuses.get(key)
            if current is None:
                raise PersistenceError("storage.not-found")
            current_spec = current.get("spec")
            if not isinstance(current_spec, Mapping):
                raise PersistenceError("storage.corrupt")
            if current_spec.get("state") in {"succeeded", "failed", "cancelled"}:
                return _copy_document(current)
            result_spec = result.get("spec")
            status_spec = status.get("spec")
            expected_state = (
                "cancelled"
                if current_spec.get("state") == "cancellation-requested"
                else "failed"
            )
            if (
                not isinstance(result_spec, Mapping)
                or not isinstance(status_spec, Mapping)
                or result_spec.get("status") != expected_state
                or status_spec.get("state") != expected_state
            ):
                raise PersistenceError("plugin.request.cancellation-pending")
            if _timestamp(observed_at) < _timestamp(current_spec.get("deadline")):
                raise PersistenceError("plugin.reconciliation.deadline-live")
            record = self._records.get(key)
            if record is None or record[2] is not None:
                raise PersistenceError("storage.conflict")
            self._records[key] = (record[0], record[1], _copy_document(result))
            self._statuses[key] = _copy_document(status)
            self._audits.append(_copy_document(audit_document))
            return _copy_document(status)

    @staticmethod
    def _claim_status(
        actor: ActorContext,
        session_metadata: Mapping[str, object],
        invocation_metadata: Mapping[str, object],
        request_digest: str,
        claimed_at: str,
    ) -> dict[str, object]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationStatus",
            "metadata": {
                "id": invocation_metadata["id"],
                "sessionId": session_metadata["id"],
                "tenantId": actor.tenant_id,
                "pluginId": session_metadata["pluginId"],
                "pluginVersion": session_metadata["pluginVersion"],
                "updatedAt": claimed_at,
            },
            "spec": {
                "requestDigest": request_digest,
                "state": "claimed",
                "claimedAt": claimed_at,
                "deadline": invocation_metadata["deadline"],
            },
        }

    @staticmethod
    def _terminal_status(
        current: Mapping[str, object] | None,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        if current is None:
            raise PersistenceError("storage.corrupt")
        metadata = current.get("metadata")
        spec = current.get("spec")
        result_metadata = result.get("metadata")
        result_spec = result.get("spec")
        if not all(
            isinstance(value, Mapping)
            for value in (metadata, spec, result_metadata, result_spec)
        ):
            raise PersistenceError("storage.corrupt")
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationStatus",
            "metadata": {
                **dict(metadata),
                "updatedAt": result_metadata["completedAt"],
            },
            "spec": {
                **dict(spec),
                "state": result_spec["status"],
                "completedAt": result_metadata["completedAt"],
                "resultRef": (
                    f"plugin-result://{metadata['tenantId']}/sessions/"
                    f"{metadata['sessionId']}/invocations/{metadata['id']}"
                ),
            },
        }


class SignedDockerPluginRunner:
    """Verify trust/session scope, then run exactly one isolated stdio request."""

    def __init__(
        self,
        trust_store: PluginTrustStore,
        configuration: PluginRunnerConfiguration | None = None,
        transport: PluginContainerTransport | None = None,
        ledger: PluginInvocationLedger | None = None,
        mediation: PluginMediationService | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._configuration = configuration or PluginRunnerConfiguration()
        self._configuration.validate()
        self._trust_store = trust_store
        self._transport = transport or DockerCliPluginTransport(self._configuration)
        self._ledger = ledger or InMemoryPluginExecutionLedger()
        self._mediation = mediation
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
        if not isinstance(permissions, Mapping):
            raise PluginRunnerError("plugin.permission.unsupported")
        mediation_handler = None
        declared_mediation = bool(
            permissions.get("network")
            or permissions.get("secrets")
            or permissions.get("actions")
        )
        has_grants = bool(
            spec.get("mediationGrants") or spec.get("actionMediationGrants")
        )
        if declared_mediation or has_grants:
            if self._mediation is None or not has_grants:
                raise PluginRunnerError("plugin.permission.unsupported")
            try:
                mediation_handler = self._mediation.bind(
                    actor, manifest, invocation
                ).handle
            except PluginMediationError as error:
                raise PluginRunnerError(str(error)) from None
        payload = _canonical_bytes(invocation) + b"\n"
        if len(payload) > self._configuration.max_input_bytes:
            raise PluginRunnerError("plugin.input.too-large")
        request_digest = canonical_digest(invocation)
        try:
            claim = self._ledger.claim_plugin_invocation(
                actor,
                session,
                invocation,
                request_digest,
                self._now().isoformat().replace("+00:00", "Z"),
            )
        except PersistenceError as error:
            raise PluginRunnerError(_persistence_code(error)) from None
        if claim.state == "completed":
            if not isinstance(claim.result, Mapping):
                raise PluginRunnerError("plugin.execution.persistence-corrupt")
            return dict(claim.result)
        if claim.state == "in-progress":
            raise PluginRunnerError("plugin.request.reconciliation-required")
        if claim.state not in {"claimed", "cancellation-requested"} or claim.result is not None:
            raise PluginRunnerError("plugin.execution.persistence-corrupt")

        limits = session_spec["limits"]
        start = self._monotonic()
        if claim.state == "cancellation-requested":
            result = self._result(
                actor,
                manifest,
                metadata,
                session_metadata,
                status="cancelled",
                wall_time_millis=0,
                output_bytes=0,
                error_code="plugin.runtime.cancelled",
            )
        else:
            try:
                transport_arguments = {
                    "timeout_seconds": limits["maxWallTimeSeconds"],
                    "max_output_bytes": limits["maxOutputBytes"],
                    "cancellation_requested": lambda: self._cancellation_requested(
                        actor, str(metadata["id"]), request_digest
                    ),
                }
                if mediation_handler is None:
                    output_bytes = self._transport.run(
                        image_reference, payload, **transport_arguments
                    )
                else:
                    output_bytes = self._transport.run(
                        image_reference,
                        payload,
                        mediation_handler=mediation_handler,
                        **transport_arguments,
                    )
                try:
                    output = json.loads(output_bytes)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise PluginRunnerError("plugin.output.invalid") from None
                if not isinstance(output, dict):
                    raise PluginRunnerError("plugin.output.invalid")
                result = self._result(
                    actor,
                    manifest,
                    metadata,
                    session_metadata,
                    status="succeeded",
                    wall_time_millis=self._elapsed_millis(start),
                    output_bytes=len(output_bytes),
                    output=output,
                )
            except PluginRunnerError as error:
                code = _runtime_error_code(error)
                result = self._result(
                    actor,
                    manifest,
                    metadata,
                    session_metadata,
                    status="cancelled" if code == "plugin.runtime.cancelled" else "failed",
                    wall_time_millis=self._elapsed_millis(start),
                    output_bytes=0,
                    error_code=code,
                )
            except Exception:
                result = self._result(
                    actor,
                    manifest,
                    metadata,
                    session_metadata,
                    status="failed",
                    wall_time_millis=self._elapsed_millis(start),
                    output_bytes=0,
                    error_code="plugin.runtime.failed",
                )
        try:
            self._ledger.commit_plugin_invocation_result(
                actor, request_digest, result
            )
        except PersistenceError as error:
            if str(error) == "plugin.request.cancellation-pending":
                result = self._result(
                    actor,
                    manifest,
                    metadata,
                    session_metadata,
                    status="cancelled",
                    wall_time_millis=self._elapsed_millis(start),
                    output_bytes=0,
                    error_code="plugin.runtime.cancelled",
                )
                try:
                    self._ledger.commit_plugin_invocation_result(
                        actor, request_digest, result
                    )
                except PersistenceError as cancellation_error:
                    recovered = self._recover_completed(
                        actor,
                        session,
                        invocation,
                        request_digest,
                        cancellation_error,
                    )
                    if recovered is not None:
                        return recovered
                    raise PluginRunnerError(
                        _persistence_code(cancellation_error)
                    ) from None
                return result
            recovered = self._recover_completed(
                actor, session, invocation, request_digest, error
            )
            if recovered is not None:
                return recovered
            raise PluginRunnerError(_persistence_code(error)) from None
        return result

    def _cancellation_requested(
        self, actor: ActorContext, request_id: str, request_digest: str
    ) -> bool:
        try:
            return self._ledger.plugin_invocation_cancellation_requested(
                actor, request_id, request_digest
            )
        except PersistenceError as error:
            raise PluginRunnerError(_persistence_code(error)) from None

    def _recover_completed(
        self,
        actor: ActorContext,
        session: Mapping[str, object],
        invocation: Mapping[str, object],
        request_digest: str,
        error: PersistenceError,
    ) -> Mapping[str, object] | None:
        if str(error) != "storage.conflict":
            return None
        try:
            claim = self._ledger.claim_plugin_invocation(
                actor,
                session,
                invocation,
                request_digest,
                self._now().isoformat().replace("+00:00", "Z"),
            )
        except PersistenceError:
            return None
        if claim.state == "completed" and isinstance(claim.result, Mapping):
            return dict(claim.result)
        return None

    def _elapsed_millis(self, start: float) -> int:
        return min(max(0, round((self._monotonic() - start) * 1000)), 3_600_000)

    def _result(
        self,
        actor: ActorContext,
        manifest: Mapping[str, object],
        invocation_metadata: Mapping[str, object],
        session_metadata: Mapping[str, object],
        *,
        status: str,
        wall_time_millis: int,
        output_bytes: int,
        output: Mapping[str, object] | None = None,
        error_code: str | None = None,
    ) -> dict[str, object]:
        plugin_metadata = manifest["metadata"]
        assert isinstance(plugin_metadata, Mapping)
        spec: dict[str, object] = {
            "status": status,
            "usage": {
                "wallTimeMillis": wall_time_millis,
                "outputBytes": output_bytes,
            },
        }
        if status == "succeeded" and output is not None:
            spec.update(outputDigest=canonical_digest(output), output=dict(output))
        elif error_code is not None:
            spec["error"] = {"code": error_code}
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationResult",
            "metadata": {
                "id": invocation_metadata["id"],
                "sessionId": session_metadata["id"],
                "tenantId": actor.tenant_id,
                "pluginId": plugin_metadata["id"],
                "pluginVersion": plugin_metadata["version"],
                "completedAt": self._now().isoformat().replace("+00:00", "Z"),
            },
            "spec": spec,
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
                or not {"manifestDigest", "capability", "method", "input"}.issubset(
                    spec
                )
                or not set(spec).issubset(
                    {
                        "manifestDigest",
                        "capability",
                        "method",
                        "input",
                        "mediationGrants",
                        "actionMediationGrants",
                    }
                )
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
            if not created <= deadline <= expires:
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


def _copy_document(document: Mapping[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(document))


def _unsigned_manifest_digest(manifest: Mapping[str, object]) -> str:
    document = _copy_document(manifest)
    try:
        artifact = document["spec"]["artifact"]
        if not isinstance(artifact, dict):
            raise ValueError
        artifact.pop("signature")
    except (KeyError, TypeError, ValueError):
        raise ValueError from None
    return canonical_digest(document)


def _docker_environment() -> dict[str, str]:
    return {
        name: os.environ[name]
        for name in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT")
        if name in os.environ
    }


def _persistence_code(error: PersistenceError) -> str:
    return {
        "storage.not-found": "plugin.session.not-found",
        "storage.conflict": "plugin.request.conflict",
        "storage.input-invalid": "plugin.request.invalid",
        "storage.corrupt": "plugin.execution.persistence-corrupt",
        "plugin.request.limit-exceeded": "plugin.request.limit-exceeded",
        "plugin.request.expired": "plugin.request.expired",
        "plugin.request.cancellation-pending": "plugin.request.cancellation-pending",
    }.get(str(error), "plugin.execution.persistence-unavailable")


def _runtime_error_code(error: PluginRunnerError) -> str:
    code = str(error)
    return code if code in _TERMINAL_RUNTIME_ERRORS else "plugin.runtime.failed"
