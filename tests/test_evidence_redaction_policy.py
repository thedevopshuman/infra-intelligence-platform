from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import EvidenceRedactionPolicy
from iip.adapters.evidence import StructuredTextRedactor
from iip.adapters.evidence_redaction import (
    EvidenceRedactionPolicyConfigurationError,
    EvidenceRedactionPolicyRegistry,
    derive_evidence_redaction_policy_id,
    validate_evidence_redaction_policy,
)
from iip.application.collect_evidence import CollectEvidenceCommand
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.ports import ActorContext, RawEvidenceArtifact
from iip.bootstrap import build_local_runtime, build_runtime_from_env


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"
SCHEMAS = ROOT / "contracts" / "schemas"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reidentify(policy: dict) -> None:
    identity = {
        "tenantId": policy["metadata"]["tenantId"],
        "version": policy["metadata"]["version"],
        "spec": policy["spec"],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    policy["metadata"]["id"] = "erp_" + digest[:32]


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class EvidenceRedactionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load(EXAMPLES / "evidence-redaction-policy.json")

    def test_example_schema_sdk_identity_and_registry_round_trip(self) -> None:
        schema = load(SCHEMAS / "evidence-redaction-policy.schema.json")
        self.assertEqual(
            [],
            validate_schemas.instance_validation_errors(
                schema,
                self.policy,
                label="evidence redaction policy",
            ),
        )
        validated = validate_evidence_redaction_policy(self.policy)
        self.assertEqual("tenant-a", validated.tenant_id)
        self.assertEqual(
            frozenset({"email-address", "ipv4-address"}),
            validated.value_classes_for("repository.context"),
        )
        self.assertEqual(
            self.policy,
            EvidenceRedactionPolicy.from_dict(self.policy).to_dict(),
        )
        registry = EvidenceRedactionPolicyRegistry.from_json(
            json.dumps({"policies": [self.policy]})
        )
        self.assertEqual(self.policy["metadata"]["id"], registry.get("tenant-a").policy_id)
        self.assertIsNone(registry.get("tenant-b"))
        with self.assertRaises(TypeError):
            registry.get("tenant-a").rules["repository.context"] = frozenset()
        with self.assertRaises(TypeError):
            registry.get("tenant-a").document["spec"] = {}
        self.assertEqual((self.policy,), registry.documents())

        placeholder = copy.deepcopy(self.policy)
        placeholder["metadata"]["id"] = "erp_" + "0" * 32
        self.assertEqual(
            self.policy["metadata"]["id"],
            derive_evidence_redaction_policy_id(placeholder),
        )

    def test_policy_and_set_are_closed_ordered_and_unambiguous(self) -> None:
        invalid: list[dict] = []

        wrong_id = copy.deepcopy(self.policy)
        wrong_id["metadata"]["id"] = "erp_" + "f" * 32
        invalid.append(wrong_id)

        extra = copy.deepcopy(self.policy)
        extra["spec"]["disableCredentialRedaction"] = True
        reidentify(extra)
        invalid.append(extra)

        unsupported = copy.deepcopy(self.policy)
        unsupported["spec"]["rules"][0]["valueClasses"] = ["phone-number"]
        reidentify(unsupported)
        invalid.append(unsupported)

        unsorted_rules = copy.deepcopy(self.policy)
        unsorted_rules["spec"]["rules"].reverse()
        reidentify(unsorted_rules)
        invalid.append(unsorted_rules)

        duplicate_scope = copy.deepcopy(self.policy)
        duplicate_scope["spec"]["rules"][1]["evidenceTypes"] = [
            "repository.context"
        ]
        reidentify(duplicate_scope)
        invalid.append(duplicate_scope)

        unsorted_classes = copy.deepcopy(self.policy)
        unsorted_classes["spec"]["rules"][0]["valueClasses"].reverse()
        reidentify(unsorted_classes)
        invalid.append(unsorted_classes)

        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaisesRegex(
                EvidenceRedactionPolicyConfigurationError,
                "evidence.redaction.configuration.invalid",
            ):
                validate_evidence_redaction_policy(policy)

        duplicate = copy.deepcopy(self.policy)
        duplicate["metadata"]["version"] = "2026-09-06.2"
        reidentify(duplicate)
        with self.assertRaisesRegex(
            EvidenceRedactionPolicyConfigurationError,
            "evidence.redaction.configuration.invalid",
        ):
            EvidenceRedactionPolicyRegistry((self.policy, duplicate))
        for raw in ('{"policies":[]}', '{"policies":[],"extra":true}', "not-json"):
            with self.subTest(raw=raw), self.assertRaisesRegex(
                EvidenceRedactionPolicyConfigurationError,
                "evidence.redaction.configuration.invalid",
            ):
                EvidenceRedactionPolicyRegistry.from_json(raw)

    def test_detectors_are_additive_exact_tenant_and_exact_evidence_type(self) -> None:
        redactor = StructuredTextRedactor(
            EvidenceRedactionPolicyRegistry((self.policy,))
        )
        content = (
            b"contact=ops@example.test client=10.2.3.4 bad=999.2.3.4 "
            b"password=customer-secret"
        )
        selected = redactor.redact(
            content,
            tenant_id="tenant-a",
            media_type="text/plain",
            evidence_type="repository.context",
        )
        self.assertNotIn(b"ops@example.test", selected.content)
        self.assertNotIn(b"10.2.3.4", selected.content)
        self.assertIn(b"999.2.3.4", selected.content)
        self.assertNotIn(b"customer-secret", selected.content)
        self.assertEqual(
            (
                "policy-email-address",
                "policy-ipv4-address",
                "secret-pattern",
            ),
            selected.methods,
        )
        self.assertEqual(self.policy["metadata"]["id"], selected.policy_id)

        wrong_type = redactor.redact(
            content,
            tenant_id="tenant-a",
            media_type="text/plain",
            evidence_type="kubernetes.status",
        )
        other_tenant = redactor.redact(
            content,
            tenant_id="tenant-b",
            media_type="text/plain",
            evidence_type="repository.context",
        )
        for result in (wrong_type, other_tenant):
            self.assertIn(b"ops@example.test", result.content)
            self.assertIn(b"10.2.3.4", result.content)
            self.assertNotIn(b"customer-secret", result.content)
            self.assertEqual(("secret-pattern",), result.methods)
        self.assertEqual(self.policy["metadata"]["id"], wrong_type.policy_id)
        self.assertIsNone(other_tenant.policy_id)

    def test_evidence_records_minimized_policy_provenance_after_redaction(self) -> None:
        runtime = build_local_runtime(evidence_redaction_policies=(self.policy,))
        now = datetime.now(timezone.utc)
        actor = ActorContext("investigator", "tenant-a")
        resource = load(EXAMPLES / "resource.json")
        resource["metadata"]["tenantId"] = actor.tenant_id
        resource["metadata"]["observedAt"] = iso(now - timedelta(seconds=2))
        stored_resource = runtime.ingestion.execute(
            IngestResourceCommand(actor, resource)
        )
        document = runtime.evidence.record_artifact(
            CollectEvidenceCommand(
                actor=actor,
                provider="context",
                integration_id="context-local",
                evidence_type="repository.context",
                resource_uids=(stored_resource.identity.uid,),
                locator="context://reviewed/runbook",
                deadline=iso(now + timedelta(minutes=1)),
                sensitivity="confidential",
            ),
            RawEvidenceArtifact(
                content=(
                    b"owner=ops@example.test source=10.2.3.4 "
                    b"token=customer-secret"
                ),
                media_type="text/plain",
                observed_at=iso(now - timedelta(seconds=1)),
                summary="Reviewed redaction-policy fixture.",
            ),
        )
        redaction = document["spec"]["handling"]["redaction"]
        self.assertEqual(
            {
                "id": self.policy["metadata"]["id"],
                "version": self.policy["metadata"]["version"],
            },
            redaction["policyRef"],
        )
        self.assertEqual(
            [
                "policy-email-address",
                "policy-ipv4-address",
                "secret-pattern",
            ],
            redaction["methods"],
        )
        artifact = runtime.evidence_store.read_artifact(
            actor,
            document["metadata"]["id"],
        )
        self.assertIsNotNone(artifact)
        self.assertNotIn(b"ops@example.test", artifact)
        self.assertNotIn(b"10.2.3.4", artifact)
        self.assertNotIn(b"customer-secret", artifact)

        evidence_schema = load(SCHEMAS / "evidence.schema.json")
        self.assertEqual(
            [],
            validate_schemas.instance_validation_errors(
                evidence_schema,
                document,
                label="policy-redacted evidence",
            ),
        )

    def test_environment_composition_accepts_exact_wrapper_and_fails_closed(self) -> None:
        identities = json.dumps(
            {
                "identities": [
                    {
                        "tokenSha256": "sha256:" + "a" * 64,
                        "actorId": "redaction-test",
                        "tenantId": "tenant-a",
                        "roles": ["developer"],
                    }
                ]
            }
        )
        wrapper = json.dumps({"policies": [self.policy]})
        environment = {
            "IIP_AUTH_IDENTITIES_JSON": identities,
            "IIP_EVIDENCE_REDACTION_POLICIES_JSON": wrapper,
        }
        with patch.dict(os.environ, environment, clear=True):
            runtime = build_runtime_from_env()

        result = runtime.evidence._redactor.redact(  # noqa: SLF001
            b"owner=ops@example.test token=customer-secret",
            tenant_id="tenant-a",
            media_type="text/plain",
            evidence_type="repository.context",
        )
        self.assertEqual(self.policy["metadata"]["id"], result.policy_id)
        self.assertNotIn(b"ops@example.test", result.content)
        self.assertNotIn(b"customer-secret", result.content)

        disabled_environment = dict(environment)
        disabled_environment["IIP_EVIDENCE_REDACTION_POLICIES_JSON"] = ""
        with patch.dict(os.environ, disabled_environment, clear=True):
            unconfigured = build_runtime_from_env()
        unconfigured_result = unconfigured.evidence._redactor.redact(  # noqa: SLF001
            b"owner=ops@example.test token=customer-secret",
            tenant_id="tenant-a",
            media_type="text/plain",
            evidence_type="repository.context",
        )
        self.assertIsNone(unconfigured_result.policy_id)
        self.assertIn(b"ops@example.test", unconfigured_result.content)
        self.assertNotIn(b"customer-secret", unconfigured_result.content)

        invalid_environment = dict(environment)
        invalid_environment["IIP_EVIDENCE_REDACTION_POLICIES_JSON"] = (
            '{"policies":[],"secret":"must-not-appear"}'
        )
        with patch.dict(os.environ, invalid_environment, clear=True), self.assertRaisesRegex(
            EvidenceRedactionPolicyConfigurationError,
            "evidence.redaction.configuration.invalid",
        ) as raised:
            build_runtime_from_env()
        self.assertNotIn("must-not-appear", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
