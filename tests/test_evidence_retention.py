from __future__ import annotations

import hashlib
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker
from infra_intelligence_sdk import Client, EvidenceRetentionReport
from iip.adapters.evidence import InMemoryEvidenceStore
from iip.adapters.memory import AllowTenantPolicy
from iip.application.evidence_retention import (
    EvidenceRetentionAuthorizationError,
    EvidenceRetentionPolicy,
    EvidenceRetentionService,
    EvidenceRetentionStateError,
    GetEvidenceRetentionCommand,
)
from iip.application.ports import ActorContext, EvidenceRetentionState
from iip.bootstrap import _evidence_retention_policy_from_env, build_local_runtime
from iip.surfaces.http import ApiHandler
from iip.surfaces.worker import (
    evidence_retention_interval_seconds,
    run_evidence_retention_pass,
)


ROOT = Path(__file__).resolve().parents[1]
TENANT = "tenant-acme"
TOKEN = "retention-token-0123456789abcdef0123456789abcdef"


class FixedClock:
    def now(self) -> str:
        return "2026-08-17T16:00:00Z"


def evidence_document(
    index: int,
    *,
    tenant_id: str = TENANT,
    recorded_at: str,
    retention_class: str,
    expires_at: str | None = None,
) -> tuple[str, dict[str, object], bytes]:
    evidence_id = f"evd_{index:032x}"
    content = f"safe-artifact-{tenant_id}-{index}".encode()
    handling: dict[str, object] = {
        "redaction": {"status": "not-required", "methods": []},
        "sensitivity": "internal",
        "retentionClass": retention_class,
    }
    if expires_at is not None:
        handling["expiresAt"] = expires_at
    document: dict[str, object] = {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "Evidence",
        "metadata": {
            "id": evidence_id,
            "tenantId": tenant_id,
            "recordedAt": recorded_at,
        },
        "spec": {
            "type": "test-artifact",
            "source": {
                "provider": "test",
                "integrationId": "int_test",
                "locator": "test://artifact",
            },
            "observedAt": recorded_at,
            "retrievedAt": recorded_at,
            "resourceRefs": [],
            "summary": "Safe artifact fixture.",
            "artifact": {
                "mediaType": "text/plain",
                "contentHash": "sha256:" + hashlib.sha256(content).hexdigest(),
                "sizeBytes": len(content),
                "encoding": "identity",
                "storageRef": f"evidence://{tenant_id}/{evidence_id}/artifact",
            },
            "handling": handling,
        },
    }
    return evidence_id, document, content


class EvidenceRetentionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryEvidenceStore()
        self.actor = ActorContext(
            "operator",
            TENANT,
            ("platform-admin",),
        )
        fixtures = (
            evidence_document(
                1,
                recorded_at="2026-08-15T00:00:00Z",
                retention_class="ephemeral",
            ),
            evidence_document(
                2,
                recorded_at="2026-08-17T15:00:00Z",
                retention_class="standard",
            ),
            evidence_document(
                3,
                recorded_at="2026-01-01T00:00:00Z",
                retention_class="legal-hold",
                expires_at="2026-01-02T00:00:00Z",
            ),
            evidence_document(
                4,
                recorded_at="2026-08-17T15:00:00Z",
                retention_class="extended",
                expires_at="2026-08-16T12:00:00Z",
            ),
        )
        for evidence_id, document, content in fixtures:
            self.store.commit(self.actor, evidence_id, document, content)
        other = ActorContext("operator", "tenant-other", ("platform-admin",))
        evidence_id, document, content = evidence_document(
            5,
            tenant_id="tenant-other",
            recorded_at="2026-01-01T00:00:00Z",
            retention_class="ephemeral",
        )
        self.store.commit(other, evidence_id, document, content)

    def service(self, *, enabled: bool, batch_size: int = 1) -> EvidenceRetentionService:
        return EvidenceRetentionService(
            self.store,
            AllowTenantPolicy(),
            FixedClock(),
            EvidenceRetentionPolicy(enabled=enabled, batch_size=batch_size),
        )

    def test_observe_is_non_mutating_tenant_scoped_and_legal_hold_aware(self) -> None:
        report = self.service(enabled=True).get(
            GetEvidenceRetentionCommand(self.actor)
        ).to_dict()

        self.assertEqual(report["spec"]["status"], "cleanup-required")
        self.assertEqual(
            report["spec"]["artifacts"],
            {
                "storedBefore": 4,
                "eligible": 2,
                "expired": 0,
                "remainingEligible": 2,
                "legalHold": 1,
            },
        )
        self.assertNotIn("auditRef", report["spec"])
        for index in range(1, 5):
            self.assertIsNotNone(self.store.read_artifact(self.actor, f"evd_{index:032x}"))

    def test_expire_is_bounded_audited_and_preserves_metadata_and_other_tenants(self) -> None:
        service = self.service(enabled=True)
        first = service.expire(TENANT).to_dict()

        self.assertEqual(first["spec"]["mode"], "expire")
        self.assertEqual(first["spec"]["status"], "cleanup-required")
        self.assertEqual(first["spec"]["artifacts"]["expired"], 1)
        self.assertEqual(first["spec"]["artifacts"]["remainingEligible"], 1)
        self.assertRegex(first["spec"]["auditRef"], r"^audit://tenant-acme/")
        self.assertIsNone(self.store.read_artifact(self.actor, f"evd_{1:032x}"))
        self.assertIsNotNone(self.store.get(self.actor, f"evd_{1:032x}"))

        second = service.expire(TENANT).to_dict()
        self.assertEqual(second["spec"]["status"], "current")
        self.assertEqual(second["spec"]["artifacts"]["expired"], 1)
        self.assertEqual(second["spec"]["artifacts"]["remainingEligible"], 0)
        self.assertIsNone(self.store.read_artifact(self.actor, f"evd_{4:032x}"))
        self.assertIsNotNone(self.store.read_artifact(self.actor, f"evd_{3:032x}"))
        other = ActorContext("operator", "tenant-other", ("platform-admin",))
        self.assertIsNotNone(self.store.read_artifact(other, f"evd_{5:032x}"))

        serialized_audit = json.dumps(self.store._retention_audit)
        self.assertNotIn("evd_", serialized_audit)
        self.assertNotIn("safe-artifact", serialized_audit)

    def test_authority_disabled_mode_configuration_and_invalid_store_fail_closed(self) -> None:
        disabled = self.service(enabled=False)
        report = disabled.get(GetEvidenceRetentionCommand(self.actor)).to_dict()
        self.assertEqual(report["spec"]["status"], "disabled")
        with self.assertRaises(EvidenceRetentionAuthorizationError):
            disabled.expire(TENANT)
        with self.assertRaises(EvidenceRetentionAuthorizationError):
            disabled.get(
                GetEvidenceRetentionCommand(
                    ActorContext("viewer", TENANT, ("viewer",))
                )
            )
        with self.assertRaisesRegex(
            ValueError,
            "evidence.retention.configuration.invalid",
        ):
            EvidenceRetentionPolicy(
                ephemeral_seconds=2_592_000,
                standard_seconds=86_400,
            )

        class InvalidStore:
            def evaluate_evidence_retention(self, tenant_id, evaluated_at, **parameters):
                del parameters
                return EvidenceRetentionState(
                    tenant_id=tenant_id,
                    evaluated_at=evaluated_at,
                    stored_artifacts=1,
                    eligible_artifacts=1,
                    expired_artifacts=0,
                    remaining_eligible_artifacts=0,
                    legal_hold_artifacts=0,
                )

        invalid = EvidenceRetentionService(
            InvalidStore(),
            AllowTenantPolicy(),
            FixedClock(),
            EvidenceRetentionPolicy(enabled=True),
        )
        with self.assertRaises(EvidenceRetentionStateError):
            invalid.get(GetEvidenceRetentionCommand(self.actor))


