#!/usr/bin/env python3
"""Prove the pinned Bedrock span can leave the process over OTLP/HTTP."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

import run_bedrock_instrumentation_compatibility as compatibility


class _CaptureHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, bytes]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        payload = self.rfile.read(length)
        self.requests.append(
            (self.path, self.headers.get("content-type", ""), payload)
        )
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
        with tempfile.TemporaryDirectory(prefix="iip-bedrock-delivery-") as temporary:
            headers_path = Path(temporary) / "headers.json"
            headers_path.write_text(
                '{"Authorization":"Bearer qualification-only"}\n',
                encoding="utf-8",
            )
            headers_path.chmod(0o600)
            with patch.dict(
                os.environ,
                {
                    "IIP_BEDROCK_OTLP_TRACES_ENDPOINT": endpoint,
                    "IIP_BEDROCK_OTLP_ALLOW_INSECURE": "true",
                    "IIP_BEDROCK_OTLP_HEADERS_FILE": str(headers_path),
                },
                clear=True,
            ):
                configured_exporter, configured_endpoint = (
                    compatibility._delivery_exporter()
                )
            compatibility.require(
                configured_exporter is not None and configured_endpoint == endpoint,
                "delivery.configuration-invalid",
            )
            configured_exporter.shutdown()

            headers_path.chmod(0o644)
            try:
                compatibility._protected_headers(headers_path)
            except compatibility.CompatibilityFailure:
                pass
            else:
                raise compatibility.CompatibilityFailure(
                    "delivery.headers-mode-accepted"
                )

            with patch.dict(
                os.environ,
                {
                    "IIP_BEDROCK_OTLP_TRACES_ENDPOINT": endpoint + "?secret=value",
                    "IIP_BEDROCK_OTLP_ALLOW_INSECURE": "true",
                },
                clear=True,
            ):
                try:
                    compatibility._delivery_exporter()
                except compatibility.CompatibilityFailure:
                    pass
                else:
                    raise compatibility.CompatibilityFailure(
                        "delivery.endpoint-query-accepted"
                    )

            correlation_path = Path(temporary) / "correlation.json"
            report = compatibility.compatibility_report(
                mode="offline",
                operation="converse-stream",
                model_id=compatibility.OFFLINE_MODEL,
                region="us-east-1",
                delivery_exporter=OTLPSpanExporter(endpoint=endpoint, timeout=5),
                correlation_path=correlation_path,
                delivery_endpoint=endpoint,
            )
            correlation = json.loads(correlation_path.read_text(encoding="utf-8"))
            compatibility.require(
                stat.S_IMODE(correlation_path.stat().st_mode) == 0o600,
                "delivery.correlation-mode-invalid",
            )
        compatibility.require(
            report["spec"]["status"] == "compatible"
            and len(_CaptureHandler.requests) >= 1,
            "delivery.export-missing",
        )
        path, content_type, payload = _CaptureHandler.requests[-1]
        request = ExportTraceServiceRequest.FromString(payload)
        spans = [
            span
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
            if scope.scope.name == compatibility.SCOPE_NAME
        ]
        compatibility.require(
            path == "/v1/traces"
            and content_type == "application/x-protobuf"
            and len(spans) == 1
            and spans[0].trace_id.hex() == correlation["spec"]["traceId"]
            and spans[0].span_id.hex() == correlation["spec"]["spanId"]
            and correlation["spec"]["contentCaptured"] is False
            and b"Return the single word" not in payload,
            "delivery.correlation-invalid",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print("Bedrock OTLP delivery and protected exact correlation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
