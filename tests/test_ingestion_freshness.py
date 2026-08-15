from __future__ import annotations

import copy
import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from iip.adapters.auth import BearerIdentity, HashedBearerAuthenticator
from iip.application.ingest_collection import IngestCollectionCommand
from iip.application.observe_ingestion import (
    GetIngestionFreshnessCommand,
    IngestionFreshnessObjectives,
    IngestionFreshnessService,
    IngestionSourceNotFoundError,
    IngestionTelemetryAuthorizationError,
    IngestionTelemetryInputError,
    IngestionTelemetryStateError,
)
from iip.application.ports import ActorContext, PolicyDecision, SourceIngestionState
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import Client, IngestionFreshnessReport


ROOT = Path(__file__).resolve().parents[1]
LOCAL_TOKEN = "telemetry-reference-token-00000000000001"
OTHER_TOKEN = "telemetry-other-token-0000000000000001"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


class FixedClock:
    def __init__(self, value: str) -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class RecordingPolicy:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[ActorContext, str, dict[str, object]]] = []

    def decide(
        self, actor: ActorContext, action: str, resource: dict[str, object]
    ) -> PolicyDecision:
        self.calls.append((actor, action, resource))
        return PolicyDecision(self.allowed, "test.allow" if self.allowed else "test.deny")


class StateRepository:
    def __init__(self, state: SourceIngestionState | None) -> None:
        self.state = state
        self.calls: list[tuple[str, str]] = []

    def get_source_ingestion_state(
        self, tenant_id: str, source_id: str
    ) -> SourceIngestionState | None:
        self.calls.append((tenant_id, source_id))
        return self.state


def source_state(**changes: object) -> SourceIngestionState:
    values: dict[str, object] = {
        "tenant_id": "local",
        "source_id": "kubernetes-local",
        "stream_id": "obs_0f4e8c2a6b1d4975a3c9e7f102d468ab",
        "checkpoint_sequence": 42,
        "checkpoint_committed_at": "2026-08-14T10:30:05Z",
        "latest_observed_at": "2026-08-14T10:30:00Z",
        "latest_recorded_at": "2026-08-14T10:30:02Z",
        "accepted_observation_count": 1,
        "pending_event_count": 0,
        "oldest_pending_event_recorded_at": None,
    }
    values.update(changes)
    return SourceIngestionState(**values)


