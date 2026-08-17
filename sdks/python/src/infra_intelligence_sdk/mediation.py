"""Small Unix-socket client for the private plugin runner mediation protocol."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from .models import (
    PluginActionMediationRequest,
    PluginActionMediationResponse,
    PluginMediationRequest,
    PluginMediationResponse,
)


class PluginMediationClientError(RuntimeError):
    """Local socket or framing failure without host or provider details."""


class PluginMediationClient:
    """Send one bounded request per invocation-local socket connection."""

    def __init__(
        self,
        socket_path: str = "/run/iip-mediation/request.sock",
        *,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 4_194_304,
    ) -> None:
        if (
            not isinstance(socket_path, str)
            or not Path(socket_path).is_absolute()
            or "\x00" in socket_path
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0.1 <= timeout_seconds <= 120
            or not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or not 1024 <= max_response_bytes <= 4_194_304
        ):
            raise ValueError("plugin mediation client configuration is invalid")
        self._socket_path = socket_path
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes

    def request(self, request: PluginMediationRequest) -> PluginMediationResponse:
        if not isinstance(request, PluginMediationRequest):
            raise TypeError("request must be PluginMediationRequest")
        response = self._exchange(
            request.to_dict(),
            response_type=PluginMediationResponse,
        )
        assert isinstance(response, PluginMediationResponse)
        return response

    def propose_action(
        self, request: PluginActionMediationRequest
    ) -> PluginActionMediationResponse:
        """Request one governed proposal without receiving execution authority."""

        if not isinstance(request, PluginActionMediationRequest):
            raise TypeError("request must be PluginActionMediationRequest")
        response = self._exchange(
            request.to_dict(),
            response_type=PluginActionMediationResponse,
        )
        assert isinstance(response, PluginActionMediationResponse)
        return response

    def _exchange(
        self,
        request_document: dict[str, object],
        *,
        response_type: type[PluginMediationResponse]
        | type[PluginActionMediationResponse],
    ) -> PluginMediationResponse | PluginActionMediationResponse:
        body = json.dumps(
            request_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > 65_536:
            raise PluginMediationClientError("plugin.mediation.request-invalid")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self._timeout_seconds)
                connection.connect(self._socket_path)
                connection.sendall(body + b"\n")
                received = bytearray()
                while b"\n" not in received:
                    chunk = connection.recv(65_536)
                    if not chunk:
                        raise OSError
                    received.extend(chunk)
                    if len(received) > self._max_response_bytes + 1:
                        raise OSError
        except OSError:
            raise PluginMediationClientError("plugin.mediation.unavailable") from None
        line, suffix = bytes(received).split(b"\n", 1)
        if suffix:
            raise PluginMediationClientError("plugin.mediation.response-invalid")
        try:
            document = json.loads(line)
            if not isinstance(document, dict):
                raise TypeError
            response = response_type.from_dict(document)
            request_metadata = request_document["metadata"]
            response_metadata = response.payload["metadata"]
            if (
                not isinstance(request_metadata, dict)
                or not isinstance(response_metadata, dict)
                or response_metadata.get("requestId") != request_metadata.get("id")
                or response_metadata.get("invocationId")
                != request_metadata.get("invocationId")
            ):
                raise TypeError
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise PluginMediationClientError(
                "plugin.mediation.response-invalid"
            ) from None
        return response
