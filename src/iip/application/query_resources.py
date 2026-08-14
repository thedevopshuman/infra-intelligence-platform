"""Tenant-scoped resource graph and immutable timeline query use cases."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from iip.application.ports import (
    ActorContext,
    PolicyDecisionPoint,
    ResourceObservationRecord,
    ResourceRepository,
)
from iip.domain.models import Resource, ResourceRelationshipEdge


RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
RELATIONSHIP_TYPE = re.compile(r"[a-z][a-z0-9._-]{0,63}")
EDGE_ID = re.compile(r"rel_[a-f0-9]{32}")


class QueryAuthorizationError(PermissionError):
    """Raised when policy denies a scoped read."""


class InvalidQueryError(ValueError):
    """Raised when query parameters violate the public contract."""


class InvalidCursorError(InvalidQueryError):
    """Raised when an opaque cursor is malformed or bound to another query."""


class ResourceNotFoundError(LookupError):
    """Raised when a resource is not visible in the actor's tenant."""


@dataclass(frozen=True)
class PageInfo:
    """Applied page limit and optional opaque continuation token."""

    limit: int
    has_more: bool
    next_cursor: Optional[str] = None


@dataclass(frozen=True)
class ResourceNeighborhoodResult:
    """One current, depth-one graph page around a root resource."""

    tenant_id: str
    root_resource_uid: str
    direction: str
    nodes: tuple[Resource, ...]
    edges: tuple[ResourceRelationshipEdge, ...]
    page: PageInfo


@dataclass(frozen=True)
class ResourceTimelineResult:
    """One ascending page of immutable observations for a resource."""

    tenant_id: str
    resource_uid: str
    items: tuple[ResourceObservationRecord, ...]
    page: PageInfo


