from __future__ import annotations

import copy
import json
import math
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
    NoDataTelemetryMetricsBackend,
    StructuredTextRedactor,
)
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.collect_evidence import (
    EvidenceCollectionService,
    EvidenceProviderUnavailableError,
    InvalidEvidenceRequestError,
)
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.ports import (
    ActorContext,
    TelemetryMetricPoint,
    TelemetryMetricSeries,
    TelemetryMetricsQuery,
    TelemetryMetricsResult,
)
from iip.application.telemetry_evidence import (
    CollectTelemetryEvidenceCommand,
    InvalidTelemetryEvidenceRequestError,
    TelemetryEvidenceService,
    TelemetryMetricsEvidenceProvider,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import Client, Evidence, TelemetryEvidenceRequest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "evd_abcdef0123456789abcdef0123456789"
TOKEN = "telemetry-evidence-reference-token-0123456789abcdef"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def result_from_example() -> TelemetryMetricsResult:
    payload = example("telemetry-evidence-result.json")
    series = payload["spec"]["series"]
    return TelemetryMetricsResult(
        executed_at=payload["metadata"]["createdAt"],
        status=payload["spec"]["status"],
        series=tuple(
            TelemetryMetricSeries(
                metric=item["metric"],
                unit=item["unit"],
                attributes=tuple(item["attributes"].items()),
                points=tuple(
                    TelemetryMetricPoint(point["timestamp"], point["value"])
                    for point in item["points"]
                ),
            )
            for item in series
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
        self.requests: list[TelemetryMetricsQuery] = []
        self.failure: Exception | None = None

    def query_metrics(self, request: TelemetryMetricsQuery) -> TelemetryMetricsResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.result  # type: ignore[return-value]


class TelemetryEvidenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("developer", "local")
        self.resources = InMemoryResourceStore()
        self.resource = ResourceIngestionService(
            self.resources, AllowTenantPolicy()
        ).execute(IngestResourceCommand(self.actor, example("resource.json")))
        self.assertEqual(
            self.resource.identity.uid,
            example("telemetry-evidence-request.json")["spec"]["resourceRefs"][0],
        )
        self.store = InMemoryEvidenceStore()
        self.clock = FixedClock()
        self.backend = RecordingBackend()

    def service(self, backend: object | None = None) -> TelemetryEvidenceService:
        selected_backend = self.backend if backend is None else backend
        evidence = EvidenceCollectionService(
            self.resources,
            {
                "telemetry-query": TelemetryMetricsEvidenceProvider(
                    selected_backend  # type: ignore[arg-type]
                )
            },
            self.store,
            StructuredTextRedactor(),
            AllowTenantPolicy(),
            FixedEvidenceIds(),
            self.clock,
        )
        return TelemetryEvidenceService(evidence, self.clock)

    def collect(self, request: dict | None = None) -> dict:
        return dict(
            self.service().execute(
                CollectTelemetryEvidenceCommand(
                    self.actor,
                    request or example("telemetry-evidence-request.json"),
                )
            )
        )

    def test_query_is_provider_neutral_and_result_becomes_immutable_evidence(self) -> None:
        evidence = self.collect()

        evidence_schema = example_schema("evidence.schema.json")
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                evidence_schema, evidence, label="telemetry evidence"
            ),
            [],
        )
        artifact = self.store.read_artifact(self.actor, EVIDENCE_ID)
        self.assertIsNotNone(artifact)
        normalized = json.loads(artifact)
        self.assertEqual(normalized, example("telemetry-evidence-result.json"))
        result_schema = example_schema("telemetry-evidence-result.schema.json")
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                result_schema, normalized, label="normalized telemetry artifact"
            ),
            [],
        )
        self.assertEqual(evidence["spec"]["type"], "telemetry.metrics")
        self.assertEqual(evidence["spec"]["source"]["provider"], "telemetry-query")
        self.assertEqual(evidence["spec"]["handling"]["retentionClass"], "ephemeral")

        query = self.backend.requests[0]
        self.assertEqual(query.tenant_id, "local")
        self.assertEqual(query.actor_id, "developer")
        self.assertEqual(query.metric, "k8s.pod.container.restarts")
        self.assertEqual(
            query.filters,
            (("k8s.namespace.name", "eq", "default"),),
        )
        self.assertEqual(query.aggregation, "max")
        self.assertEqual(query.group_by, ("k8s.pod.name",))
        self.assertFalse(hasattr(query, "credentials"))
        self.assertFalse(hasattr(query, "endpoint"))

    def test_contract_identity_time_bounds_and_secret_checks_run_before_backend(self) -> None:
        requests = []
        wrong_actor = copy.deepcopy(example("telemetry-evidence-request.json"))
        wrong_actor["metadata"]["actorId"] = "another-actor"
        requests.append(wrong_actor)
        reversed_range = copy.deepcopy(example("telemetry-evidence-request.json"))
        reversed_range["spec"]["timeRange"]["start"] = "2026-08-14T10:30:01Z"
        requests.append(reversed_range)
        secret_filter = copy.deepcopy(example("telemetry-evidence-request.json"))
        secret_filter["spec"]["query"]["filters"][0]["value"] = "token=fake-value"
        requests.append(secret_filter)
        excessive_limit = copy.deepcopy(example("telemetry-evidence-request.json"))
        excessive_limit["spec"]["limits"]["maxSeries"] = 101
        requests.append(excessive_limit)
        unknown_field = copy.deepcopy(example("telemetry-evidence-request.json"))
        unknown_field["spec"]["providerQuery"] = "up"
        requests.append(unknown_field)

        service = self.service()
        for request in requests:
            with self.subTest(request=request):
                with self.assertRaisesRegex(
                    InvalidTelemetryEvidenceRequestError,
                    "telemetry.request.invalid",
                ):
                    service.execute(CollectTelemetryEvidenceCommand(self.actor, request))
        self.assertEqual(self.backend.requests, [])

    def test_cross_tenant_or_missing_resource_never_reaches_backend(self) -> None:
        request = copy.deepcopy(example("telemetry-evidence-request.json"))
        request["spec"]["resourceRefs"] = [
            "res_00000000000000000000000000000000"
        ]

        with self.assertRaisesRegex(
            InvalidEvidenceRequestError, "evidence.resource.unavailable"
        ):
            self.service().execute(CollectTelemetryEvidenceCommand(self.actor, request))

        self.assertEqual(self.backend.requests, [])
        self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_untrusted_backend_output_fails_closed_with_stable_error(self) -> None:
        base = result_from_example()
        point = base.series[0].points[0]
        invalid_results = (
            replace(
                base,
                series=(
                    replace(
                        base.series[0],
                        points=(replace(point, value=math.inf),),
                    ),
                ),
            ),
            replace(
                base,
                series=(replace(base.series[0], metric="another.metric"),),
            ),
            replace(base, status="no-data"),
            {"provider": "raw response"},
        )
        for invalid in invalid_results:
            with self.subTest(invalid=invalid):
                self.store = InMemoryEvidenceStore()
                backend = RecordingBackend(invalid)
                with self.assertRaises(EvidenceProviderUnavailableError) as raised:
                    self.service(backend).execute(
                        CollectTelemetryEvidenceCommand(
                            self.actor, example("telemetry-evidence-request.json")
                        )
                    )
                self.assertEqual(
                    str(raised.exception), "evidence.provider.unavailable"
                )
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_reference_backend_returns_honest_no_data_result(self) -> None:
        evidence = self.service(NoDataTelemetryMetricsBackend(self.clock)).execute(
            CollectTelemetryEvidenceCommand(
                self.actor, example("telemetry-evidence-request.json")
            )
        )

        artifact = self.store.read_artifact(self.actor, evidence["metadata"]["id"])
        result = json.loads(artifact)
        self.assertEqual(result["spec"]["status"], "no-data")
        self.assertEqual(result["spec"]["series"], [])
        self.assertEqual(
            result["spec"]["summary"],
            {"seriesCount": 0, "dataPointCount": 0},
        )


class TelemetryEvidenceHttpAndSdkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(
                                TOKEN
                            ),
                            "actorId": "developer",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        self.runtime = build_local_runtime(self.authenticator)
        self.actor = ActorContext("developer", "local")
        self.runtime.ingestion.execute(
            IngestResourceCommand(self.actor, example("resource.json"))
        )

    def live_request(self) -> dict:
        request = copy.deepcopy(example("telemetry-evidence-request.json"))
        requested_at = datetime.now(timezone.utc)
        request["metadata"]["requestedAt"] = iso(requested_at)
        request["spec"]["timeRange"] = {
            "start": iso(requested_at - timedelta(minutes=5)),
            "end": iso(requested_at - timedelta(seconds=1)),
        }
        request["spec"]["deadline"] = iso(requested_at + timedelta(minutes=1))
        return request

    def test_authenticated_http_collection_persists_normalized_artifact(self) -> None:
        handler = object.__new__(ApiHandler)
        handler.runtime = self.runtime
        handler.path = "/v1/evidence/telemetry/queries"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler._read_json = self.live_request
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_POST()

        self.assertEqual(len(responses), 1)
        status, evidence = responses[0]
        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertEqual(evidence["kind"], "Evidence")
        self.assertEqual(evidence["metadata"]["tenantId"], "local")
        artifact = self.runtime.evidence_store.read_artifact(
            self.actor, evidence["metadata"]["id"]
        )
        result = json.loads(artifact)
        self.assertEqual(result["kind"], "TelemetryEvidenceResult")
        self.assertEqual(result["spec"]["status"], "no-data")
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                example_schema("telemetry-evidence-result.schema.json"),
                result,
                label="HTTP telemetry result artifact",
            ),
            [],
        )

    def test_http_rejects_payload_identity_override(self) -> None:
        request = self.live_request()
        request["metadata"]["tenantId"] = "another-tenant"
        handler = object.__new__(ApiHandler)
        handler.runtime = self.runtime
        handler.path = "/v1/evidence/telemetry/queries"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler._read_json = lambda: request
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_POST()

        self.assertEqual(
            responses,
            [
                (
                    HTTPStatus.BAD_REQUEST,
                    {"error": {"code": "telemetry.request.invalid"}},
                )
            ],
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

        request = TelemetryEvidenceRequest.from_dict(
            example("telemetry-evidence-request.json")
        )
        client = Client("https://control.example", TOKEN)
        with patch(
            "infra_intelligence_sdk.client.urlopen", return_value=Response()
        ) as send:
            evidence = client.collect_telemetry_evidence(request)

        self.assertIsInstance(evidence, Evidence)
        outgoing = send.call_args.args[0]
        self.assertEqual(
            outgoing.full_url,
            "https://control.example/v1/evidence/telemetry/queries",
        )
        self.assertEqual(json.loads(outgoing.data), request.to_dict())
        self.assertEqual(outgoing.get_header("Authorization"), f"Bearer {TOKEN}")


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def example_schema(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "schemas" / name).read_text())


if __name__ == "__main__":
    unittest.main()
