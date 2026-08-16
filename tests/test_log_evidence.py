from __future__ import annotations

import copy
import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.evidence import (
    InMemoryEvidenceStore,
    NoDataTelemetryLogsBackend,
    StructuredTextRedactor,
)
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.collect_evidence import (
    EvidenceCollectionService,
    EvidenceProviderUnavailableError,
    InvalidEvidenceRequestError,
)
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.log_evidence import (
    CollectLogEvidenceCommand,
    InvalidLogEvidenceRequestError,
    LogEvidenceService,
    TelemetryLogsEvidenceProvider,
)
from iip.application.ports import (
    ActorContext,
    TelemetryLogRecord,
    TelemetryLogsQuery,
    TelemetryLogsResult,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import Client, Evidence, LogEvidenceRequest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "evd_fedcba9876543210fedcba9876543210"
TOKEN = "log-evidence-reference-token-0123456789abcdef"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def schema(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "schemas" / name).read_text())


def result_from_example() -> TelemetryLogsResult:
    payload = example("log-evidence-result.json")
    records = payload["spec"]["records"]
    return TelemetryLogsResult(
        executed_at=payload["metadata"]["createdAt"],
        status=payload["spec"]["status"],
        records=tuple(
            TelemetryLogRecord(
                record_id=item["id"],
                resource_uid=item["resourceRef"],
                timestamp=item["timestamp"],
                observed_timestamp=item.get("observedTimestamp"),
                severity=item["severity"],
                service_name=item["serviceName"],
                body=item["body"],
                attributes=tuple(item["attributes"].items()),
                trace_id=item.get("traceId"),
                span_id=item.get("spanId"),
            )
            for item in records
        ),
        warnings=tuple(payload["spec"]["warnings"]),
    )


class FixedClock:
    def __init__(self, value: str = "2026-08-14T10:30:04Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class FixedEvidenceIds:
    def new_id(self) -> str:
        return EVIDENCE_ID


class RecordingBackend:
    def __init__(self, result: object | None = None) -> None:
        self.result = result if result is not None else result_from_example()
        self.requests: list[TelemetryLogsQuery] = []

    def query_logs(self, request: TelemetryLogsQuery) -> TelemetryLogsResult:
        self.requests.append(request)
        return self.result  # type: ignore[return-value]


class LogEvidenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("developer", "local")
        self.resources = InMemoryResourceStore()
        ResourceIngestionService(self.resources, AllowTenantPolicy()).execute(
            IngestResourceCommand(self.actor, example("resource.json"))
        )
        self.store = InMemoryEvidenceStore()
        self.clock = FixedClock()
        self.backend = RecordingBackend()

    def service(self, backend: object | None = None) -> LogEvidenceService:
        selected = self.backend if backend is None else backend
        evidence = EvidenceCollectionService(
            self.resources,
            {"log-query": TelemetryLogsEvidenceProvider(selected)},  # type: ignore[arg-type]
            self.store,
            StructuredTextRedactor(),
            AllowTenantPolicy(),
            FixedEvidenceIds(),
            self.clock,
        )
        return LogEvidenceService(evidence, self.clock)

    def test_query_is_provider_neutral_and_result_is_canonical_evidence(self) -> None:
        evidence = self.service().execute(
            CollectLogEvidenceCommand(self.actor, example("log-evidence-request.json"))
        )

        artifact = self.store.read_artifact(self.actor, EVIDENCE_ID)
        self.assertEqual(json.loads(artifact), example("log-evidence-result.json"))
        self.assertEqual(evidence["spec"]["type"], "telemetry.logs")
        self.assertEqual(evidence["spec"]["source"]["provider"], "log-query")
        self.assertEqual(evidence["spec"]["handling"]["sensitivity"], "confidential")
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema("log-evidence-result.schema.json"),
                json.loads(artifact),
                label="normalized log artifact",
            ),
            [],
        )

        query = self.backend.requests[0]
        self.assertEqual(query.tenant_id, "local")
        self.assertEqual(query.actor_id, "developer")
        self.assertEqual(query.service_names, ("checkout",))
        self.assertEqual(query.severities, ("error", "fatal"))
        self.assertEqual(query.filters, (("k8s.namespace.name", "eq", "default"),))
        self.assertFalse(hasattr(query, "credentials"))
        self.assertFalse(hasattr(query, "endpoint"))
        self.assertFalse(hasattr(query, "logql"))

    def test_identity_time_secret_and_vendor_fields_fail_before_backend(self) -> None:
        invalid_requests = []
        wrong_actor = copy.deepcopy(example("log-evidence-request.json"))
        wrong_actor["metadata"]["actorId"] = "another-actor"
        invalid_requests.append(wrong_actor)
        reversed_range = copy.deepcopy(example("log-evidence-request.json"))
        reversed_range["spec"]["timeRange"]["start"] = "2026-08-14T10:30:01Z"
        invalid_requests.append(reversed_range)
        secret_filter = copy.deepcopy(example("log-evidence-request.json"))
        secret_filter["spec"]["query"]["filters"][0]["value"] = "token=fake-value"
        invalid_requests.append(secret_filter)
        vendor_query = copy.deepcopy(example("log-evidence-request.json"))
        vendor_query["spec"]["query"]["logql"] = "{namespace=\"default\"}"
        invalid_requests.append(vendor_query)

        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaisesRegex(
                    InvalidLogEvidenceRequestError, "logs.request.invalid"
                ):
                    self.service().execute(CollectLogEvidenceCommand(self.actor, request))
        self.assertEqual(self.backend.requests, [])

    def test_missing_resource_and_out_of_scope_backend_records_fail_closed(self) -> None:
        request = copy.deepcopy(example("log-evidence-request.json"))
        request["spec"]["resourceRefs"] = ["res_" + "0" * 32]
        with self.assertRaisesRegex(
            InvalidEvidenceRequestError, "evidence.resource.unavailable"
        ):
            self.service().execute(CollectLogEvidenceCommand(self.actor, request))
        self.assertEqual(self.backend.requests, [])

        base = result_from_example()
        invalid = replace(
            base,
            records=(replace(base.records[0], service_name="another-service"),),
        )
        with self.assertRaisesRegex(
            EvidenceProviderUnavailableError, "evidence.provider.unavailable"
        ):
            self.service(RecordingBackend(invalid)).execute(
                CollectLogEvidenceCommand(self.actor, example("log-evidence-request.json"))
            )

    def test_log_body_secrets_are_redacted_before_commit(self) -> None:
        base = result_from_example()
        result = replace(
            base,
            records=(replace(base.records[0], body="token=customer-secret"),),
        )
        evidence = self.service(RecordingBackend(result)).execute(
            CollectLogEvidenceCommand(self.actor, example("log-evidence-request.json"))
        )

        artifact = self.store.read_artifact(self.actor, evidence["metadata"]["id"])
        self.assertNotIn(b"customer-secret", artifact)
        self.assertIn(b"[REDACTED]", artifact)
        self.assertEqual(evidence["spec"]["handling"]["redaction"]["status"], "applied")

    def test_reference_backend_returns_honest_no_data(self) -> None:
        evidence = self.service(NoDataTelemetryLogsBackend(self.clock)).execute(
            CollectLogEvidenceCommand(self.actor, example("log-evidence-request.json"))
        )
        artifact = json.loads(
            self.store.read_artifact(self.actor, evidence["metadata"]["id"])
        )
        self.assertEqual(artifact["spec"]["status"], "no-data")
        self.assertEqual(artifact["spec"]["records"], [])
        self.assertEqual(
            artifact["spec"]["summary"], {"recordCount": 0, "errorCount": 0}
        )


