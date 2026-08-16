"""External policy decision boundary tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from iip.adapters.policy import (
    ExternalHttpPolicyDecisionPoint,
    ExternalPolicyConfiguration,
)
from iip.application.ports import ActorContext, PolicyConfigurationError
from iip.adapters.auth import HashedBearerAuthenticator
from iip.bootstrap import build_runtime_from_env
from unittest.mock import patch


class RecordingTransport:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        endpoint,
        body,
        headers,
        context,
        timeout_seconds,
        max_response_bytes,
    ):
        del context
        self.calls.append(
            {
                "endpoint": endpoint,
                "document": json.loads(body),
                "headers": dict(headers),
                "timeout": timeout_seconds,
                "maximum": max_response_bytes,
            }
        )
        if self.error is not None:
            raise self.error
        response = self.response
        if response is None:
            request = self.calls[-1]["document"]["input"]
            digest = "sha256:" + sha256(
                json.dumps(
                    request,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            response = {
                "result": {
                    "apiVersion": "iip.platform/v1alpha1",
                    "kind": "PolicyDecision",
                    "metadata": {"tenantId": request["metadata"]["tenantId"]},
                    "spec": {
                        "allowed": True,
                        "inputDigest": digest,
                        "reasonCode": "policy.resource-read-allowed",
                        "policySnapshotRef": "policy://tenant-a/snapshots/bundle-42",
                    },
                }
            }
        return json.dumps(response).encode("utf-8")


class ExternalPolicyDecisionPointTests(unittest.TestCase):
    actor = ActorContext("operator-a", "tenant-a", ("developer",))

    def configuration(self, **overrides) -> ExternalPolicyConfiguration:
        return ExternalPolicyConfiguration(
            endpoint="https://policy.example.test/v1/data/iip/decision",
            **overrides,
        )

    def test_exact_input_and_versioned_decision_cross_the_adapter(self) -> None:
        transport = RecordingTransport()
        policy = ExternalHttpPolicyDecisionPoint(self.configuration(), transport)

        decision = policy.decide(
            self.actor,
            "resource:read",
            {"tenantId": "tenant-a", "resourceUid": "res_" + "a" * 32},
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason_code, "policy.resource-read-allowed")
        self.assertEqual(
            decision.policy_snapshot_ref,
            "policy://tenant-a/snapshots/bundle-42",
        )
        self.assertEqual(
            transport.calls[0]["document"],
            {
                "input": {
                    "apiVersion": "iip.platform/v1alpha1",
                    "kind": "PolicyDecisionRequest",
                    "metadata": {
                        "tenantId": "tenant-a",
                        "actorId": "operator-a",
                    },
                    "spec": {
                        "action": "resource:read",
                        "roles": ["developer"],
                        "resource": {
                            "tenantId": "tenant-a",
                            "resourceUid": "res_" + "a" * 32,
                        },
                    },
                }
            },
        )

    def test_cross_tenant_malformed_and_unavailable_decisions_fail_closed(self) -> None:
        transport = RecordingTransport()
        policy = ExternalHttpPolicyDecisionPoint(self.configuration(), transport)

        cross_tenant = policy.decide(
            self.actor, "resource:read", {"tenantId": "tenant-b"}
        )
        self.assertFalse(cross_tenant.allowed)
        self.assertEqual(cross_tenant.reason_code, "policy.input-invalid")
        self.assertEqual(transport.calls, [])

        malformed_transport = RecordingTransport(response={"result": {"allowed": True}})
        malformed_policy = ExternalHttpPolicyDecisionPoint(
            self.configuration(), malformed_transport
        )
        malformed = malformed_policy.decide(
            self.actor, "resource:read", {"tenantId": "tenant-a"}
        )
        self.assertFalse(malformed.allowed)
        self.assertEqual(malformed.reason_code, "policy.unavailable")

        failed_policy = ExternalHttpPolicyDecisionPoint(
            self.configuration(), RecordingTransport(error=RuntimeError("provider-secret"))
        )
        failed = failed_policy.decide(
            self.actor, "resource:read", {"tenantId": "tenant-a"}
        )
        self.assertEqual(failed.reason_code, "policy.unavailable")
        self.assertNotIn("provider-secret", repr(failed))

    def test_wrong_input_digest_and_invalid_actor_fail_closed(self) -> None:
        response = {
            "result": {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "PolicyDecision",
                "metadata": {"tenantId": "tenant-a"},
                "spec": {
                    "allowed": True,
                    "inputDigest": "sha256:" + "0" * 64,
                    "reasonCode": "policy.resource-read-allowed",
                    "policySnapshotRef": "policy://tenant-a/snapshots/bundle-42",
                },
            }
        }
        transport = RecordingTransport(response=response)
        policy = ExternalHttpPolicyDecisionPoint(self.configuration(), transport)

        stale = policy.decide(
            self.actor, "resource:read", {"tenantId": "tenant-a"}
        )
        invalid_actor = policy.decide(
            ActorContext("anonymous", "tenant-a", ("developer",)),
            "resource:read",
            {"tenantId": "tenant-a"},
        )

        self.assertFalse(stale.allowed)
        self.assertEqual(stale.reason_code, "policy.unavailable")
        self.assertFalse(invalid_actor.allowed)
        self.assertEqual(invalid_actor.reason_code, "policy.input-invalid")
        self.assertEqual(len(transport.calls), 1)

    def test_bearer_token_is_read_per_request_and_not_stored_in_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "token"
            first = "a" * 64
            second = "b" * 64
            path.write_text(first, encoding="utf-8")
            transport = RecordingTransport()
            configuration = self.configuration(bearer_token_path=str(path))
            policy = ExternalHttpPolicyDecisionPoint(configuration, transport)

            policy.decide(self.actor, "resource:read", {"tenantId": "tenant-a"})
            path.write_text(second, encoding="utf-8")
            policy.decide(self.actor, "resource:read", {"tenantId": "tenant-a"})

            self.assertEqual(
                [call["headers"]["Authorization"] for call in transport.calls],
                [f"Bearer {first}", f"Bearer {second}"],
            )
            self.assertNotIn(first, repr(configuration))
            self.assertNotIn(second, repr(configuration))

    def test_configuration_rejects_plaintext_unknown_and_unbounded_values(self) -> None:
        valid = {"endpoint": "https://policy.example.test/v1/decision"}
        parsed = ExternalPolicyConfiguration.from_json(json.dumps(valid))
        self.assertEqual(parsed.timeout_seconds, 5)

        for invalid in (
            {"endpoint": "http://policy.example.test/v1/decision"},
            {**valid, "unknown": True},
            {**valid, "timeoutSeconds": 31},
            {**valid, "bearerTokenPath": "relative/token"},
        ):
            with self.assertRaises(PolicyConfigurationError):
                ExternalPolicyConfiguration.from_json(json.dumps(invalid))

    def test_runtime_composes_external_policy_only_when_explicitly_selected(self) -> None:
        token = "policy-composition-token-0123456789abcdef"
        identities = json.dumps(
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
        with patch.dict(
            os.environ,
            {
                "IIP_AUTH_IDENTITIES_JSON": identities,
                "IIP_POLICY_MODE": "external-http",
                "IIP_POLICY_CONFIG_JSON": json.dumps(
                    {"endpoint": "https://policy.example.test/v1/decision"}
                ),
            },
            clear=True,
        ):
            runtime = build_runtime_from_env()

        self.assertIsInstance(runtime.actions._policy, ExternalHttpPolicyDecisionPoint)


if __name__ == "__main__":
    unittest.main()
