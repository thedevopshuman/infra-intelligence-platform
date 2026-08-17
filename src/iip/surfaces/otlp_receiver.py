"""Dedicated OTLP/HTTP intake surface with no control-plane routes."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from iip.bootstrap import build_otlp_receiver_runtime_from_env
from iip.surfaces.http import ApiHandler


class TokenBucketRateLimiter:
    """Bound per-channel request bursts before any telemetry body is read."""

    def __init__(
        self,
        requests_per_second: int,
        burst: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_second < 1 or requests_per_second > 100_000:
            raise ValueError("otlp.rate-limit.configuration.invalid")
        if burst < 1 or burst > 100_000:
            raise ValueError("otlp.rate-limit.configuration.invalid")
        self._rate = float(requests_per_second)
        self._burst = float(burst)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def admit(self, channel_id: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, updated_at = self._buckets.get(
                channel_id, (self._burst, now)
            )
            tokens = min(self._burst, tokens + max(0.0, now - updated_at) * self._rate)
            admitted = tokens >= 1.0
            self._buckets[channel_id] = (
                tokens - 1.0 if admitted else tokens,
                now,
            )
            return admitted


class OtlpReceiverHandler(ApiHandler):
    """Expose only health and selected OTLP signal endpoints."""

    server_version = "IIPOtlpReceiver/0.47.0"
    rate_limiter = TokenBucketRateLimiter(50, 100)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        if path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/readyz":
            self._readiness()
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "route.not_found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        if path == "/v1/metrics":
            self._receive_otlp_metrics()
            return
        if path == "/v1/logs":
            self._receive_otlp_logs()
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "route.not_found"}})

    def _admit_otlp_channel(self, channel_id: str) -> bool:
        return self.rate_limiter.admit(channel_id)


def _bounded_integer(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise ValueError("otlp.rate-limit.configuration.invalid") from None
    if value < 1 or value > maximum:
        raise ValueError("otlp.rate-limit.configuration.invalid")
    return value


def main() -> None:
    """Start the isolated OTLP/HTTP receiver."""

    host = os.environ.get("IIP_OTLP_HTTP_HOST", "0.0.0.0")
    port = _bounded_integer("IIP_OTLP_HTTP_PORT", 4318, 65_535)
    rate = _bounded_integer("IIP_OTLP_MAX_REQUESTS_PER_SECOND", 50, 100_000)
    burst = _bounded_integer("IIP_OTLP_REQUEST_BURST", 100, 100_000)
    runtime = build_otlp_receiver_runtime_from_env()
    try:
        OtlpReceiverHandler.runtime = runtime
        OtlpReceiverHandler.rate_limiter = TokenBucketRateLimiter(rate, burst)
        server = ThreadingHTTPServer((host, port), OtlpReceiverHandler)
        print(f"IIP OTLP receiver listening on http://{host}:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("IIP OTLP receiver stopped")
        finally:
            server.server_close()
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
