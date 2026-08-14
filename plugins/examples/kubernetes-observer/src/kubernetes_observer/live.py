"""Explicit, bounded kubectl transport for local live-cluster development."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping


RESOURCE_ARGUMENT = (
    "namespaces,nodes,pods,services,configmaps,deployments.apps,"
    "replicasets.apps,statefulsets.apps,daemonsets.apps,"
    "ingresses.networking.k8s.io"
)


class LiveCollectionError(RuntimeError):
    """Live transport failure carrying only a stable external code."""


def list_objects(
    *,
    context: str,
    kubeconfig: Path,
    timeout_seconds: int = 20,
) -> Mapping[str, Any]:
    """Return one aggregate Kubernetes List using explicit client configuration.

    This transport is intentionally a development adapter. The plugin process is
    given a named context and kubeconfig path; it never guesses a context or reads
    an implicit default. Production execution uses a request-scoped credential
    broker and the same public collection contract.
    """

    if not context or len(context) > 253:
        raise LiveCollectionError("collector.live.context_invalid")
    if not kubeconfig.is_file():
        raise LiveCollectionError("collector.live.kubeconfig_unavailable")
    if not 1 <= timeout_seconds <= 120:
        raise LiveCollectionError("collector.live.timeout_invalid")

    command = [
        "kubectl",
        "--kubeconfig",
        str(kubeconfig),
        "--context",
        context,
        "get",
        RESOURCE_ARGUMENT,
        "--all-namespaces",
        "--output=json",
        f"--request-timeout={timeout_seconds}s",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
        )
    except (OSError, subprocess.SubprocessError):
        raise LiveCollectionError("collector.live.transport_unavailable") from None
    if completed.returncode != 0:
        raise LiveCollectionError("collector.live.provider_error")
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise LiveCollectionError("collector.live.response_invalid") from None
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise LiveCollectionError("collector.live.response_invalid")

    # kubectl's multi-resource aggregate List has an empty list resourceVersion.
    # A deterministic composite is safe for reconciliation identity but is not a
    # provider watch cursor. Individual object resourceVersions remain untouched.
    revisions = []
    for item in document["items"]:
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
    document["metadata"] = {
        "resourceVersion": "composite-sha256:" + hashlib.sha256(material).hexdigest()
    }
    return document
