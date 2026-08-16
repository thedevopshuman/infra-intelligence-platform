"""Deterministic Kubernetes List normalizer using only public SDK contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from infra_intelligence_sdk import ResourceCollectionRequest, ResourceCollectionResult


API_VERSION = "iip.platform/v1alpha1"
MAX_SAFE_SEQUENCE = 9007199254740991
PROVIDER_UID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}")
SAFE_LABEL_KEYS = frozenset(
    {
        "app",
        "app.kubernetes.io/component",
        "app.kubernetes.io/instance",
        "app.kubernetes.io/name",
        "component",
        "environment",
        "team",
        "tier",
    }
)
SUPPORTED_TYPES = {
    ("v1", "Namespace"): "core/namespace",
    ("v1", "Node"): "core/node",
    ("v1", "Pod"): "core/pod",
    ("v1", "Service"): "core/service",
    ("v1", "ConfigMap"): "core/configmap",
    ("apps/v1", "Deployment"): "apps/deployment",
    ("apps/v1", "ReplicaSet"): "apps/replicaset",
    ("apps/v1", "StatefulSet"): "apps/statefulset",
    ("apps/v1", "DaemonSet"): "apps/daemonset",
    ("networking.k8s.io/v1", "Ingress"): "networking.k8s.io/ingress",
}
WORKLOAD_KINDS = frozenset({"Deployment", "ReplicaSet", "StatefulSet", "DaemonSet"})


class CollectorError(ValueError):
    """Internal failure carrying only a stable externally safe code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Candidate:
    """Safe intermediate resource plus relationship-only provider context."""

    api_version: str
    kind: str
    namespace: Optional[str]
    name: str
    resource: Dict[str, Any]
    raw_labels: Mapping[str, str]
    owner_references: Tuple[Mapping[str, Any], ...]
    node_name: Optional[str] = None
    config_map_names: Tuple[str, ...] = ()
    service_selector: Optional[Mapping[str, str]] = None
    ingress_services: Tuple[str, ...] = ()


def canonical_digest(document: object) -> str:
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def resource_uid(
    tenant_id: str, provider: str, resource_type: str, external_id: str
) -> str:
    value = "\x1f".join((tenant_id, provider, resource_type, external_id)).encode("utf-8")
    return "res_" + hashlib.sha256(value).hexdigest()[:32]


def _mapping(value: object, code: str = "collector.object_invalid") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CollectorError(code)
    return value


def _string(value: object, code: str = "collector.object_invalid") -> str:
    if not isinstance(value, str) or not value:
        raise CollectorError(code)
    return value