class IngestionFreshnessServiceTests(unittest.TestCase):
    def service(
        self,
        state: SourceIngestionState | None,
        *,
        policy: RecordingPolicy | None = None,
        evaluated_at: str = "2026-08-14T10:30:10Z",
    ) -> tuple[IngestionFreshnessService, StateRepository, RecordingPolicy]:
        repository = StateRepository(state)
        selected_policy = policy or RecordingPolicy()
        objectives = IngestionFreshnessObjectives(
            maximum_checkpoint_age_seconds=60,
            maximum_observation_age_seconds=60,
            maximum_ingestion_delay_seconds=10,
            maximum_pending_event_age_seconds=30,
            maximum_clock_skew_seconds=5,
        )
        return (
            IngestionFreshnessService(
                repository,
                selected_policy,
                FixedClock(evaluated_at),
                objectives,
            ),
            repository,
            selected_policy,
        )

    def test_healthy_source_matches_the_public_contract_example(self) -> None:
        service, repository, policy = self.service(source_state())

        report = service.get(
            GetIngestionFreshnessCommand(
                ActorContext("operator", "local"), "kubernetes-local"
            )
        ).to_dict()

        self.assertEqual(report, example("ingestion-freshness-report.json"))
        self.assertEqual(repository.calls, [("local", "kubernetes-local")])
        self.assertEqual(policy.calls[0][1], "ingestion-telemetry:read")
        self.assertEqual(
            policy.calls[0][2],
            {"tenantId": "local", "sourceId": "kubernetes-local"},
        )

    def test_every_age_objective_has_a_stable_violation(self) -> None:
        state = source_state(
            checkpoint_committed_at="2026-08-14T10:00:00Z",
            latest_observed_at="2026-08-14T10:00:00Z",
            latest_recorded_at="2026-08-14T10:00:20Z",
            pending_event_count=2,
            oldest_pending_event_recorded_at="2026-08-14T10:00:30Z",
        )
        service, _, _ = self.service(state)

        report = service.get(
            GetIngestionFreshnessCommand(
                ActorContext("operator", "local"), "kubernetes-local"
            )
        ).to_dict()

        self.assertEqual(report["spec"]["status"], "breached")
        self.assertEqual(
            report["spec"]["violations"],
            [
                "checkpoint-age-exceeded",
                "ingestion-delay-exceeded",
                "observation-age-exceeded",
                "pending-event-age-exceeded",
            ],
        )

    def test_clock_skew_is_clamped_and_cannot_look_healthy(self) -> None:
        state = source_state(
            checkpoint_committed_at="2026-08-14T10:31:00Z",
            latest_observed_at="2026-08-14T10:31:00Z",
            latest_recorded_at="2026-08-14T10:31:00Z",
        )
        service, _, _ = self.service(state)

        report = service.get(
            GetIngestionFreshnessCommand(
                ActorContext("operator", "local"), "kubernetes-local"
            )
        ).to_dict()

        self.assertEqual(report["spec"]["checkpoint"]["ageSeconds"], 0)
        self.assertEqual(report["spec"]["latestObservation"]["ageSeconds"], 0)
        self.assertEqual(report["spec"]["violations"], ["clock-skew-detected"])

    def test_empty_complete_source_remains_measurable(self) -> None:
        state = source_state(
            latest_observed_at=None,
            latest_recorded_at=None,
            accepted_observation_count=0,
        )
        service, _, _ = self.service(state)

        report = service.get(
            GetIngestionFreshnessCommand(
                ActorContext("operator", "local"), "kubernetes-local"
            )
        ).to_dict()

        self.assertNotIn("latestObservation", report["spec"])
        self.assertEqual(report["spec"]["acceptedObservationCount"], 0)
        self.assertEqual(report["spec"]["status"], "within-objective")

    def test_validation_and_policy_run_before_tenant_scoped_storage(self) -> None:
        denied = RecordingPolicy(False)
        service, repository, _ = self.service(source_state(), policy=denied)

        with self.assertRaises(IngestionTelemetryInputError):
            service.get(
                GetIngestionFreshnessCommand(
                    ActorContext("operator", "local"), "invalid source"
                )
            )
        self.assertEqual(denied.calls, [])
        with self.assertRaises(IngestionTelemetryAuthorizationError):
            service.get(
                GetIngestionFreshnessCommand(
                    ActorContext("operator", "local"), "kubernetes-local"
                )
            )
        self.assertEqual(repository.calls, [])

    def test_missing_or_inconsistent_adapter_state_fails_closed(self) -> None:
        missing, _, _ = self.service(None)
        inconsistent, _, _ = self.service(
            source_state(tenant_id="another-tenant")
        )
        command = GetIngestionFreshnessCommand(
            ActorContext("operator", "local"), "kubernetes-local"
        )

        with self.assertRaises(IngestionSourceNotFoundError):
            missing.get(command)
        with self.assertRaises(IngestionTelemetryStateError):
            inconsistent.get(command)

    def test_objectives_reject_nonpositive_boolean_and_unbounded_values(self) -> None:
        for value in (0, -1, True, 315_360_001):
            with self.subTest(value=value):
                with self.assertRaises(IngestionTelemetryInputError):
                    IngestionFreshnessObjectives(
                        maximum_checkpoint_age_seconds=value
                    ).validate()


class IngestionFreshnessSchemaTests(unittest.TestCase):
    def test_status_and_delivery_conditions_are_enforced(self) -> None:
        schema = example_schema("ingestion-freshness-report.schema.json")
        healthy = example("ingestion-freshness-report.json")
        invalid_status = copy.deepcopy(healthy)
        invalid_status["spec"]["violations"] = ["checkpoint-age-exceeded"]
        invalid_delivery = copy.deepcopy(healthy)
        invalid_delivery["spec"]["delivery"][
            "oldestPendingEventAgeSeconds"
        ] = 1

        for payload in (invalid_status, invalid_delivery):
            with self.subTest(payload=payload):
                self.assertTrue(
                    validate_schemas.instance_validation_errors(
                        schema,
                        payload,
                        label="invalid ingestion freshness report",
                    )
                )


