"""Dependency-free synchronous HTTP client."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .errors import ApiError
from .models import ResourceNeighborhood, ResourceObservation, ResourceTimeline


class Client:
    """Bearer-authenticated control-plane client."""

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if not isinstance(bearer_token, str) or not bearer_token:
            raise ValueError("bearer_token must be a non-empty string")
        self._bearer_token = bearer_token
        self._timeout = timeout_seconds

    def ingest_resource(
        self,
        resource: ResourceObservation,
        correlation_id: Optional[str] = None,
    ) -> ResourceObservation:
        """Submit one resource observation."""

        headers = self._headers(correlation_id)
        request = Request(
            f"{self._base_url}/v1/resources",
            data=json.dumps(resource.to_dict()).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        payload = self._send(request)
        return ResourceObservation.from_dict(payload)

    def list_resources(self) -> list[ResourceObservation]:
        """List resources within this client's tenant scope."""

        request = Request(
            f"{self._base_url}/v1/resources",
            headers=self._headers(None),
            method="GET",
        )
        payload = self._send(request)
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise ApiError(200, "response.invalid")
        return [ResourceObservation.from_dict(item) for item in items]

    def get_resource_neighborhood(
        self,
        resource_uid: str,
        *,
        direction: str = "both",
        relationship_types: Iterable[str] = (),
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ResourceNeighborhood:
        """Read one tenant-scoped page of current graph relationships."""

        query: list[tuple[str, str]] = [
            ("depth", "1"),
            ("direction", direction),
            ("limit", str(limit)),
        ]
        query.extend(("relationshipType", value) for value in relationship_types)
        if cursor is not None:
            query.append(("cursor", cursor))
        request = Request(
            f"{self._base_url}/v1/resources/{quote(resource_uid, safe='')}/neighborhood?{urlencode(query)}",
            headers=self._headers(None),
            method="GET",
        )
        return ResourceNeighborhood.from_dict(self._send(request))

    def get_resource_timeline(
        self,
        resource_uid: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ResourceTimeline:
        """Read one tenant-scoped page of immutable resource observations."""

        query = [("limit", str(limit))]
        if cursor is not None:
            query.append(("cursor", cursor))
        request = Request(
            f"{self._base_url}/v1/resources/{quote(resource_uid, safe='')}/timeline?{urlencode(query)}",
            headers=self._headers(None),
            method="GET",
        )
        return ResourceTimeline.from_dict(self._send(request))

    def _headers(self, correlation_id: Optional[str]) -> Dict[str, str]:
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self._bearer_token}",
        }
        if correlation_id:
            headers["x-correlation-id"] = correlation_id
        return headers

    def _send(self, request: Request) -> Dict[str, Any]:
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                code = str(body.get("error", {}).get("code", "request.failed"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                code = "request.failed"
            raise ApiError(exc.code, code) from exc
        if not isinstance(payload, dict):
            raise ApiError(200, "response.invalid")
        return payload
