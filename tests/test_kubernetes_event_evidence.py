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
    NoDataKubernetesEventsBackend,
    ResourceStateEvidenceProvider,
    StructuredTextRedactor,
)
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
from iip.application.collect_evidence import (
    EvidenceCollectionService,
    EvidenceProviderUnavailableError,
)
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.investigate import (
    DeterministicInvestigationService,
    InvalidInvestigationError,
    RunInvestigationCommand,
)
from iip.application.kubernetes_event_evidence import (
    CollectKubernetesEventEvidenceCommand,
    InvalidKubernetesEventEvidenceRequestError,
    KubernetesEventEvidenceService,
    KubernetesEventsEvidenceProvider,
)
from iip.application.ports import (
    ActorContext,
    KubernetesEventQuery,
    KubernetesEventRecord,
    KubernetesEventsResult,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import (
    Client,
    Evidence,
    KubernetesEventEvidenceRequest,
)


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "kubernetes-event-reference-token-0123456789abcdef"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def schema(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "schemas" / name).read_text())


class FixedClock:
    def __init__(self, value: str = "2026-08-16T10:30:05Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class SequentialEvidenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"evd_{self.value:032x}"


def result_from_example() -> KubernetesEventsResult:
    payload = example("kubernetes-event-evidence-result.json")
    events = payload["spec"]["events"]
    return KubernetesEventsResult(
        executed_at=payload["metadata"]["createdAt"],
        status=payload["spec"]["status"],
        events=tuple(
            KubernetesEventRecord(
                event_id=item["id"],
                resource_uid=item["resourceRef"],
                severity=item["severity"],
                reason=item["reason"],
                condition=item["condition"],
                first_observed_at=item["firstObservedAt"],
                last_observed_at=item["lastObservedAt"],
                occurrence_count=item["occurrenceCount"],
                reporting_controller=item.get("reportingController"),
                message=item.get("message"),
            )
            for item in events
        ),
        warnings=tuple(payload["spec"]["warnings"]),
    )


class RecordingBackend:
    def __init__(self, result: object | None = None) -> None:
        self.result = result if result is not None else result_from_example()
        self.requests: list[KubernetesEventQuery] = []

    def query_events(self, request: KubernetesEventQuery) -> KubernetesEventsResult:
        self.requests.append(request)
        return self.result  # type: ignore[return-value]


class KubernetesEventEvidenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("developer", "local")
        self.resources = InMemoryResourceStore()
        ResourceIngestionService(self.resources, AllowTenantPolicy()).execute(
            IngestResourceCommand(self.actor, example("resource.json"))
        )
        self.store = InMemoryEvidenceStore()
        self.clock = FixedClock()
        self.backend = RecordingBackend()

    def service(self, backend: object | None = None) -> KubernetesEventEvidenceService:
        selected = self.backend if backend is None else backend
        evidence = EvidenceCollectionService(
            self.resources,
            {
                "kubernetes-events": KubernetesEventsEvidenceProvider(
                    selected,  # type: ignore[arg-type]
                    self.resources,
                )
            },
            self.store,
            StructuredTextRedactor(),
            AllowTenantPolicy(),
            SequentialEvidenceIds(),
            self.clock,
        )
        return KubernetesEventEvidenceService(evidence, self.clock)

    def test_query_is_credential_free_and_result_is_canonical_evidence(self) -> None:
        evidence = self.service().execute(
            CollectKubernetesEventEvidenceCommand(
                self.actor,
                example("kubernetes-event-evidence-request.json"),
            )
        )

        artifact = self.store.read_artifact(self.actor, evidence["metadata"]["id"])
        self.assertEqual(json.loads(artifact), example("kubernetes-event-evidence-result.json"))
        self.assertEqual(evidence["spec"]["type"], "kubernetes.event")
        query = self.backend.requests[0]
        self.assertEqual(query.tenant_id, "local")
        self.assertEqual(query.actor_id, "developer")
        self.assertEqual(query.severities, ("warning",))
        self.assertEqual(query.reasons, ("ProgressDeadlineExceeded",))
        self.assertEqual(query.resources[0].provider, "kubernetes")
        self.assertEqual(query.resources[0].resource_type, "apps/deployment")
        self.assertFalse(hasattr(query, "credentials"))
        self.assertFalse(hasattr(query, "kubeconfig"))

    def test_request_and_backend_boundaries_fail_closed(self) -> None:
        invalid_request = copy.deepcopy(
            example("kubernetes-event-evidence-request.json")
        )
        invalid_request["spec"]["query"]["kubectlArgs"] = ["--all-namespaces"]
        with self.assertRaisesRegex(
            InvalidKubernetesEventEvidenceRequestError,
            "kubernetes.event.request.invalid",
        ):
            self.service().execute(
                CollectKubernetesEventEvidenceCommand(self.actor, invalid_request)
            )
        self.assertEqual(self.backend.requests, [])

        base = result_from_example()
        invalid_result = replace(
            base,
            events=(replace(base.events[0], resource_uid="res_" + "0" * 32),),
        )
        with self.assertRaisesRegex(
            EvidenceProviderUnavailableError,
            "evidence.provider.unavailable",
        ):
            self.service(RecordingBackend(invalid_result)).execute(
                CollectKubernetesEventEvidenceCommand(
                    self.actor,
                    example("kubernetes-event-evidence-request.json"),
                )
            )

    def test_message_secrets_are_redacted_before_commit(self) -> None:
        base = result_from_example()
        result = replace(
            base,
            events=(replace(base.events[0], message="token=customer-secret"),),
        )
        evidence = self.service(RecordingBackend(result)).execute(
            CollectKubernetesEventEvidenceCommand(
                self.actor,
                example("kubernetes-event-evidence-request.json"),
            )
        )

        artifact = self.store.read_artifact(self.actor, evidence["metadata"]["id"])
        self.assertNotIn(b"customer-secret", artifact)
        self.assertIn(b"[REDACTED]", artifact)
        self.assertEqual(
            evidence["spec"]["handling"]["redaction"]["status"], "applied"
        )

    def test_reference_backend_returns_honest_no_data(self) -> None:
        evidence = self.service(NoDataKubernetesEventsBackend(self.clock)).execute(
            CollectKubernetesEventEvidenceCommand(
                self.actor,
                example("kubernetes-event-evidence-request.json"),
            )
        )
        artifact = self.store.read_artifact(self.actor, evidence["metadata"]["id"])
        result = json.loads(artifact)
        self.assertEqual(result["spec"]["status"], "no-data")
        self.assertEqual(result["spec"]["events"], [])
        self.assertEqual(result["spec"]["summary"]["eventCount"], 0)


class KubernetesEventInvestigationTests(unittest.TestCase):
    def test_no_data_partial_and_corrupt_artifacts_are_conservative(self) -> None:
        actor = ActorContext("developer", "local")
        selection = example("investigation-request-kubernetes-events.json")["spec"][
            "kubernetesEventSelections"
        ][0]
        event_request = example("kubernetes-event-evidence-request.json")
        evidence = {"metadata": {"id": "evd_" + "7" * 32}}

        class ArtifactStore:
            artifact = b""

            def read_artifact(self, actor_context: object, evidence_id: str) -> bytes:
                del actor_context, evidence_id
                return self.artifact

        store = ArtifactStore()
        service = DeterministicInvestigationService(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            FixedClock(),
            evidence_store=store,  # type: ignore[arg-type]
        )

        no_data = example("kubernetes-event-evidence-result.json")
        no_data["spec"].update(
            {
                "status": "no-data",
                "events": [],
                "summary": {"eventCount": 0, "warningEventCount": 0},
                "warnings": [],
            }
        )
        store.artifact = json.dumps(no_data).encode()
        assessment = service._assess_kubernetes_events(
            actor,
            selection,
            evidence,
            "kubernetes.rollout.unavailable-replicas",
            event_request,
        )
        self.assertEqual(assessment["disposition"], "no-data")
        self.assertNotIn("matchedEventCount", assessment)

        partial = example("kubernetes-event-evidence-result.json")
        partial["spec"]["status"] = "partial"
        partial["spec"]["warnings"] = ["backend-partial"]
        store.artifact = json.dumps(partial).encode()
        assessment = service._assess_kubernetes_events(
            actor,
            selection,
            evidence,
            "kubernetes.rollout.unavailable-replicas",
            event_request,
        )
        self.assertEqual(assessment["disposition"], "incomplete")
        self.assertNotIn("matchedEventIds", assessment)

        partial["spec"]["events"][0]["condition"] = "INVALID"
        store.artifact = json.dumps(partial).encode()
        self.assertIsNone(
            service._assess_kubernetes_events(
                actor,
                selection,
                evidence,
                "kubernetes.rollout.unavailable-replicas",
                event_request,
            )
        )

    def test_invalid_event_selection_fails_before_collection(self) -> None:
        actor = ActorContext("developer", "local")
        request = example("investigation-request-kubernetes-events.json")
        request["spec"]["kubernetesEventSelections"][0]["query"][
            "fieldSelector"
        ] = "type=Warning"

        with self.assertRaisesRegex(
            InvalidInvestigationError,
            "investigation.contract.invalid",
        ):
            DeterministicInvestigationService._validate(
                RunInvestigationCommand(actor, request)
            )

        request = example("investigation-request-kubernetes-events.json")
        selection = request["spec"]["kubernetesEventSelections"][0]
        selection["interpretation"]["minMatches"] = 101
        selection["limits"]["maxEvents"] = 100
        with self.assertRaisesRegex(
            InvalidInvestigationError,
            "investigation.contract.invalid",
        ):
            DeterministicInvestigationService._validate(
                RunInvestigationCommand(actor, request)
            )

    def test_matching_event_condition_becomes_a_cited_assessment(self) -> None:
        actor = ActorContext("developer", "local")
        resources = InMemoryResourceStore()
        policy = AllowTenantPolicy()
        ResourceIngestionService(resources, policy).execute(
            IngestResourceCommand(actor, example("resource.json"))
        )
        clock = FixedClock("2026-08-16T10:30:08Z")

        class InvestigationBackend:
            def query_events(
                self, request: KubernetesEventQuery
            ) -> KubernetesEventsResult:
                return KubernetesEventsResult(
                    executed_at=clock.now(),
                    status="complete",
                    events=(
                        KubernetesEventRecord(
                            event_id="kve_79584db884b6478ea438561b6a803913",
                            resource_uid=request.resources[0].platform_uid,
                            severity="warning",
                            reason="ProgressDeadlineExceeded",
                            condition="workload.progress-deadline-exceeded",
                            first_observed_at="2026-08-16T10:26:52Z",
                            last_observed_at="2026-08-16T10:29:54Z",
                            occurrence_count=4,
                        ),
                    ),
                )

        store = InMemoryEvidenceStore()
        event_provider = KubernetesEventsEvidenceProvider(
            InvestigationBackend(), resources
        )
        evidence = EvidenceCollectionService(
            resources,
            {
                "resource-state": ResourceStateEvidenceProvider(resources),
                "kubernetes-events": event_provider,
            },
            store,
            StructuredTextRedactor(),
            policy,
            SequentialEvidenceIds(),
            clock,
        )
        event_service = KubernetesEventEvidenceService(evidence, clock)
        service = DeterministicInvestigationService(
            resources,
            evidence,
            InMemoryOperationalStore(),
            clock,
            kubernetes_events=event_service,
            evidence_store=store,
        )

        report = service.execute(
            RunInvestigationCommand(
                actor,
                example("investigation-request-kubernetes-events.json"),
            )
        )

        assessment = report["spec"]["kubernetesEventAssessments"][0]
        hypothesis = report["spec"]["hypotheses"][0]
        self.assertEqual(assessment["matchedEventCount"], 1)
        self.assertEqual(assessment["disposition"], "supporting")
        self.assertIn(assessment["evidenceId"], hypothesis["supportingEvidenceIds"])
        self.assertEqual(report["spec"]["usage"]["toolCalls"], 2)
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema("investigation-report.schema.json"),
                report,
                label="event investigation",
            ),
            [],
        )


