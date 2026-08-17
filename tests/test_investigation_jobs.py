from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iip.adapters.evidence import (
    InMemoryEvidenceStore,
    ResourceStateEvidenceProvider,
    StructuredTextRedactor,
    UuidEvidenceIdGenerator,
)
from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
from iip.application.collect_evidence import EvidenceCollectionService
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.investigate import DeterministicInvestigationService
from iip.application.investigation_dispatch import (
    CancelInvestigationJobCommand,
    GetInvestigationJobCommand,
    InvestigationDispatchService,
    InvestigationJobNotFoundError,
    SubmitInvestigationJobCommand,
)
from iip.application.investigation_lifecycle import InvestigationLifecycleService
from iip.application.investigation_worker import InvestigationWorker
from iip.application.ports import ActorContext
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import (
    Client,
    InvestigationCancellationRequest,
    InvestigationJobStatus,
    InvestigationRequest,
)
from http import HTTPStatus


ROOT = Path(__file__).resolve().parents[1]


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)

    def now(self) -> str:
        return iso(self.value)

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class InvestigationJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.actor = ActorContext("developer", "local", ("developer",))
        self.other = ActorContext("other", "another-tenant", ("developer",))
        self.resources = InMemoryResourceStore()
        resource = ResourceIngestionService(
            self.resources, AllowTenantPolicy()
        ).execute(IngestResourceCommand(self.actor, example("resource.json")))
        self.store = InMemoryOperationalStore()
        evidence_store = InMemoryEvidenceStore()
        evidence = EvidenceCollectionService(
            self.resources,
            {"resource-state": ResourceStateEvidenceProvider(self.resources)},
            evidence_store,
            StructuredTextRedactor(),
            AllowTenantPolicy(),
            UuidEvidenceIdGenerator(),
            self.clock,
        )
        self.investigations = DeterministicInvestigationService(
            self.resources,
            evidence,
            self.store,
            self.clock,
            evidence_store=evidence_store,
        )
        lifecycle = InvestigationLifecycleService(self.store, self.clock)
        self.dispatch = InvestigationDispatchService(
            self.store, self.investigations, lifecycle, self.clock
        )
        self.request = example("investigation-request.json")
        self.request["metadata"]["actorId"] = self.actor.actor_id
        self.request["metadata"]["requestedAt"] = self.clock.now()
        self.request["spec"]["scope"]["resourceUids"] = [resource.identity.uid]
        self.request["spec"]["scope"]["timeRange"] = {
            "start": iso(self.clock.value - timedelta(minutes=30)),
            "end": self.clock.now(),
        }

    def worker(self, worker_id: str) -> InvestigationWorker:
        return InvestigationWorker(
            self.store,
            self.investigations,
            self.clock,
            worker_id=worker_id,
            lease_seconds=10,
            heartbeat_seconds=0,
            retry_seconds=1,
        )

    def test_submit_worker_and_terminal_report_are_durable(self) -> None:
        queued = self.dispatch.submit(
            SubmitInvestigationJobCommand(self.actor, self.request)
        )
        self.assertEqual(queued["spec"]["state"], "queued")
        self.assertEqual(queued["spec"]["attempts"], 0)
        self.assertIsNone(
            self.store.get_investigation_job(
                self.other, self.request["metadata"]["id"]
            )
        )

        result = self.worker("worker-a").run_once("local")

        self.assertIsNotNone(result)
        self.assertEqual(result.disposition, "completed")
        status = self.dispatch.get(
            GetInvestigationJobCommand(self.actor, self.request["metadata"]["id"])
        )
        self.assertEqual(status["spec"]["state"], "completed")
        self.assertEqual(status["spec"]["attempts"], 1)
        self.assertIn("reportRef", status["spec"])
        report = self.store.get_investigation(
            self.actor, self.request["metadata"]["id"]
        )
        self.assertIsNotNone(report)

    def test_same_request_is_idempotent_and_different_request_conflicts(self) -> None:
        first = self.dispatch.submit(
            SubmitInvestigationJobCommand(self.actor, self.request)
        )
        second = self.dispatch.submit(
            SubmitInvestigationJobCommand(self.actor, copy.deepcopy(self.request))
        )
        self.assertEqual(first, second)

        changed = copy.deepcopy(self.request)
        changed["spec"]["question"] = "A different immutable question"
        with self.assertRaisesRegex(Exception, "investigation.id.conflict"):
            self.dispatch.submit(SubmitInvestigationJobCommand(self.actor, changed))

    def test_queued_cancellation_prevents_execution(self) -> None:
        self.dispatch.submit(SubmitInvestigationJobCommand(self.actor, self.request))
        cancellation = example("investigation-cancellation-request.json")
        cancellation["metadata"]["actorId"] = self.actor.actor_id
        cancellation["metadata"]["requestedAt"] = self.clock.now()
        cancellation["spec"]["investigationId"] = self.request["metadata"]["id"]

        cancelled = self.dispatch.cancel(
            CancelInvestigationJobCommand(self.actor, cancellation)
        )

        changed = copy.deepcopy(cancellation)
        changed["spec"]["reasonCode"] = "superseded"
        replay = self.dispatch.cancel(
            CancelInvestigationJobCommand(self.actor, changed)
        )

        self.assertEqual(cancelled["spec"]["state"], "cancelled")
        self.assertEqual(replay, cancelled)
        self.assertIsNone(self.worker("worker-a").run_once("local"))
        self.assertIsNone(
            self.store.get_investigation(
                self.actor, self.request["metadata"]["id"]
            )
        )

    def test_expired_dispatch_claim_is_recovered_without_cross_tenant_claim(self) -> None:
        self.dispatch.submit(SubmitInvestigationJobCommand(self.actor, self.request))
        claim = self.store.claim_investigation_job(
            "local", "crashed-worker", self.clock.now(), iso(self.clock.value + timedelta(seconds=10))
        )
        self.assertIsNotNone(claim)
        self.assertIsNone(self.worker("other-worker").run_once("another-tenant"))

        self.clock.advance(11)
        recovered = self.worker("recovery-worker").run_once("local")

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.disposition, "completed")
        status = self.dispatch.get(
            GetInvestigationJobCommand(self.actor, self.request["metadata"]["id"])
        )
        self.assertEqual(status["spec"]["attempts"], 2)

    def test_cancellation_cannot_be_overwritten_by_a_stale_heartbeat(self) -> None:
        self.dispatch.submit(SubmitInvestigationJobCommand(self.actor, self.request))
        claim = self.store.claim_investigation_job(
            "local",
            "worker-a",
            self.clock.now(),
            iso(self.clock.value + timedelta(seconds=10)),
        )
        assert claim is not None
        stale_running = self.store.get_investigation_job(
            self.actor, self.request["metadata"]["id"]
        )
        cancellation = example("investigation-cancellation-request.json")
        cancellation["metadata"]["actorId"] = self.actor.actor_id
        cancellation["metadata"]["requestedAt"] = self.clock.now()
        cancellation["spec"]["investigationId"] = self.request["metadata"]["id"]
        self.dispatch.cancel(CancelInvestigationJobCommand(self.actor, cancellation))

        self.assertFalse(
            self.store.heartbeat_investigation_job(
                "local",
                self.request["metadata"]["id"],
                "worker-a",
                claim.claim_token,
                stale_running,
            )
        )
        current = self.store.get_investigation_job(
            self.actor, self.request["metadata"]["id"]
        )
        self.assertEqual(current["spec"]["state"], "cancellation-requested")

        self.clock.advance(11)
        result = self.worker("recovery-worker").run_once("local")
        self.assertEqual(result.disposition, "cancelled")

    def test_unknown_job_is_tenant_safe(self) -> None:
        with self.assertRaises(InvestigationJobNotFoundError):
            self.dispatch.get(
                GetInvestigationJobCommand(
                    self.actor, "inv_ffffffffffffffffffffffffffffffff"
                )
            )

    def test_successful_terminal_status_clears_a_transient_retry_error(self) -> None:
        retrying = InvestigationDispatchService.queued_status(
            self.actor,
            self.request["metadata"]["id"],
            self.request,
            self.clock.now(),
            attempts=1,
            error_code="investigation.runtime.unavailable",
        )

        completed = InvestigationDispatchService.terminal_status(
            retrying,
            state="completed",
            completed_at=self.clock.now(),
            report_ref=self.request["metadata"]["id"],
        )

        self.assertNotIn("lastErrorCode", completed["spec"])