class IngestionFreshnessHttpAndSdkTests(unittest.TestCase):
    def setUp(self) -> None:
        authenticator = HashedBearerAuthenticator(
            (
                BearerIdentity(
                    HashedBearerAuthenticator.token_sha256(LOCAL_TOKEN),
                    ActorContext("operator", "local", ("operator",)),
                ),
                BearerIdentity(
                    HashedBearerAuthenticator.token_sha256(OTHER_TOKEN),
                    ActorContext("other", "another-tenant", ("operator",)),
                ),
            )
        )
        self.runtime = build_local_runtime(authenticator)
        request = example("resource-collection-request.json")
        result = example("resource-collection-result.json")
        actor = ActorContext(
            request["metadata"]["actorId"], request["metadata"]["tenantId"]
        )
        self.runtime.collection_ingestion.execute(
            IngestCollectionCommand(actor, request, result)
        )
        self.handler = object.__new__(ApiHandler)
        self.handler.runtime = self.runtime
        self.handler.headers = {"authorization": f"Bearer {LOCAL_TOKEN}"}
        self.responses: list[tuple[HTTPStatus, dict]] = []
        self.handler._json = lambda status, payload: self.responses.append((status, payload))

    def request(self, path: str, *, token: str = LOCAL_TOKEN) -> tuple[HTTPStatus, dict]:
        self.responses.clear()
        self.handler.path = path
        self.handler.headers = {"authorization": f"Bearer {token}"}
        self.handler.do_GET()
        self.assertEqual(len(self.responses), 1)
        return self.responses[0]

    def test_http_report_is_authenticated_tenant_scoped_and_schema_valid(self) -> None:
        status, report = self.request(
            "/v1/telemetry/ingestion?sourceId=kubernetes-local"
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(report["kind"], "IngestionFreshnessReport")
        self.assertEqual(report["metadata"]["tenantId"], "local")
        schema = example_schema("ingestion-freshness-report.schema.json")
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, report, label="HTTP ingestion freshness response"
            ),
            [],
        )

        other_status, other = self.request(
            "/v1/telemetry/ingestion?sourceId=kubernetes-local",
            token=OTHER_TOKEN,
        )
        self.assertEqual(other_status, HTTPStatus.NOT_FOUND)
        self.assertEqual(other["error"]["code"], "ingestion.source_not_found")

    def test_http_rejects_missing_duplicate_and_unknown_query_fields(self) -> None:
        for query in (
            "",
            "sourceId=",
            "sourceId=kubernetes-local&sourceId=another-source",
            "sourceId=kubernetes-local&tenantId=another-tenant",
        ):
            with self.subTest(query=query):
                status, body = self.request(f"/v1/telemetry/ingestion?{query}")
                self.assertEqual(status, HTTPStatus.BAD_REQUEST)
                self.assertEqual(body["error"]["code"], "request.invalid")

    def test_python_sdk_builds_source_query_and_parses_report(self) -> None:
        payload = example("ingestion-freshness-report.json")

        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode()

        client = Client("https://control.example", LOCAL_TOKEN)
        with patch(
            "infra_intelligence_sdk.client.urlopen", return_value=Response()
        ) as send:
            report = client.get_ingestion_freshness("kubernetes-local")

        self.assertIsInstance(report, IngestionFreshnessReport)
        self.assertEqual(report.to_dict(), payload)
        request = send.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://control.example/v1/telemetry/ingestion?sourceId=kubernetes-local",
        )
        self.assertEqual(request.get_header("Authorization"), f"Bearer {LOCAL_TOKEN}")


def example_schema(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "schemas" / name).read_text())


if __name__ == "__main__":
    unittest.main()