class LogEvidenceHttpAndSdkTests(unittest.TestCase):
    def setUp(self) -> None:
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(TOKEN),
                            "actorId": "developer",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        self.runtime = build_local_runtime(authenticator)
        self.actor = ActorContext("developer", "local")
        self.runtime.ingestion.execute(
            IngestResourceCommand(self.actor, example("resource.json"))
        )

    def live_request(self) -> dict:
        request = copy.deepcopy(example("log-evidence-request.json"))
        requested_at = datetime.now(timezone.utc)
        request["metadata"]["requestedAt"] = iso(requested_at)
        request["spec"]["timeRange"] = {
            "start": iso(requested_at - timedelta(minutes=5)),
            "end": iso(requested_at - timedelta(seconds=1)),
        }
        request["spec"]["deadline"] = iso(requested_at + timedelta(minutes=1))
        return request

    def test_authenticated_http_collection_and_identity_failure(self) -> None:
        handler = object.__new__(ApiHandler)
        handler.runtime = self.runtime
        handler.path = "/v1/evidence/logs/queries"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler._read_json = self.live_request
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))
        handler.do_POST()

        self.assertEqual(responses[0][0], HTTPStatus.CREATED)
        evidence = responses[0][1]
        artifact = json.loads(
            self.runtime.evidence_store.read_artifact(
                self.actor, evidence["metadata"]["id"]
            )
        )
        self.assertEqual(artifact["kind"], "LogEvidenceResult")
        self.assertEqual(artifact["spec"]["status"], "no-data")

        invalid = self.live_request()
        invalid["metadata"]["tenantId"] = "another-tenant"
        handler._read_json = lambda: invalid
        responses.clear()
        handler.do_POST()
        self.assertEqual(
            responses,
            [(HTTPStatus.BAD_REQUEST, {"error": {"code": "logs.request.invalid"}})],
        )

    def test_python_sdk_posts_public_request_and_parses_evidence(self) -> None:
        payload = example("evidence.json")

        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode()

        request = LogEvidenceRequest.from_dict(example("log-evidence-request.json"))
        client = Client("https://control.example", TOKEN)
        with patch("infra_intelligence_sdk.client.urlopen", return_value=Response()) as send:
            evidence = client.collect_log_evidence(request)

        self.assertIsInstance(evidence, Evidence)
        outgoing = send.call_args.args[0]
        self.assertEqual(
            outgoing.full_url, "https://control.example/v1/evidence/logs/queries"
        )
        self.assertEqual(json.loads(outgoing.data), request.to_dict())


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