class InvestigationJobHttpAndSdkTests(unittest.TestCase):
    def test_authenticated_submit_get_and_cancel_queued_job(self) -> None:
        token = "investigation-job-http-token-0123456789abcdef"
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(token),
                            "actorId": "developer",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        runtime = build_local_runtime(authenticator)
        actor = ActorContext("developer", "local", ("developer",))
        resource = runtime.ingestion.execute(
            IngestResourceCommand(actor, example("resource.json"))
        )
        request = example("investigation-request.json")
        request["spec"]["scope"]["resourceUids"] = [resource.identity.uid]

        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.headers = {"authorization": f"Bearer {token}"}
        handler.path = "/v1/investigation-jobs"
        handler._read_json = lambda: request
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_POST()
        self.assertEqual(responses[0][0], HTTPStatus.ACCEPTED)
        self.assertEqual(responses[0][1]["spec"]["state"], "queued")

        responses.clear()
        investigation_id = request["metadata"]["id"]
        handler.path = f"/v1/investigation-jobs/{investigation_id}"
        handler.do_GET()
        self.assertEqual(responses[0][1]["spec"]["state"], "queued")

        cancellation = example("investigation-cancellation-request.json")
        handler.path = f"/v1/investigation-jobs/{investigation_id}/cancel"
        handler._read_json = lambda: cancellation
        responses.clear()
        handler.do_POST()
        self.assertEqual(responses[0][0], HTTPStatus.ACCEPTED)
        self.assertEqual(responses[0][1]["spec"]["state"], "cancelled")

    def test_python_sdk_uses_job_paths_and_types(self) -> None:
        request = InvestigationRequest.from_dict(example("investigation-request.json"))
        cancellation = InvestigationCancellationRequest.from_dict(
            example("investigation-cancellation-request.json")
        )
        queued = InvestigationDispatchService.queued_status(
            ActorContext("developer", "local"),
            request.to_dict()["metadata"]["id"],
            request.to_dict(),
            "2026-08-17T12:00:00Z",
            attempts=0,
        )
        client = Client("https://api.example", "token")
        calls: list[tuple[str, object | None]] = []
        client._post = lambda path, body, correlation_id=None: (
            calls.append((path, body)) or queued
        )
        client._get = lambda path: calls.append((path, None)) or queued

        submitted = client.submit_investigation(request)
        loaded = client.get_investigation_job(request.to_dict()["metadata"]["id"])
        cancelled = client.cancel_investigation_job(cancellation)

        self.assertIsInstance(submitted, InvestigationJobStatus)
        self.assertEqual(loaded.state, "queued")
        self.assertEqual(cancelled.attempts, 0)
        self.assertEqual(calls[0][0], "/v1/investigation-jobs")
        self.assertTrue(calls[1][0].startswith("/v1/investigation-jobs/inv_"))
        self.assertTrue(calls[2][0].endswith("/cancel"))


if __name__ == "__main__":
    unittest.main()