class EvidenceRetentionSchemaTests(unittest.TestCase):
    def test_status_mode_expiration_and_audit_conditions_fail_closed(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/schemas/evidence-retention-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        example = json.loads(
            (ROOT / "contracts/examples/evidence-retention-report.json").read_text(
                encoding="utf-8"
            )
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self.assertEqual(list(validator.iter_errors(example)), [])

        mutations = []
        observe_expired = json.loads(json.dumps(example))
        observe_expired["spec"]["artifacts"]["expired"] = 1
        mutations.append(observe_expired)
        cleanup_without_backlog = json.loads(json.dumps(example))
        cleanup_without_backlog["spec"]["artifacts"]["remainingEligible"] = 0
        mutations.append(cleanup_without_backlog)
        current_disabled = json.loads(json.dumps(example))
        current_disabled["spec"]["status"] = "current"
        current_disabled["spec"]["policy"]["enabled"] = False
        current_disabled["spec"]["artifacts"]["remainingEligible"] = 0
        mutations.append(current_disabled)
        audit_without_expiration = json.loads(json.dumps(example))
        audit_without_expiration["spec"]["auditRef"] = (
            "audit://tenant-acme/records/1"
        )
        mutations.append(audit_without_expiration)

        for document in mutations:
            with self.subTest(document=document["spec"]):
                self.assertTrue(list(validator.iter_errors(document)))


class EvidenceRetentionSurfaceTests(unittest.TestCase):
    def test_http_and_python_sdk_use_the_public_contract(self) -> None:
        class Authenticator:
            def authenticate_bearer(self, token: str) -> ActorContext:
                if token != TOKEN:
                    raise AssertionError(token)
                return ActorContext("operator", TENANT, ("platform-admin",))

        runtime = build_local_runtime(Authenticator())
        self.addCleanup(runtime.close)
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/operations/evidence/retention"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))

        handler.do_GET()

        self.assertEqual(handler.responses[0][0], HTTPStatus.OK)
        payload = handler.responses[0][1]
        self.assertEqual(payload["kind"], "EvidenceRetentionReport")
        self.assertEqual(payload["metadata"]["tenantId"], TENANT)
        self.assertEqual(payload["spec"]["status"], "disabled")

        example = json.loads(
            (ROOT / "contracts/examples/evidence-retention-report.json").read_text(
                encoding="utf-8"
            )
        )
        client = Client("https://control.example", TOKEN)
        client._get = lambda path: example if path == "/v1/operations/evidence/retention" else {}  # type: ignore[method-assign]
        sdk_report = client.get_evidence_retention()
        self.assertIsInstance(sdk_report, EvidenceRetentionReport)
        self.assertEqual(sdk_report.to_dict(), example)

    def test_environment_worker_interval_and_failure_isolation_are_closed(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "IIP_EVIDENCE_RETENTION_ENABLED": "true",
                "IIP_EVIDENCE_RETENTION_INTERVAL_SECONDS": "600",
                "IIP_EVIDENCE_RETENTION_EPHEMERAL_SECONDS": "7200",
                "IIP_EVIDENCE_RETENTION_STANDARD_SECONDS": "86400",
                "IIP_EVIDENCE_RETENTION_EXTENDED_SECONDS": "172800",
                "IIP_EVIDENCE_RETENTION_BATCH_SIZE": "25",
            },
            clear=True,
        ):
            policy = _evidence_retention_policy_from_env()
            interval = evidence_retention_interval_seconds()
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.batch_size, 25)
        self.assertEqual(interval, 600)

        class Service:
            def expire(self, tenant_id: str):
                if tenant_id == "tenant-b":
                    raise RuntimeError("secret provider detail")
                return SimpleNamespace(
                    to_dict=lambda: {
                        "spec": {
                            "artifacts": {
                                "expired": 2,
                                "remainingEligible": 3,
                            }
                        }
                    }
                )

        summary = run_evidence_retention_pass(
            Service(),
            ("tenant-a", "tenant-b"),
        )
        self.assertEqual(summary.tenants, 2)
        self.assertEqual(summary.expired_artifacts, 2)
        self.assertEqual(summary.remaining_eligible_artifacts, 3)
        self.assertEqual(summary.failures, 1)

        for environment in (
            {"IIP_EVIDENCE_RETENTION_ENABLED": "maybe"},
            {"IIP_EVIDENCE_RETENTION_BATCH_SIZE": "all"},
            {"IIP_EVIDENCE_RETENTION_INTERVAL_SECONDS": "59"},
        ):
            with self.subTest(environment=environment), patch.dict(
                "os.environ", environment, clear=True
            ), self.assertRaisesRegex(
                ValueError,
                "evidence.retention.configuration.invalid",
            ):
                if "INTERVAL" in next(iter(environment)):
                    evidence_retention_interval_seconds()
                else:
                    _evidence_retention_policy_from_env()


if __name__ == "__main__":
    unittest.main()