def _integer(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def _safe_labels(value: object) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    labels: Dict[str, str] = {}
    for key in sorted(value):
        item = value[key]
        if key in SAFE_LABEL_KEYS and isinstance(item, str):
            labels[key] = item[:256]
        if len(labels) == 64:
            break
    return labels


def _raw_string_map(value: object) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _external_id(cluster_id: str, namespace: Optional[str], name: str) -> str:
    if namespace:
        return f"{cluster_id}/{namespace}/{name}"
    return f"{cluster_id}/{name}"


def _relationship(kind: str, target: str) -> Dict[str, str]:
    return {"type": kind, "target": target, "direction": "outbound"}


def _condition(status: Mapping[str, Any], condition_type: str) -> Optional[str]:
    conditions = status.get("conditions", [])
    if not isinstance(conditions, list):
        return None
    for item in conditions:
        if isinstance(item, Mapping) and item.get("type") == condition_type:
            value = item.get("status")
            return value if isinstance(value, str) else None
    return None


def _pod_waiting_reason(status: Mapping[str, Any]) -> Optional[str]:
    statuses = status.get("containerStatuses", [])
    if not isinstance(statuses, list):
        return None
    for container in statuses:
        if not isinstance(container, Mapping):
            continue
        state = container.get("state")
        waiting = state.get("waiting") if isinstance(state, Mapping) else None
        reason = waiting.get("reason") if isinstance(waiting, Mapping) else None
        if isinstance(reason, str) and reason:
            return reason[:128]
    return None


def _health_and_lifecycle(
    kind: str, metadata: Mapping[str, Any], spec: Mapping[str, Any], status: Mapping[str, Any]
) -> Tuple[str, str]:
    if metadata.get("deletionTimestamp") is not None:
        return "unknown", "deleting"
    if kind == "Node":
        ready = _condition(status, "Ready")
        return ({"True": "healthy", "False": "unhealthy"}.get(ready, "unknown"), "active")
    if kind == "Pod":
        phase = status.get("phase")
        ready = _condition(status, "Ready")
        waiting_reason = _pod_waiting_reason(status)
        if waiting_reason in (
            "CreateContainerConfigError",
            "CrashLoopBackOff",
            "ErrImagePull",
            "ImagePullBackOff",
            "RunContainerError",
        ):
            return "unhealthy", "creating" if phase == "Pending" else "active"
        if phase == "Running":
            return ("healthy" if ready == "True" else "degraded"), "active"
        if phase == "Failed":
            return "unhealthy", "active"
        if phase == "Pending":
            return "unknown", "creating"
        return "unknown", "unknown"
    if kind in WORKLOAD_KINDS:
        desired = _integer(spec.get("replicas"), 1)
        if kind == "DaemonSet":
            desired = _integer(status.get("desiredNumberScheduled"))
        ready = _integer(status.get("readyReplicas"))
        if kind == "DaemonSet":
            ready = _integer(status.get("numberReady"))
        if ready >= desired:
            return "healthy", "active"
        return ("degraded" if ready else "unhealthy"), "active"
    if kind == "Namespace":
        return ("healthy" if status.get("phase") == "Active" else "unknown"), "active"
    return "unknown", "active"


def _safe_attributes(
    kind: str,
    namespace: Optional[str],
    metadata: Mapping[str, Any],
    spec: Mapping[str, Any],
    status: Mapping[str, Any],
) -> Dict[str, Any]:
    attributes: Dict[str, Any] = {}
    provider_uid = metadata.get("uid")
    if isinstance(provider_uid, str) and PROVIDER_UID.fullmatch(provider_uid):
        attributes["providerUid"] = provider_uid
    if namespace:
        attributes["namespace"] = namespace
    if kind == "Namespace":
        phase = status.get("phase")
        if isinstance(phase, str):
            attributes["phase"] = phase
    elif kind == "Node":
        node_info = status.get("nodeInfo")
        node_info = node_info if isinstance(node_info, Mapping) else {}
        for source, target in (
            ("architecture", "architecture"),
            ("operatingSystem", "operatingSystem"),
            ("kubeletVersion", "kubeletVersion"),
        ):
            value = node_info.get(source)
            if isinstance(value, str):
                attributes[target] = value
        attributes["unschedulable"] = spec.get("unschedulable") is True
    elif kind in WORKLOAD_KINDS:
        attributes["replicas"] = _integer(spec.get("replicas"), 1)
        if kind == "DaemonSet":
            attributes["desiredScheduled"] = _integer(status.get("desiredNumberScheduled"))
            attributes["readyScheduled"] = _integer(status.get("numberReady"))
        else:
            attributes["readyReplicas"] = _integer(status.get("readyReplicas"))
            attributes["availableReplicas"] = _integer(status.get("availableReplicas"))
    elif kind == "Pod":
        phase = status.get("phase")
        if isinstance(phase, str):
            attributes["phase"] = phase
        node_name = spec.get("nodeName")
        if isinstance(node_name, str) and node_name:
            attributes["nodeName"] = node_name
        containers = spec.get("containers")
        attributes["containerCount"] = len(containers) if isinstance(containers, list) else 0
        statuses = status.get("containerStatuses")
        attributes["readyContainers"] = (
            sum(1 for item in statuses if isinstance(item, Mapping) and item.get("ready") is True)
            if isinstance(statuses, list)
            else 0
        )
        waiting_reason = _pod_waiting_reason(status)
        if waiting_reason is not None:
            attributes["waitingReason"] = waiting_reason
    elif kind == "Service":
        service_type = spec.get("type")
        if isinstance(service_type, str):
            attributes["serviceType"] = service_type
        ports = spec.get("ports")
        if isinstance(ports, list):
            attributes["ports"] = [
                {
                    key: port[key]
                    for key in ("name", "port", "protocol", "targetPort")
                    if isinstance(port, Mapping) and key in port
                }
                for port in ports[:64]
                if isinstance(port, Mapping)
            ]
    elif kind == "Ingress":
        ingress_class = spec.get("ingressClassName")
        if isinstance(ingress_class, str):
            attributes["ingressClassName"] = ingress_class
        rules = spec.get("rules")
        if isinstance(rules, list):
            attributes["hosts"] = sorted(
                {
                    rule["host"]
                    for rule in rules
                    if isinstance(rule, Mapping) and isinstance(rule.get("host"), str)
                }
            )[:128]
    elif kind == "ConfigMap":
        data = metadata.get("_configMapData")
        attributes["keys"] = sorted(data)[:1024] if isinstance(data, Mapping) else []
        attributes["immutable"] = spec.get("immutable") is True
    return attributes


def _config_map_names(spec: Mapping[str, Any]) -> Tuple[str, ...]:
    names = set()
    volumes = spec.get("volumes", [])
    if isinstance(volumes, list):
        for volume in volumes:
            if not isinstance(volume, Mapping):
                continue
            config_map = volume.get("configMap")
            if isinstance(config_map, Mapping) and isinstance(config_map.get("name"), str):
                names.add(config_map["name"])
    return tuple(sorted(names))


def _ingress_services(spec: Mapping[str, Any]) -> Tuple[str, ...]:
    names = set()
    default_backend = spec.get("defaultBackend")
    backends: List[object] = [default_backend] if default_backend is not None else []
    rules = spec.get("rules", [])
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            http = rule.get("http")
            paths = http.get("paths", []) if isinstance(http, Mapping) else []
            if isinstance(paths, list):
                backends.extend(path.get("backend") for path in paths if isinstance(path, Mapping))
    for backend in backends:
        if not isinstance(backend, Mapping):
            continue
        service = backend.get("service")
        if isinstance(service, Mapping) and isinstance(service.get("name"), str):
            names.add(service["name"])
    return tuple(sorted(names))


def _candidate(
    item: Mapping[str, Any],
    *,
    tenant_id: str,
    cluster_id: str,
    allowed_namespaces: frozenset[str],
    request: Mapping[str, Any],
    list_resource_version: str,
) -> Optional[Candidate]:
    api_version = _string(item.get("apiVersion"))
    kind = _string(item.get("kind"))
    if kind == "Secret":
        return None
    resource_type = SUPPORTED_TYPES.get((api_version, kind))
    if resource_type is None:
        return None
    metadata = _mapping(item.get("metadata"))
    name = _string(metadata.get("name"))
    namespace_value = metadata.get("namespace")
    namespace = namespace_value if isinstance(namespace_value, str) and namespace_value else None
    if kind == "Namespace":
        if name not in allowed_namespaces:
            return None
    elif kind not in ("Node",) and namespace not in allowed_namespaces:
        return None

    spec = item if kind == "ConfigMap" else item.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    status = item.get("status")
    status = status if isinstance(status, Mapping) else {}
    safe_metadata: Dict[str, Any] = dict(metadata)
    if kind == "ConfigMap":
        safe_metadata["_configMapData"] = item.get("data") if isinstance(item.get("data"), Mapping) else {}
    external_id = _external_id(cluster_id, namespace, name)
    resource_version = metadata.get("resourceVersion")
    cursor: Dict[str, Any] = {
        "sourceId": request["sourceId"],
        "streamId": request["streamId"],
        "sequence": 0,
        "mode": request["mode"],
        "resourceVersion": resource_version if isinstance(resource_version, str) else list_resource_version,
    }
    if request.get("snapshotId") is not None:
        cursor["snapshotId"] = request["snapshotId"]
    labels = _safe_labels(metadata.get("labels"))
    health, lifecycle = _health_and_lifecycle(kind, metadata, spec, status)
    resource: Dict[str, Any] = {
        "apiVersion": API_VERSION,
        "kind": "Resource",
        "metadata": {
            "uid": resource_uid(tenant_id, "kubernetes", resource_type, external_id),
            "tenantId": tenant_id,
            "observedAt": request["observedAt"],
            "observation": cursor,
        },
        "spec": {
            "provider": "kubernetes",
            "type": resource_type,
            "externalId": external_id,
            "displayName": name,
            "attributes": _safe_attributes(kind, namespace, safe_metadata, spec, status),
            "relationships": [],
        },
        "status": {"health": health, "lifecycle": lifecycle},
    }
    if labels:
        resource["metadata"]["labels"] = labels
    owners_value = metadata.get("ownerReferences", [])
    owners = (
        tuple(item for item in owners_value if isinstance(item, Mapping))
        if isinstance(owners_value, list)
        else ()
    )
    node_name = spec.get("nodeName") if kind == "Pod" else None
    selector = spec.get("selector") if kind == "Service" else None
    return Candidate(
        api_version=api_version,
        kind=kind,
        namespace=namespace,
        name=name,
        resource=resource,
        raw_labels=_raw_string_map(metadata.get("labels")),
        owner_references=owners,
        node_name=node_name if isinstance(node_name, str) else None,
        config_map_names=_config_map_names(spec) if kind == "Pod" else (),
        service_selector=_raw_string_map(selector) if selector is not None else None,
        ingress_services=_ingress_services(spec) if kind == "Ingress" else (),
    )


def _cluster_candidate(
    *, tenant_id: str, cluster_id: str, request: Mapping[str, Any], resource_version: str
) -> Candidate:
    external_id = cluster_id
    cursor: Dict[str, Any] = {
        "sourceId": request["sourceId"],
        "streamId": request["streamId"],
        "sequence": 0,
        "mode": request["mode"],
        "resourceVersion": resource_version,
    }
    if request.get("snapshotId") is not None:
        cursor["snapshotId"] = request["snapshotId"]
    return Candidate(
        api_version="iip.platform/v1alpha1",
        kind="Cluster",
        namespace=None,
        name=cluster_id,
        resource={
            "apiVersion": API_VERSION,
            "kind": "Resource",
            "metadata": {
                "uid": resource_uid(tenant_id, "kubernetes", "core/cluster", external_id),
                "tenantId": tenant_id,
                "observedAt": request["observedAt"],
                "observation": cursor,
            },
            "spec": {
                "provider": "kubernetes",
                "type": "core/cluster",
                "externalId": external_id,
                "displayName": cluster_id,
                "attributes": {"clusterId": cluster_id},
                "relationships": [],
            },
            "status": {"health": "unknown", "lifecycle": "active"},
        },
        raw_labels={},
        owner_references=(),
    )


def _add_relationships(candidates: List[Candidate]) -> None:
    by_key = {(item.api_version, item.kind, item.namespace, item.name): item for item in candidates}
    cluster = next(item for item in candidates if item.kind == "Cluster")
    namespaces = {item.name: item for item in candidates if item.kind == "Namespace"}
    nodes = {item.name: item for item in candidates if item.kind == "Node"}
    pods = [item for item in candidates if item.kind == "Pod"]
    config_maps = {
        (item.namespace, item.name): item for item in candidates if item.kind == "ConfigMap"
    }
    services = {
        (item.namespace, item.name): item for item in candidates if item.kind == "Service"
    }

    for item in candidates:
        if item.kind in ("Namespace", "Node"):
            cluster.resource["spec"]["relationships"].append(
                _relationship("contains", item.resource["metadata"]["uid"])
            )
        if item.namespace in namespaces:
            namespaces[item.namespace].resource["spec"]["relationships"].append(
                _relationship("contains", item.resource["metadata"]["uid"])
            )
        for owner in item.owner_references:
            owner_kind = owner.get("kind")
            owner_name = owner.get("name")
            owner_api_version = owner.get("apiVersion")
            owner_item = by_key.get((owner_api_version, owner_kind, item.namespace, owner_name))
            if owner_item is not None:
                owner_item.resource["spec"]["relationships"].append(
                    _relationship("owns", item.resource["metadata"]["uid"])
                )
        if item.kind == "Pod" and item.node_name in nodes:
            item.resource["spec"]["relationships"].append(
                _relationship("runs_on", nodes[item.node_name].resource["metadata"]["uid"])
            )
        if item.kind == "Pod":
            for name in item.config_map_names:
                config_map = config_maps.get((item.namespace, name))
                if config_map is not None:
                    item.resource["spec"]["relationships"].append(
                        _relationship("reads_from", config_map.resource["metadata"]["uid"])
                    )
        if item.kind == "Service" and item.service_selector:
            for pod in pods:
                if pod.namespace == item.namespace and all(
                    pod.raw_labels.get(key) == value
                    for key, value in item.service_selector.items()
                ):
                    item.resource["spec"]["relationships"].append(
                        _relationship("routes_to", pod.resource["metadata"]["uid"])
                    )
        if item.kind == "Ingress":
            for service_name in item.ingress_services:
                service = services.get((item.namespace, service_name))
                if service is not None:
                    item.resource["spec"]["relationships"].append(
                        _relationship("routes_to", service.resource["metadata"]["uid"])
                    )

    for item in candidates:
        relationships = item.resource["spec"]["relationships"]
        unique = {
            (relationship["type"], relationship["target"], relationship["direction"]): relationship
            for relationship in relationships
        }
        item.resource["spec"]["relationships"] = [unique[key] for key in sorted(unique)]


def _failed_result(
    request: Mapping[str, Any], *, reason_code: str, scope_digest: str
) -> ResourceCollectionResult:
    completion: Dict[str, Any] = {
        "status": "failed",
        "resourceCount": 0,
        "nextSequence": request["startSequence"],
        "scopeDigest": scope_digest,
        "reasonCode": reason_code,
    }
    if request.get("snapshotId") is not None:
        completion["snapshotId"] = request["snapshotId"]
    return ResourceCollectionResult.from_dict(
        {
            "apiVersion": API_VERSION,
            "kind": "ResourceCollectionResult",
            "metadata": {
                "requestId": request["requestId"],
                "tenantId": request["tenantId"],
                "sourceId": request["sourceId"],
                "createdAt": request["observedAt"],
            },
            "spec": {"observations": [], "completion": completion},
        }
    )


def collect(
    request_payload: Mapping[str, Any], list_document: Mapping[str, Any]
) -> ResourceCollectionResult:
    """Normalize a bounded Kubernetes List into a deterministic collection result."""

    public_request = ResourceCollectionRequest.from_dict(request_payload).to_dict()
    metadata = _mapping(public_request["metadata"], "collector.request_invalid")
    spec = _mapping(public_request["spec"], "collector.request_invalid")
    scope = _mapping(spec.get("scope"), "collector.request_invalid")
    limits = _mapping(spec.get("limits"), "collector.request_invalid")
    parameters = scope.get("parameters", {})
    parameters = _mapping(parameters, "collector.scope_invalid")
    namespaces_value = parameters.get("namespaces")
    if (
        scope.get("provider") != "kubernetes"
        or not isinstance(namespaces_value, list)
        or not namespaces_value
        or len(namespaces_value) > 128
        or any(
            not isinstance(item, str) or not item or len(item) > 63
            for item in namespaces_value
        )
        or len(set(namespaces_value)) != len(namespaces_value)
        or set(parameters) != {"namespaces"}
    ):
        raise ValueError("resource collection request has unsupported Kubernetes scope")
    request = {
        "requestId": _string(metadata.get("requestId"), "collector.request_invalid"),
        "tenantId": _string(metadata.get("tenantId"), "collector.request_invalid"),
        "observedAt": _string(metadata.get("requestedAt"), "collector.request_invalid"),
        "sourceId": _string(spec.get("sourceId"), "collector.request_invalid"),
        "streamId": _string(spec.get("streamId"), "collector.request_invalid"),
        "mode": _string(spec.get("mode"), "collector.request_invalid"),
        "snapshotId": spec.get("snapshotId"),
        "startSequence": spec.get("startSequence"),
    }
    if (
        not isinstance(request["startSequence"], int)
        or isinstance(request["startSequence"], bool)
        or request["startSequence"] < 0
    ):
        raise ValueError("resource collection request startSequence is invalid")
    scope_digest = canonical_digest(scope)

    try:
        list_document = _mapping(list_document)
        list_metadata = _mapping(list_document.get("metadata"))
        resource_version = _string(
            list_metadata.get("resourceVersion"), "collector.checkpoint_missing"
        )
        checkpoint_value = list_metadata.get("checkpoint")
        provider_cursors_value = list_metadata.get("providerCursors")
        if (checkpoint_value is None) != (provider_cursors_value is None):
            raise CollectorError("collector.cursor_state_invalid")
        provider_cursors: Dict[str, str] = {}
        if provider_cursors_value is not None:
            if (
                not isinstance(checkpoint_value, str)
                or not 1 <= len(checkpoint_value) <= 2048
                or not isinstance(provider_cursors_value, Mapping)
                or not 1 <= len(provider_cursors_value) <= 2048
                or any(
                    not isinstance(key, str)
                    or not 1 <= len(key) <= 256
                    or not isinstance(value, str)
                    or not 1 <= len(value) <= 512
                    for key, value in provider_cursors_value.items()
                )
            ):
                raise CollectorError("collector.cursor_state_invalid")
            provider_cursors = dict(sorted(provider_cursors_value.items()))
        items = list_document.get("items")
        if not isinstance(items, list):
            raise CollectorError("collector.object_invalid")
        cluster_id = _string(scope.get("rootExternalId"), "collector.scope_invalid")
        candidates = [
            _cluster_candidate(
                tenant_id=request["tenantId"],
                cluster_id=cluster_id,
                request=request,
                resource_version=resource_version,
            )
        ]
        for item in items:
            candidate = _candidate(
                _mapping(item),
                tenant_id=request["tenantId"],
                cluster_id=cluster_id,
                allowed_namespaces=frozenset(namespaces_value),
                request=request,
                list_resource_version=resource_version,
            )
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(
            key=lambda item: (
                item.resource["spec"]["type"],
                item.resource["spec"]["externalId"],
            )
        )
        _add_relationships(candidates)
        if len(candidates) > limits.get("maxResources", 0):
            raise CollectorError("collector.resource_limit_exceeded")
        observations = [item.resource for item in candidates]
        if request["startSequence"] + len(observations) > MAX_SAFE_SEQUENCE:
            raise CollectorError("collector.sequence_exhausted")
        for offset, observation in enumerate(observations):
            sequence = request["startSequence"] + offset
            observation["metadata"]["observation"]["sequence"] = sequence
        completion: Dict[str, Any] = {
            "status": "complete",
            "resourceCount": len(observations),
            "nextSequence": request["startSequence"] + len(observations),
            "checkpoint": checkpoint_value
            or f"kubernetes:{cluster_id}:resource-version:{resource_version}",
            "scopeDigest": scope_digest,
        }
        if provider_cursors:
            completion["providerCursors"] = provider_cursors
        if request.get("snapshotId") is not None:
            completion["snapshotId"] = request["snapshotId"]
        result = {
            "apiVersion": API_VERSION,
            "kind": "ResourceCollectionResult",
            "metadata": {
                "requestId": request["requestId"],
                "tenantId": request["tenantId"],
                "sourceId": request["sourceId"],
                "createdAt": request["observedAt"],
            },
            "spec": {"observations": observations, "completion": completion},
        }
        output_size = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        )
        if output_size > limits.get("maxOutputBytes", 0):
            raise CollectorError("collector.output_limit_exceeded")
        return ResourceCollectionResult.from_dict(result)
    except CollectorError as exc:
        return _failed_result(request, reason_code=exc.code, scope_digest=scope_digest)
