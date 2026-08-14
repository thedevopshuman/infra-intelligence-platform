from __future__ import annotations

import hashlib
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from iip.adapters.evidence import (
    InMemoryEvidenceStore,
    StructuredTextRedactor,
)
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.collect_evidence import (
    CollectEvidenceCommand,
    EvidenceAuthorizationError,
    EvidenceCollectionService,
    EvidenceDeadlineExceededError,
    EvidenceProviderUnavailableError,
    EvidenceRedactionError,
    InvalidEvidenceRequestError,
)
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.ports import (
    ActorContext,
    EvidenceProviderRequest,
    PolicyDecision,
    RawEvidenceArtifact,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


EVIDENCE_ID = "evd_1234567890abcdef1234567890abcdef"


def resource_payload() -> dict:
    return json.loads((ROOT / "contracts/examples/resource.json").read_text("utf-8"))


class SequenceClock:
    def __init__(self, *timestamps: str) -> None:
        self._timestamps = iter(timestamps)

    def now(self) -> str:
        return next(self._timestamps)


class FixedEvidenceIds:
    def new_id(self) -> str:
        return EVIDENCE_ID


class RecordingProvider:
    def __init__(self, artifact: RawEvidenceArtifact) -> None:
        self.artifact = artifact
        self.requests: list[EvidenceProviderRequest] = []
        self.failure: Exception | None = None

    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.artifact


class RecordingPolicy:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[ActorContext, str, Mapping[str, object]]] = []

    def decide(
        self,
        actor: ActorContext,
        action: str,
        resource: Mapping[str, object],
    ) -> PolicyDecision:
        self.calls.append((actor, action, resource))
        return PolicyDecision(self.allowed, "test.allow" if self.allowed else "test.deny")


class EvidenceCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resources = InMemoryResourceStore()
        self.resource = ResourceIngestionService(
            self.resources,
            AllowTenantPolicy(),
        ).execute(
            IngestResourceCommand(
                actor=ActorContext("developer", "local"),
                payload=resource_payload(),
            )
        )
        self.store = InMemoryEvidenceStore()
        self.actor = ActorContext("investigator", "local")
        self.artifact = RawEvidenceArtifact(
            content=(
                b'{"authorization":"Bearer fake-provider-token",'
                b'"message":"deployment unavailable",'
                b'"nested":{"password":"fake-password"}}'
            ),
            media_type="application/json",
            observed_at="2026-08-14T10:29:54Z",
            summary="The deployment has fewer available replicas than desired.",
        )
        self.provider = RecordingProvider(self.artifact)
        self.policy = RecordingPolicy()

    def command(self, **changes: object) -> CollectEvidenceCommand:
        command = CollectEvidenceCommand(
            actor=self.actor,
            provider="kubernetes",
            integration_id="kubernetes-local",
            evidence_type="kubernetes.status",
            resource_uids=(self.resource.identity.uid,),
            locator="kubernetes://cluster-local/namespaces/default/deployments/api",
            query="namespace=default, name=api",
            deadline="2026-08-14T10:31:00Z",
            expires_at="2026-09-13T10:30:06Z",
        )
        return replace(command, **changes)

    def service(
        self,
        *,
        clock: SequenceClock | None = None,
        policy: RecordingPolicy | AllowTenantPolicy | None = None,
    ) -> EvidenceCollectionService:
        return EvidenceCollectionService(
            resources=self.resources,
            providers={"kubernetes": self.provider},
            store=self.store,
            redactor=StructuredTextRedactor(),
            policy=policy or self.policy,
            ids=FixedEvidenceIds(),
            clock=clock
            or SequenceClock(
                "2026-08-14T10:30:00Z",
                "2026-08-14T10:30:05Z",
                "2026-08-14T10:30:06Z",
            ),
        )

    def test_collection_redacts_hashes_validates_and_commits_atomically(self) -> None:
        document = self.service().execute(self.command())

        schema = json.loads(
            (ROOT / "contracts/schemas/evidence.schema.json").read_text("utf-8")
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema,
                document,
                label="collected evidence",
            ),
            [],
        )
        stored = self.store.read_artifact(self.actor, EVIDENCE_ID)
        self.assertIsNotNone(stored)
        self.assertNotIn(b"fake-provider-token", stored)
        self.assertNotIn(b"fake-password", stored)
        self.assertIn(b"[REDACTED]", stored)
        artifact = document["spec"]["artifact"]
        self.assertEqual(
            artifact["contentHash"],
            "sha256:" + hashlib.sha256(stored).hexdigest(),
        )
        self.assertEqual(artifact["sizeBytes"], len(stored))
        self.assertEqual(
            document["spec"]["handling"]["redaction"],
            {"status": "applied", "methods": ["structured-secret-fields"]},
        )
        self.assertEqual(document["spec"]["retrievedAt"], "2026-08-14T10:30:05Z")
        self.assertEqual(document["metadata"]["recordedAt"], "2026-08-14T10:30:06Z")

    def test_unmodified_artifact_is_hashed_without_reencoding(self) -> None:
        content = b'{ "status": "degraded" }\n'
        self.provider.artifact = replace(self.artifact, content=content)

        document = self.service().execute(self.command(expires_at=None))

        self.assertEqual(self.store.read_artifact(self.actor, EVIDENCE_ID), content)
        self.assertEqual(
            document["spec"]["handling"]["redaction"],
            {"status": "not-required", "methods": []},
        )
        self.assertNotIn("expiresAt", document["spec"]["handling"])

    def test_policy_receives_exact_scope_before_provider_call(self) -> None:
        self.service().execute(self.command())

        self.assertEqual(len(self.policy.calls), 1)
        actor, action, scope = self.policy.calls[0]
        self.assertEqual(actor, ActorContext("investigator", "local"))
        self.assertEqual(action, "evidence:collect")
        self.assertEqual(scope["tenantId"], "local")
        self.assertEqual(scope["resourceUids"], (self.resource.identity.uid,))
        request = self.provider.requests[0]
        self.assertEqual(request.tenant_id, "local")
        self.assertEqual(request.actor_id, "investigator")
        self.assertFalse(hasattr(request, "credentials"))

    def test_denied_actor_never_reaches_provider(self) -> None:
        with self.assertRaisesRegex(EvidenceAuthorizationError, "actor.anonymous"):
            self.service(policy=AllowTenantPolicy()).execute(
                self.command(actor=ActorContext("anonymous", "local"))
            )

        self.assertEqual(self.provider.requests, [])
        self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_unresolved_or_cross_tenant_resource_never_reaches_provider(self) -> None:
        with self.assertRaisesRegex(
            InvalidEvidenceRequestError,
            "evidence.resource.unavailable",
        ):
            self.service().execute(
                self.command(resource_uids=("res_00000000000000000000000000000000",))
            )

        self.assertEqual(self.provider.requests, [])
        self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_provider_errors_are_replaced_with_a_stable_code(self) -> None:
        self.provider.failure = RuntimeError("provider stack and internal endpoint")

        with self.assertRaises(EvidenceProviderUnavailableError) as raised:
            self.service().execute(self.command())

        self.assertEqual(str(raised.exception), "evidence.provider.unavailable")
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn("internal endpoint", str(raised.exception))
        self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_provider_output_cannot_exceed_request_limit(self) -> None:
        self.provider.artifact = replace(self.artifact, content=b"12345")

        with self.assertRaisesRegex(
            InvalidEvidenceRequestError,
            "evidence.provider.output-limited",
        ):
            self.service().execute(self.command(max_bytes=4))

        self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_unsupported_artifact_fails_closed_before_persistence(self) -> None:
        self.provider.artifact = replace(
            self.artifact,
            content=b"binary",
            media_type="application/octet-stream",
        )

        with self.assertRaisesRegex(EvidenceRedactionError, "evidence.redaction.failed"):
            self.service().execute(self.command())

        self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_deadline_is_enforced_after_provider_returns(self) -> None:
        clock = SequenceClock(
            "2026-08-14T10:30:00Z",
            "2026-08-14T10:31:01Z",
        )

        with self.assertRaisesRegex(
            EvidenceDeadlineExceededError,
            "evidence.deadline.exceeded",
        ):
            self.service(clock=clock).execute(self.command())

        self.assertEqual(len(self.provider.requests), 1)
        self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_secret_bearing_request_or_summary_is_rejected(self) -> None:
        commands = (
            self.command(locator="https://user:fake@example.test/evidence"),
            self.command(query="token=fake-value"),
        )
        for command in commands:
            with self.subTest(command=command):
                with self.assertRaises(InvalidEvidenceRequestError):
                    self.service().execute(command)
        self.assertEqual(self.provider.requests, [])

        self.provider.artifact = replace(
            self.artifact,
            summary="authorization: Bearer fake-summary-value",
        )
        with self.assertRaises(InvalidEvidenceRequestError):
            self.service().execute(self.command())
        self.assertIsNone(self.store.get(self.actor, EVIDENCE_ID))

    def test_store_is_immutable_and_tenant_scoped(self) -> None:
        document = self.service().execute(self.command())

        returned = self.store.get(self.actor, EVIDENCE_ID)
        returned["metadata"]["tenantId"] = "changed"
        self.assertEqual(self.store.get(self.actor, EVIDENCE_ID), document)
        other_tenant = ActorContext("investigator", "another-tenant")
        self.assertIsNone(self.store.get(other_tenant, EVIDENCE_ID))
        self.assertIsNone(self.store.read_artifact(other_tenant, EVIDENCE_ID))
        anonymous = ActorContext("anonymous", "local")
        self.assertIsNone(self.store.get(anonymous, EVIDENCE_ID))


if __name__ == "__main__":
    unittest.main()
