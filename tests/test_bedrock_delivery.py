from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    import run_bedrock_instrumentation_compatibility as compatibility  # noqa: E402

    BEDROCK_DEPENDENCIES = True
except ModuleNotFoundError:
    OTLPSpanExporter = None  # type: ignore[assignment,misc]
    ExportTraceServiceRequest = None  # type: ignore[assignment,misc]
    compatibility = None  # type: ignore[assignment]
    BEDROCK_DEPENDENCIES = False


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


@unittest.skipUnless(
    BEDROCK_DEPENDENCIES,
    "pinned Bedrock compatibility dependencies run in the Docker gate",
)
class BedrockDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        _CaptureHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_same_offline_span_is_exported_and_written_to_protected_correlation(self) -> None:
        endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/traces"
        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=5)
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            {
                "IIP_SOURCE_REVISION": "a" * 40,
                "IIP_SOURCE_DIRTY": "false",
                "IIP_CONTAINER_RUNTIME_VERSION": "test",
            },
            clear=False,
        ):
            correlation_path = Path(temporary) / "correlation.json"
            report = compatibility.compatibility_report(
                mode="offline",
                operation="converse-stream",
                model_id=compatibility.OFFLINE_MODEL,
                region="us-east-1",
                delivery_exporter=exporter,
                correlation_path=correlation_path,
                delivery_endpoint=endpoint,
            )
            correlation = json.loads(correlation_path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(correlation_path.stat().st_mode), 0o600)

        self.assertEqual(report["spec"]["status"], "compatible")
        self.assertGreaterEqual(len(_CaptureHandler.requests), 1)
        path, content_type, payload = _CaptureHandler.requests[-1]
        self.assertEqual(path, "/v1/traces")
        self.assertEqual(content_type, "application/x-protobuf")
        request = ExportTraceServiceRequest.FromString(payload)
        spans = [
            span
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
            if scope.scope.name == compatibility.SCOPE_NAME
        ]
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].trace_id.hex(), correlation["spec"]["traceId"])
        self.assertEqual(spans[0].span_id.hex(), correlation["spec"]["spanId"])
        self.assertFalse(correlation["spec"]["contentCaptured"])
        self.assertNotIn(b"Return the single word", payload)

    def test_http_delivery_requires_explicit_local_insecure_enablement(self) -> None:
        endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/traces"
        with patch.dict(
            os.environ,
            {"IIP_BEDROCK_OTLP_TRACES_ENDPOINT": endpoint},
            clear=True,
        ):
            with self.assertRaisesRegex(
                compatibility.CompatibilityFailure,
                "delivery.endpoint-invalid",
            ):
                compatibility._delivery_exporter()
        with patch.dict(
            os.environ,
            {
                "IIP_BEDROCK_OTLP_TRACES_ENDPOINT": endpoint,
                "IIP_BEDROCK_OTLP_ALLOW_INSECURE": "true",
            },
            clear=True,
        ):
            exporter, selected = compatibility._delivery_exporter()
        self.assertIsInstance(exporter, OTLPSpanExporter)
        self.assertEqual(selected, endpoint)
        assert exporter is not None
        exporter.shutdown()


if __name__ == "__main__":
    unittest.main()
