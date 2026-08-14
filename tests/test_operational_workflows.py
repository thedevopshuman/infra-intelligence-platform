from __future__ import annotations

import copy
import json
import sys
import unittest
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path

from iip.adapters.actions import KubernetesRestartDryRunExecutor
from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.evidence import (
    InMemoryEvidenceStore,
    ResourceStateEvidenceProvider,
    StructuredTextRedactor,
    SystemClock,
    UuidEvidenceIdGenerator,
)
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
from iip.application.actions import (
    ActionWorkflowError,
    DecideActionCommand,
    ExecuteActionCommand,
    GovernedActionService,
    ProposeActionCommand,
)
from iip.application.collect_evidence import EvidenceCollectionService
from iip.application.evaluate import run_repeated, score_report
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.investigate import (
    DeterministicInvestigationService,
    RunInvestigationCommand,
)
from iip.application.plugin_sessions import (
    OpenPluginSessionCommand,
    PluginHandshakeError,
    PluginSessionService,
)
from iip.application.ports import ActorContext, PersistenceError
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def assert_schema(test: unittest.TestCase, name: str, document: dict) -> None:
    schema = ROOT / "contracts" / "schemas" / name
    errors: list[str] = []
    loaded = validate_schemas.load_json(schema, errors)
    test.assertEqual(errors, [])
    test.assertEqual(
        validate_schemas.instance_validation_errors(loaded, document, label=name), []
    )


class InvestigationAndEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = example("evaluation-scenario.json")
        self.actor = ActorContext("evaluation-runner", "evaluation")
        self.resources = InMemoryResourceStore()
        policy = AllowTenantPolicy()
        ingestion = ResourceIngestionService(self.resources, policy)
        for resource in self.scenario["spec"]["fixtures"]["graph"]["resources"]:
            ingestion.execute(IngestResourceCommand(self.actor, resource))
        evidence_store = InMemoryEvidenceStore()
        clock = SystemClock()
        evidence = EvidenceCollectionService(
            self.resources,
            {"resource-state": ResourceStateEvidenceProvider(self.resources)},
            evidence_store,
            StructuredTextRedactor(),
            policy,
            UuidEvidenceIdGenerator(),
            clock,
        )
        self.operations = InMemoryOperationalStore()
        self.service = DeterministicInvestigationService(
            self.resources, evidence, self.operations, clock
        )

    def test_investigation_is_evidence_backed_bounded_and_idempotent(self) -> None:
        request = self.scenario["spec"]["request"]

        report = self.service.execute(RunInvestigationCommand(self.actor, request))
        replay = self.service.execute(RunInvestigationCommand(self.actor, request))

        self.assertEqual(report, replay)
        self.assertEqual(report["spec"]["outcome"], "conclusive")
        self.assertEqual(
            report["spec"]["hypotheses"][0]["rootCauseClass"],
            "kubernetes.image-pull.manifest-not-found",
        )
        self.assertEqual(report["spec"]["usage"]["modelTokens"], 0)
        self.assertEqual(report["spec"]["usage"]["costUsd"], 0)
        self.assertEqual(len(report["spec"]["evidenceIds"]), 1)
        assert_schema(self, "investigation-report.schema.json", report)

    def test_zero_tool_budget_finishes_with_explicit_unknown(self) -> None:
        request = copy.deepcopy(self.scenario["spec"]["request"])
        request["metadata"]["id"] = "inv_dddddddddddddddddddddddddddddddd"
        request["spec"]["budgets"]["maxToolCalls"] = 0

        report = self.service.execute(RunInvestigationCommand(self.actor, request))

        self.assertEqual(report["spec"]["outcome"], "inconclusive")
        self.assertEqual(report["spec"]["terminalReason"], "budget-exhausted")
        self.assertTrue(report["spec"]["unknowns"])
        assert_schema(self, "investigation-report.schema.json", report)

    def test_evaluation_hard_gates_and_repeated_runs_are_deterministic(self) -> None:
        report = copy.deepcopy(example("investigation-report.json"))
        expectations = self.scenario["spec"]["expectations"]
        required = expectations["requiredEvidenceIds"]
        report["spec"]["hypotheses"][0]["rootCauseClass"] = expectations[
            "rootCauseClass"
        ]
        report["spec"]["hypotheses"][0]["supportingEvidenceIds"] = required
        report["spec"]["evidenceIds"] = required
        report["spec"]["recommendations"][0]["evidenceIds"] = required
        report["spec"]["usage"] = {
            "toolCalls": 2,
            "iterations": 1,
            "modelTokens": 0,
            "wallTimeSeconds": 1,
            "costUsd": 0,
            "evidenceItems": 2,
        }
        evidence = {
            item["metadata"]["id"]: item
            for item in self.scenario["spec"]["fixtures"]["evidence"]
        }

        score = score_report(self.scenario, report, evidence)
        repeated = run_repeated(
            self.scenario,
            lambda _: (report, evidence),
            repetitions=3,
        )

        self.assertTrue(score.passed)
        self.assertEqual(score.score, 100)
        self.assertEqual(repeated, (score, score, score))
        report["spec"]["evidenceIds"] = required[:1]
        self.assertFalse(score_report(self.scenario, report, evidence).passed)


class GovernedActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resources = InMemoryResourceStore()
        self.policy = AllowTenantPolicy()
        self.clock = SystemClock()
        resource = example("resource.json")
        actor = ActorContext("collector-local", "local")
        stored = ResourceIngestionService(self.resources, self.policy).execute(
            IngestResourceCommand(actor, resource)
        )
        self.target_uid = stored.identity.uid
        self.store = InMemoryOperationalStore()
        self.service = GovernedActionService(
            self.resources,
            self.policy,
            self.store,
            KubernetesRestartDryRunExecutor(),
            self.store,
            self.clock,
        )
        self.proposer = ActorContext("incident-investigator", "local")

    def proposal(self) -> dict:
        return self.service.propose(
            ProposeActionCommand(
                actor=self.proposer,
                investigation_id="inv_71c9e4a2b8d04f65a3c7e9b102d4f608",
                action_type="kubernetes.restart-workload",
                target_resource_uid=self.target_uid,
                parameters={
                    "namespace": "iip-demo",
                    "workloadKind": "deployment",
                    "workloadName": "api",
                },
                idempotency_key="incident-71c9-restart-api-001",
                expires_at="2099-08-14T13:30:00Z",
            )
        )

    def test_approval_requires_separate_role_and_execution_is_idempotent(self) -> None:
        proposal = self.proposal()
        assert_schema(self, "action-proposal.schema.json", proposal)
        with self.assertRaisesRegex(ActionWorkflowError, "role-required"):
            self.service.decide(
                DecideActionCommand(self.proposer, proposal["metadata"]["id"], "approved", "ok")
            )
        with self.assertRaisesRegex(ActionWorkflowError, "self-denied"):
            self.service.decide(
                DecideActionCommand(
                    ActorContext("incident-investigator", "local", ("approver",)),
                    proposal["metadata"]["id"],
                    "approved",
                    "same actor",
                )
            )
        approval = self.service.decide(
            DecideActionCommand(
                ActorContext("on-call-approver", "local", ("approver",)),
                proposal["metadata"]["id"],
                "approved",
                "Scoped dry-run is safe.",
            )
        )
        assert_schema(self, "action-approval.schema.json", approval)
        executor = ActorContext("workflow-executor", "local", ("executor",))
        result = self.service.execute(
            ExecuteActionCommand(executor, proposal["metadata"]["id"])
        )
        replay = self.service.execute(
            ExecuteActionCommand(executor, proposal["metadata"]["id"])
        )
        self.assertEqual(result, replay)
        self.assertEqual(result["spec"]["outcome"], "dry-run")
        assert_schema(self, "action-result.schema.json", result)


class PluginSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("plugin-host", "local")
        self.service = PluginSessionService(
            AllowTenantPolicy(), InMemoryOperationalStore(), SystemClock()
        )
        self.manifest = example("plugin-manifest.json")

    def test_handshake_grants_only_declared_capabilities_without_storing_token(self) -> None:
        token = "development-capability-token-0000000000000000"
        session = self.service.open(
            OpenPluginSessionCommand(
                self.actor, self.manifest, ("resource-observer",), token
            )
        )

        assert_schema(self, "plugin-session.schema.json", session)
        self.assertNotIn(token, json.dumps(session))
        self.assertEqual(session["spec"]["grantedCapabilities"], ["resource-observer"])

    def test_handshake_rejects_undeclared_capability(self) -> None:
        with self.assertRaisesRegex(PluginHandshakeError, "not-declared"):
            self.service.open(
                OpenPluginSessionCommand(
                    self.actor,
                    self.manifest,
                    ("action-executor",),
                    "development-capability-token-0000000000000000",
                )
            )


class OperationalHttpTests(unittest.TestCase):
    def test_investigation_post_and_get_use_authenticated_scope(self) -> None:
        token = "operational-http-token-0123456789abcdef0123456789abcdef"
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(token),
                            "actorId": "local-operator",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        runtime = build_local_runtime(authenticator)
        actor = ActorContext("local-operator", "local", ("developer",))
        resource = runtime.ingestion.execute(
            IngestResourceCommand(actor, example("resource.json"))
        )
        request = copy.deepcopy(example("investigation-request.json"))
        request["metadata"]["actorId"] = actor.actor_id
        request["spec"]["scope"]["resourceUids"] = [resource.identity.uid]
        request["spec"]["evidenceTypes"] = ["kubernetes.resource-status"]

        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/investigations"
        handler.headers = {"authorization": f"Bearer {token}"}
        handler._read_json = lambda: request
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_POST()

        self.assertEqual(responses[0][0], HTTPStatus.CREATED)
        report = responses[0][1]
        self.assertEqual(report["kind"], "InvestigationReport")
        responses.clear()
        handler.path = f"/v1/investigations/{request['metadata']['id']}"
        handler.do_GET()
        self.assertEqual(responses, [(HTTPStatus.OK, report)])

    def test_operational_get_maps_storage_failure_to_stable_error(self) -> None:
        token = "operational-read-error-token-0123456789abcdef"
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(token),
                            "actorId": "local-operator",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )

        class FailingOperationalStore:
            def get_investigation(self, actor: ActorContext, investigation_id: str) -> None:
                raise PersistenceError("provider-specific detail")

        handler = object.__new__(ApiHandler)
        handler.runtime = replace(
            build_local_runtime(authenticator),
            operational_store=FailingOperationalStore(),
        )
        handler.path = "/v1/investigations/inv_unavailable"
        handler.headers = {"authorization": f"Bearer {token}"}
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_GET()

        self.assertEqual(
            responses,
            [
                (
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "storage.unavailable"}},
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
