"""Dependency-free synchronous HTTP client."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .errors import ApiError
from .models import ResourceObservation


class Client:
    """Tenant- and actor-scoped control-plane client."""

    def __init__(
        self,
        base_url: str,
        tenant_id: str,
        actor_id: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._tenant_id = tenant_id
        self._actor_id = actor_id
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

    def _headers(self, correlation_id: Optional[str]) -> Dict[str, str]:
        headers = {
            "content-type": "application/json",
            "x-iip-tenant-id": self._tenant_id,
            "x-iip-actor-id": self._actor_id,
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

