from __future__ import annotations

import copy
import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
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
from iip.application.investigate import (
    DeterministicInvestigationService,
    InvestigationInProgressError,
    RunInvestigationCommand,
)
from iip.application.investigation_lifecycle import (
    CancelInvestigationCommand,
    GetInvestigationStatusCommand,
    InvalidInvestigationCancellationError,
    InvestigationLifecycleService,
)
from iip.application.ports import ActorContext
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import (
    Client,
    InvestigationCancellationRequest,
    InvestigationStatus,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)

    def now(self) -> str:
        return iso(self.value)

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class InvestigationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("developer", "local")
        self.clock = MutableClock()
        self.resources = InMemoryResourceStore()
        resource = ResourceIngestionService(
            self.resources, AllowTenantPolicy()
        ).execute(IngestResourceCommand(self.actor, example("resource.json")))
        self.operations = InMemoryOperationalStore()
        self.lifecycle = InvestigationLifecycleService(self.operations, self.clock)
        self.provider = ResourceStateEvidenceProvider(self.resources)
        self.provider_calls = 0

        class CancellingProvider:
            def fetch(inner_self, request):
                self.provider_calls += 1
                cancellation = copy.deepcopy(
                    example("investigation-cancellation-request.json")
                )
                cancellation["metadata"]["requestedAt"] = self.clock.now()
                cancellation["spec"]["investigationId"] = request.locator.split(
                    "#", 1
                )[1]
                self.lifecycle.cancel(
                    CancelInvestigationCommand(self.actor, cancellation)
                )
                return self.provider.fetch(replace(request, locator="resource://current"))

        evidence_store = InMemoryEvidenceStore()
        evidence = EvidenceCollectionService(
            self.resources,
            {"resource-state": CancellingProvider()},
            evidence_store,
            StructuredTextRedactor(),
            AllowTenantPolicy(),
            UuidEvidenceIdGenerator(),
            self.clock,
        )
        self.service = DeterministicInvestigationService(
            self.resources,
            evidence,
            self.operations,
            self.clock,
            evidence_store=evidence_store,
        )
        self.resource_uid = resource.identity.uid

    def request(self, marker: str) -> dict:
        request = copy.deepcopy(example("investigation-request.json"))
        request["metadata"]["id"] = "inv_" + marker * 32
        request["metadata"]["requestedAt"] = self.clock.now()
        request["spec"]["scope"]["resourceUids"] = [self.resource_uid]
        request["spec"]["scope"]["timeRange"] = {
            "start": iso(self.clock.value - timedelta(minutes=30)),
            "end": self.clock.now(),
        }
        request["spec"]["evidenceTypes"] = ["kubernetes.resource-status"]
        request["spec"]["budgets"]["maxWallTimeSeconds"] = 10
        return request

    def test_cooperative_cancellation_commits_terminal_report_and_status(self) -> None:
        request = self.request("a")
        request["spec"]["trigger"]["reference"] = (
            f"urn:iip:test#{request['metadata']['id']}"
        )
        # The test provider uses its credential-free locator to learn only the
        # already scoped investigation ID; production providers never receive it.
        original_execute = self.service._evidence.execute

        def execute_with_marker(command):
            command = type(command)(
                actor=command.actor,
                provider=command.provider,
                integration_id=command.integration_id,
                evidence_type=command.evidence_type,
                resource_uids=command.resource_uids,
                locator=f"resource://current#{request['metadata']['id']}",
                deadline=command.deadline,
                max_bytes=command.max_bytes,
            )
            return original_execute(command)

        self.service._evidence.execute = execute_with_marker
        report = self.service.execute(RunInvestigationCommand(self.actor, request))

        self.assertEqual(report["spec"]["outcome"], "cancelled")
        self.assertEqual(report["spec"]["terminalReason"], "cancelled")
        self.assertEqual(report["spec"]["hypotheses"], [])
        self.assertEqual(report["spec"]["usage"]["evidenceItems"], 1)
        status = self.lifecycle.get(
            GetInvestigationStatusCommand(self.actor, request["metadata"]["id"])
        )
        self.assertEqual(status["spec"]["state"], "cancelled")
        self.assertNotIn("leaseExpiresAt", status["spec"])
        self.assertEqual(
            self.service.execute(RunInvestigationCommand(self.actor, request)),
            report,
        )
        schema = json.loads(
            (ROOT / "contracts/schemas/investigation-report.schema.json").read_text()
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, report, label="cancelled investigation"
            ),
            [],
        )

    def test_live_lease_rejects_duplicate_and_expired_lease_recovers(self) -> None:
        active = self.request("b")
        normalized, _, _, scope, budgets = self.service._validate(
            RunInvestigationCommand(self.actor, active)
        )
        active_status = self.service._running_status(
            self.actor,
            active["metadata"]["id"],
            normalized,
            self.clock.now(),
            budgets["maxWallTimeSeconds"],
        )
        self.operations.start_investigation(
            self.actor, active["metadata"]["id"], normalized, active_status
        )
        with self.assertRaisesRegex(
            InvestigationInProgressError, "investigation.in_progress"
        ):
            self.service.execute(RunInvestigationCommand(self.actor, active))

        self.clock.advance(11)
        report = self.service.execute(RunInvestigationCommand(self.actor, active))
        self.assertEqual(report["spec"]["outcome"], "failed")
        self.assertEqual(report["spec"]["terminalReason"], "runtime-error")
        self.assertEqual(self.provider_calls, 0)
        status = self.lifecycle.get(
            GetInvestigationStatusCommand(self.actor, active["metadata"]["id"])
        )
        self.assertEqual(status["spec"]["state"], "failed")

    def test_cancellation_identity_and_first_request_are_immutable(self) -> None:
        request = self.request("c")
        normalized, _, _, _, budgets = self.service._validate(
            RunInvestigationCommand(self.actor, request)
        )
        status = self.service._running_status(
            self.actor,
            request["metadata"]["id"],
            normalized,
            self.clock.now(),
            budgets["maxWallTimeSeconds"],
        )
        self.operations.start_investigation(
            self.actor, request["metadata"]["id"], normalized, status
        )
        cancellation = copy.deepcopy(example("investigation-cancellation-request.json"))
        cancellation["metadata"]["requestedAt"] = self.clock.now()
        cancellation["spec"]["investigationId"] = request["metadata"]["id"]
        first = self.lifecycle.cancel(
            CancelInvestigationCommand(self.actor, cancellation)
        )
        cancellation["spec"]["reasonCode"] = "superseded"
        second = self.lifecycle.cancel(
            CancelInvestigationCommand(self.actor, cancellation)
        )
        self.assertEqual(first, second)
        self.assertEqual(
            first["spec"]["cancellation"]["reasonCode"], "operator-requested"
        )

        spoofed = copy.deepcopy(cancellation)
        spoofed["metadata"]["actorId"] = "another-actor"
        with self.assertRaisesRegex(
            InvalidInvestigationCancellationError,
            "investigation.cancellation.invalid",
        ):
            self.lifecycle.cancel(CancelInvestigationCommand(self.actor, spoofed))

    def test_cancellation_wins_a_race_with_terminal_commit(self) -> None:
        evidence_store = InMemoryEvidenceStore()
        evidence = EvidenceCollectionService(
            self.resources,
            {"resource-state": self.provider},
            evidence_store,
            StructuredTextRedactor(),
            AllowTenantPolicy(),
            UuidEvidenceIdGenerator(),
            self.clock,
        )
        service = DeterministicInvestigationService(
            self.resources,
            evidence,
            self.operations,
            self.clock,
            evidence_store=evidence_store,
        )
        request = self.request("d")
        original_commit = self.operations.commit_investigation
        injected = False

        def commit_with_racing_cancellation(actor, investigation_id, accepted, report, status):
            nonlocal injected
            if not injected:
                injected = True
                cancellation = copy.deepcopy(
                    example("investigation-cancellation-request.json")
                )
                cancellation["metadata"]["requestedAt"] = self.clock.now()
                cancellation["spec"]["investigationId"] = investigation_id
                self.lifecycle.cancel(
                    CancelInvestigationCommand(self.actor, cancellation)
                )
            return original_commit(actor, investigation_id, accepted, report, status)

        self.operations.commit_investigation = commit_with_racing_cancellation
        report = service.execute(RunInvestigationCommand(self.actor, request))

        self.assertEqual(report["spec"]["outcome"], "cancelled")
        self.assertEqual(report["spec"]["hypotheses"], [])
        self.assertEqual(
            self.lifecycle.get(
                GetInvestigationStatusCommand(self.actor, request["metadata"]["id"])
            )["spec"]["state"],
            "cancelled",
        )