class ResourceQueryService:
    """Authorize resource reads and provide query-bound cursor pagination."""

    def __init__(
        self,
        repository: ResourceRepository,
        policy: PolicyDecisionPoint,
    ) -> None:
        self._repository = repository
        self._policy = policy

    def list_resources(self, actor: ActorContext) -> tuple[Resource, ...]:
        """Return the current tenant projection after read authorization."""

        self._authorize(actor, None)
        return tuple(self._repository.list(actor.tenant_id))

    def neighborhood(
        self,
        actor: ActorContext,
        resource_uid: str,
        *,
        direction: str = "both",
        relationship_types: Iterable[str] = (),
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ResourceNeighborhoodResult:
        """Return one page of latest graph edges touching a visible root."""

        self._validate_uid(resource_uid)
        self._validate_limit(limit)
        if direction not in ("incoming", "outgoing", "both"):
            raise InvalidQueryError("request.invalid")
        types = self._normalize_relationship_types(relationship_types)
        self._authorize(actor, resource_uid)
        scope = self._scope_digest(
            "resource-neighborhood",
            actor.tenant_id,
            resource_uid,
            direction,
            *types,
        )
        after_edge_id = None
        if cursor is not None:
            position = self._decode_cursor(cursor, "resource-neighborhood", scope)
            if not isinstance(position, str) or EDGE_ID.fullmatch(position) is None:
                raise InvalidCursorError("pagination.cursor_invalid")
            after_edge_id = position
        root = self._repository.get(actor.tenant_id, resource_uid)
        if root is None:
            raise ResourceNotFoundError("resource.not_found")

        candidates = tuple(
            self._repository.relationships(
                actor.tenant_id,
                resource_uid,
                direction=direction,
                relationship_types=types,
                after_edge_id=after_edge_id,
                limit=limit + 1,
            )
        )
        has_more = len(candidates) > limit
        edges = candidates[:limit]
        next_cursor = (
            self._encode_cursor("resource-neighborhood", scope, edges[-1].edge_id)
            if has_more and edges
            else None
        )
        resolved_uids = {resource_uid}
        for edge in edges:
            if RESOURCE_UID.fullmatch(edge.source_ref):
                resolved_uids.add(edge.source_ref)
            if RESOURCE_UID.fullmatch(edge.target_ref):
                resolved_uids.add(edge.target_ref)
        nodes_by_uid = {
            resource.identity.uid: resource
            for resource in self._repository.get_many(actor.tenant_id, resolved_uids)
        }
        nodes_by_uid[resource_uid] = root
        return ResourceNeighborhoodResult(
            tenant_id=actor.tenant_id,
            root_resource_uid=resource_uid,
            direction=direction,
            nodes=tuple(nodes_by_uid[uid] for uid in sorted(nodes_by_uid)),
            edges=edges,
            page=PageInfo(limit, has_more, next_cursor),
        )

    def timeline(
        self,
        actor: ActorContext,
        resource_uid: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ResourceTimelineResult:
        """Return one ascending immutable observation-history page."""

        self._validate_uid(resource_uid)
        self._validate_limit(limit)
        self._authorize(actor, resource_uid)
        scope = self._scope_digest(
            "resource-timeline",
            actor.tenant_id,
            resource_uid,
        )
        after_offset = 0
        if cursor is not None:
            position = self._decode_cursor(cursor, "resource-timeline", scope)
            if (
                isinstance(position, bool)
                or not isinstance(position, int)
                or position < 1
                or position > 9_007_199_254_740_991
            ):
                raise InvalidCursorError("pagination.cursor_invalid")
            after_offset = position
        if self._repository.get(actor.tenant_id, resource_uid) is None:
            raise ResourceNotFoundError("resource.not_found")

        candidates = tuple(
            self._repository.history(
                actor.tenant_id,
                resource_uid,
                after_offset=after_offset,
                limit=limit + 1,
            )
        )
        has_more = len(candidates) > limit
        items = candidates[:limit]
        next_cursor = (
            self._encode_cursor("resource-timeline", scope, items[-1].offset)
            if has_more and items
            else None
        )
        return ResourceTimelineResult(
            tenant_id=actor.tenant_id,
            resource_uid=resource_uid,
            items=items,
            page=PageInfo(limit, has_more, next_cursor),
        )

    def _authorize(self, actor: ActorContext, resource_uid: Optional[str]) -> None:
        policy_resource = {"tenantId": actor.tenant_id}
        if resource_uid is not None:
            policy_resource["uid"] = resource_uid
        decision = self._policy.decide(actor, "resource:read", policy_resource)
        if not decision.allowed:
            raise QueryAuthorizationError(decision.reason_code)

    @staticmethod
    def _validate_uid(resource_uid: str) -> None:
        if not isinstance(resource_uid, str) or RESOURCE_UID.fullmatch(resource_uid) is None:
            raise InvalidQueryError("request.invalid")

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
            raise InvalidQueryError("request.invalid")

    @staticmethod
    def _normalize_relationship_types(values: Iterable[str]) -> tuple[str, ...]:
        result = set()
        for index, value in enumerate(values):
            if index >= 32:
                raise InvalidQueryError("request.invalid")
            if not isinstance(value, str) or RELATIONSHIP_TYPE.fullmatch(value) is None:
                raise InvalidQueryError("request.invalid")
            result.add(value)
        return tuple(sorted(result))

    @staticmethod
    def _scope_digest(*parts: str) -> str:
        material = "\x1f".join(parts).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _encode_cursor(kind: str, scope: str, position: object) -> str:
        payload = {
            "kind": kind,
            "position": position,
            "scope": scope,
            "version": 1,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return "p1." + encoded.rstrip("=")

    @classmethod
    def _decode_cursor(cls, cursor: str, kind: str, scope: str) -> object:
        if (
            not isinstance(cursor, str)
            or len(cursor) > 2048
            or re.fullmatch(r"p1\.[A-Za-z0-9_-]+", cursor) is None
        ):
            raise InvalidCursorError("pagination.cursor_invalid")
        token = cursor[3:]
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.b64decode(padded, altchars=b"-_", validate=True)
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise InvalidCursorError("pagination.cursor_invalid") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"kind", "position", "scope", "version"}
            or payload.get("version") != 1
            or payload.get("kind") != kind
            or not isinstance(payload.get("scope"), str)
            or not hmac.compare_digest(payload["scope"], scope)
            or cls._encode_cursor(kind, scope, payload.get("position")) != cursor
        ):
            raise InvalidCursorError("pagination.cursor_invalid")
        return payload["position"]