class KubernetesEventHttpAndSdkTests(unittest.TestCase):
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
        request = copy.deepcopy(example("kubernetes-event-evidence-request.json"))
        now = datetime.now(timezone.utc)
        request["metadata"]["requestedAt"] = iso(now)
        request["spec"]["timeRange"] = {
            "start": iso(now - timedelta(minutes=5)),
            "end": iso(now - timedelta(seconds=1)),
        }
        request["spec"]["deadline"] = iso(now + timedelta(minutes=1))
        return request

    def test_authenticated_http_collection_uses_no_data_default(self) -> None:
        handler = object.__new__(ApiHandler)
        handler.runtime = self.runtime
        handler.path = "/v1/evidence/kubernetes/events/queries"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler._read_json = self.live_request
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_POST()

        status, evidence = responses[0]
        self.assertEqual(status, HTTPStatus.CREATED)
        artifact = self.runtime.evidence_store.read_artifact(
            self.actor, evidence["metadata"]["id"]
        )
        result = json.loads(artifact)
        self.assertEqual(result["kind"], "KubernetesEventEvidenceResult")
        self.assertEqual(result["spec"]["status"], "no-data")

    def test_python_sdk_posts_public_event_request(self) -> None:
        payload = example("evidence.json")

        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode()

        request = KubernetesEventEvidenceRequest.from_dict(
            example("kubernetes-event-evidence-request.json")
        )
        client = Client("https://control.example", TOKEN)
        with patch(
            "infra_intelligence_sdk.client.urlopen", return_value=Response()
        ) as send:
            evidence = client.collect_kubernetes_event_evidence(request)

        self.assertIsInstance(evidence, Evidence)
        outgoing = send.call_args.args[0]
        self.assertEqual(
            outgoing.full_url,
            "https://control.example/v1/evidence/kubernetes/events/queries",
        )
        self.assertEqual(json.loads(outgoing.data), request.to_dict())


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