class InvestigationLifecycleHttpTests(unittest.TestCase):
    def test_authenticated_cancel_and_status_endpoints(self) -> None:
        token = "investigation-lifecycle-token-0123456789abcdef"
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(
                                token
                            ),
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
        request = copy.deepcopy(example("investigation-request.json"))
        request["spec"]["scope"]["resourceUids"] = [resource.identity.uid]
        normalized, metadata, _, _, budgets = runtime.investigations._validate(
            RunInvestigationCommand(actor, request)
        )
        started_at = request["metadata"]["requestedAt"]
        status = runtime.investigations._running_status(
            actor,
            metadata["id"],
            normalized,
            started_at,
            budgets["maxWallTimeSeconds"],
        )
        runtime.operational_store.start_investigation(
            actor, metadata["id"], normalized, status
        )
        cancellation = copy.deepcopy(example("investigation-cancellation-request.json"))

        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = f"/v1/investigations/{metadata['id']}/cancel"
        handler.headers = {"authorization": f"Bearer {token}"}
        handler._read_json = lambda: cancellation
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda code, payload, **_: responses.append((code, payload))
        handler.do_POST()

        self.assertEqual(responses[0][0], HTTPStatus.ACCEPTED)
        self.assertEqual(responses[0][1]["spec"]["state"], "cancellation-requested")
        responses.clear()
        handler.path = f"/v1/investigations/{metadata['id']}/status"
        handler.do_GET()
        self.assertEqual(responses[0][0], HTTPStatus.OK)
        self.assertEqual(responses[0][1]["spec"]["state"], "cancellation-requested")

    def test_python_sdk_uses_status_and_cancel_paths(self) -> None:
        cancellation = InvestigationCancellationRequest.from_dict(
            example("investigation-cancellation-request.json")
        )
        status_payload = example("investigation-status.json")
        investigation_id = cancellation.to_dict()["spec"]["investigationId"]
        client = Client("https://platform.example", "valid-token")
        client._get = lambda path: (
            status_payload
            if path == f"/v1/investigations/{investigation_id}/status"
            else self.fail(path)
        )
        posted: list[tuple[str, dict]] = []
        client._post = lambda path, payload, *args: (
            posted.append((path, dict(payload))) or status_payload
        )

        status = client.get_investigation_status(investigation_id)
        cancelled = client.cancel_investigation(cancellation)

        self.assertIsInstance(status, InvestigationStatus)
        self.assertEqual(cancelled.state, "cancellation-requested")
        self.assertEqual(
            posted[0][0], f"/v1/investigations/{investigation_id}/cancel"
        )


if __name__ == "__main__":
    unittest.main()
