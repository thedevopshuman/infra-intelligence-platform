"""Explicit, bounded kubectl list/watch transport for local development."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode


@dataclass(frozen=True)
class ResourceStream:
    """One independently resumable Kubernetes collection stream."""

    argument: str
    api_path: str
    item_api_version: str
    item_kind: str
    namespace: str | None = None

    @property
    def key(self) -> str:
        return self.api_path


CLUSTER_STREAMS = (
    ResourceStream("namespaces", "/api/v1/namespaces", "v1", "Namespace"),
    ResourceStream("nodes", "/api/v1/nodes", "v1", "Node"),
)
NAMESPACED_STREAMS = (
    ("pods", "/api/v1/namespaces/{namespace}/pods", "v1", "Pod"),
    ("services", "/api/v1/namespaces/{namespace}/services", "v1", "Service"),
    ("configmaps", "/api/v1/namespaces/{namespace}/configmaps", "v1", "ConfigMap"),
    (
        "deployments.apps",
        "/apis/apps/v1/namespaces/{namespace}/deployments",
        "apps/v1",
        "Deployment",
    ),
    (
        "replicasets.apps",
        "/apis/apps/v1/namespaces/{namespace}/replicasets",
        "apps/v1",
        "ReplicaSet",
    ),
    (
        "statefulsets.apps",
        "/apis/apps/v1/namespaces/{namespace}/statefulsets",
        "apps/v1",
        "StatefulSet",
    ),
    (
        "daemonsets.apps",
        "/apis/apps/v1/namespaces/{namespace}/daemonsets",
        "apps/v1",
        "DaemonSet",
    ),
    (
        "ingresses.networking.k8s.io",
        "/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses",
        "networking.k8s.io/v1",
        "Ingress",
    ),
)
NAMESPACE_PATTERN = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")


class LiveCollectionError(RuntimeError):
    """Live transport failure carrying only a stable external code."""


class WatchExpired(RuntimeError):
    """Internal signal that at least one provider cursor is no longer retained."""


def resource_streams(namespaces: Sequence[str]) -> tuple[ResourceStream, ...]:
    """Return the exact independently cursor-backed streams for a scope."""

    unique = tuple(sorted(set(namespaces)))
    if (
        not unique
        or len(unique) != len(namespaces)
        or len(unique) > 128
        or any(not NAMESPACE_PATTERN.fullmatch(item) for item in unique)
    ):
        raise LiveCollectionError("collector.live.scope_invalid")
    streams = list(CLUSTER_STREAMS)
    for namespace in unique:
        streams.extend(
            ResourceStream(
                argument,
                path.format(namespace=namespace),
                api_version,
                kind,
                namespace,
            )
            for argument, path, api_version, kind in NAMESPACED_STREAMS
        )
    return tuple(streams)


def cursor_checkpoint(
    *, cluster_id: str, scope_digest: str, provider_cursors: Mapping[str, str]
) -> str:
    """Bind an opaque host checkpoint to its cluster, scope, and cursor map."""

    material = json.dumps(
        {
            "clusterId": cluster_id,
            "providerCursors": dict(sorted(provider_cursors.items())),
            "scopeDigest": scope_digest,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "kubernetes:cursor-set:v1:sha256:" + hashlib.sha256(material).hexdigest()


def _validate_configuration(
    *, context: str, kubeconfig: Path, timeout_seconds: int
) -> None:
    if not context or len(context) > 253:
        raise LiveCollectionError("collector.live.context_invalid")
    if not kubeconfig.is_file():
        raise LiveCollectionError("collector.live.kubeconfig_unavailable")
    if not 1 <= timeout_seconds <= 120:
        raise LiveCollectionError("collector.live.timeout_invalid")


def _kubectl_prefix(*, context: str, kubeconfig: Path) -> list[str]:
    return [
        "kubectl",
        "--kubeconfig",
        str(kubeconfig),
        "--context",
        context,
    ]


def _run(
    command: list[str], *, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
        )
    except (OSError, subprocess.SubprocessError):
        raise LiveCollectionError("collector.live.transport_unavailable") from None


def _list_stream(
    stream: ResourceStream,
    *,
    context: str,
    kubeconfig: Path,
    timeout_seconds: int,
) -> tuple[ResourceStream, Mapping[str, Any]]:
    # The generic kubectl resource builder rewrites even a single-kind response
    # as `kind: List` and can clear its collection resourceVersion. The raw
    # collection endpoint preserves the provider's authoritative list cursor.
    command = _kubectl_prefix(context=context, kubeconfig=kubeconfig) + [
        "get",
        f"--raw={stream.api_path}",
        f"--request-timeout={timeout_seconds}s",
    ]
    completed = _run(command, timeout_seconds=timeout_seconds)
    if completed.returncode != 0:
        raise LiveCollectionError("collector.live.provider_error")
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise LiveCollectionError("collector.live.response_invalid") from None
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise LiveCollectionError("collector.live.response_invalid")
    metadata = document.get("metadata")
    resource_version = (
        metadata.get("resourceVersion") if isinstance(metadata, dict) else None
    )
    if not isinstance(resource_version, str) or not resource_version:
        raise LiveCollectionError("collector.live.checkpoint_missing")
    normalized_items = []
    for value in document["items"]:
        if not isinstance(value, dict):
            raise LiveCollectionError("collector.live.response_invalid")
        item = dict(value)
        if item.get("apiVersion") not in (None, stream.item_api_version) or item.get(
            "kind"
        ) not in (None, stream.item_kind):
            raise LiveCollectionError("collector.live.response_invalid")
        item["apiVersion"] = stream.item_api_version
        item["kind"] = stream.item_kind
        normalized_items.append(item)
    document = dict(document)
    document["items"] = normalized_items
    return stream, document


def _list_all(
    streams: Sequence[ResourceStream],
    *,
    context: str,
    kubeconfig: Path,
    timeout_seconds: int,
) -> tuple[tuple[ResourceStream, Mapping[str, Any]], ...]:
    results = []
    with ThreadPoolExecutor(max_workers=min(16, len(streams))) as executor:
        futures = {
            executor.submit(
                _list_stream,
                stream,
                context=context,
                kubeconfig=kubeconfig,
                timeout_seconds=timeout_seconds,
            ): stream
            for stream in streams
        }
        for future in as_completed(futures):
            results.append(future.result())
    return tuple(sorted(results, key=lambda item: item[0].key))


def list_objects(
    *,
    context: str,
    kubeconfig: Path,
    namespaces: Sequence[str] = ("default",),
    cluster_id: str = "local",
    scope_digest: str = "sha256:" + "0" * 64,
    timeout_seconds: int = 20,
) -> Mapping[str, Any]:
    """List every configured type independently and return one safe aggregate."""

    _validate_configuration(
        context=context, kubeconfig=kubeconfig, timeout_seconds=timeout_seconds
    )
    streams = resource_streams(namespaces)
    listed = _list_all(
        streams,
        context=context,
        kubeconfig=kubeconfig,
        timeout_seconds=timeout_seconds,
    )
    items: list[object] = []
    provider_cursors: dict[str, str] = {}
    for stream, document in listed:
        metadata = document["metadata"]
        provider_cursors[stream.key] = metadata["resourceVersion"]
        items.extend(document["items"])

    revisions = []
    for item in items:
        if not isinstance(item, dict):
            raise LiveCollectionError("collector.live.response_invalid")
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            raise LiveCollectionError("collector.live.response_invalid")
        revisions.append(
            "\x1f".join(
                str(value)
                for value in (
                    item.get("apiVersion", ""),
                    item.get("kind", ""),
                    metadata.get("uid", ""),
                    metadata.get("resourceVersion", ""),
                )
            )
        )
    material = "\n".join(sorted(revisions)).encode("utf-8")
    return {
        "apiVersion": "v1",
        "kind": "List",
        "metadata": {
            "resourceVersion": "composite-sha256:"
            + hashlib.sha256(material).hexdigest(),
            "checkpoint": cursor_checkpoint(
                cluster_id=cluster_id,
                scope_digest=scope_digest,
                provider_cursors=provider_cursors,
            ),
            "providerCursors": dict(sorted(provider_cursors.items())),
        },
        "items": items,
    }


def _json_stream(payload: str) -> tuple[Mapping[str, Any], ...]:
    decoder = json.JSONDecoder()
    offset = 0
    documents = []
    while offset < len(payload):
        while offset < len(payload) and payload[offset].isspace():
            offset += 1
        if offset == len(payload):
            break
        try:
            document, offset = decoder.raw_decode(payload, offset)
        except json.JSONDecodeError:
            raise LiveCollectionError("collector.live.response_invalid") from None
        if not isinstance(document, Mapping):
            raise LiveCollectionError("collector.live.response_invalid")
        documents.append(document)
    return tuple(documents)


def _looks_expired(payload: str) -> bool:
    lowered = payload.lower()
    return "410" in lowered or "resourceexpired" in lowered or "(gone)" in lowered


def _watch_stream(
    stream: ResourceStream,
    cursor: str,
    *,
    context: str,
    kubeconfig: Path,
    timeout_seconds: int,
) -> bool:
    query = urlencode(
        {
            "allowWatchBookmarks": "true",
            "resourceVersion": cursor,
            "timeoutSeconds": str(timeout_seconds),
            "watch": "true",
        }
    )
    command = _kubectl_prefix(context=context, kubeconfig=kubeconfig) + [
        "get",
        f"--raw={stream.api_path}?{query}",
        f"--request-timeout={timeout_seconds + 2}s",
    ]
    completed = _run(command, timeout_seconds=timeout_seconds + 2)
    if completed.returncode != 0:
        if _looks_expired(completed.stdout) or _looks_expired(completed.stderr):
            raise WatchExpired(stream.key)
        raise LiveCollectionError("collector.live.provider_error")

    changed = False
    for event in _json_stream(completed.stdout):
        event_type = event.get("type")
        item = event.get("object")
        if event_type == "ERROR":
            if isinstance(item, Mapping) and item.get("code") == 410:
                raise WatchExpired(stream.key)
            raise LiveCollectionError("collector.live.provider_error")
        if event_type in ("ADDED", "MODIFIED", "DELETED"):
            changed = True
        elif event_type != "BOOKMARK":
            raise LiveCollectionError("collector.live.response_invalid")
        metadata = item.get("metadata") if isinstance(item, Mapping) else None
        if not isinstance(metadata, Mapping) or not isinstance(
            metadata.get("resourceVersion"), str
        ):
            raise LiveCollectionError("collector.live.response_invalid")
    return changed


def _watch_all(
    streams: Sequence[ResourceStream],
    provider_cursors: Mapping[str, str],
    *,
    context: str,
    kubeconfig: Path,
    timeout_seconds: int,
) -> bool:
    changed = False
    expired = False
    provider_error: LiveCollectionError | None = None
    with ThreadPoolExecutor(max_workers=min(16, len(streams))) as executor:
        futures = [
            executor.submit(
                _watch_stream,
                stream,
                provider_cursors[stream.key],
                context=context,
                kubeconfig=kubeconfig,
                timeout_seconds=timeout_seconds,
            )
            for stream in streams
        ]
        for future in as_completed(futures):
            try:
                changed = future.result() or changed
            except WatchExpired:
                expired = True
            except LiveCollectionError as exc:
                provider_error = exc
    if provider_error is not None:
        raise provider_error
    return changed or expired


def watch_then_list_objects(
    *,
    context: str,
    kubeconfig: Path,
    namespaces: Sequence[str],
    cluster_id: str,
    scope_digest: str,
    resume: Mapping[str, Any],
    timeout_seconds: int = 20,
) -> Mapping[str, Any]:
    """Resume every watch and always close the cycle with a fresh full list.

    An expired stream deliberately discards the complete cursor set and follows
    the same full-list path as a normal change. The returned cursor set becomes
    authoritative only after the host durably ingests the reconciliation result.
    """

    _validate_configuration(
        context=context, kubeconfig=kubeconfig, timeout_seconds=timeout_seconds
    )
    streams = resource_streams(namespaces)
    cursors = resume.get("providerCursors")
    checkpoint = resume.get("checkpoint")
    if (
        not isinstance(cursors, Mapping)
        or set(cursors) != {stream.key for stream in streams}
        or any(not isinstance(value, str) or not value for value in cursors.values())
        or checkpoint
        != cursor_checkpoint(
            cluster_id=cluster_id,
            scope_digest=scope_digest,
            provider_cursors=cursors,
        )
    ):
        raise LiveCollectionError("collector.live.resume_invalid")
    _watch_all(
        streams,
        cursors,
        context=context,
        kubeconfig=kubeconfig,
        timeout_seconds=timeout_seconds,
    )
    return list_objects(
        context=context,
        kubeconfig=kubeconfig,
        namespaces=namespaces,
        cluster_id=cluster_id,
        scope_digest=scope_digest,
        timeout_seconds=timeout_seconds,
    )
