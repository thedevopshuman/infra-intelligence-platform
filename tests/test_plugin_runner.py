"""Signed and isolated plugin runner boundary tests."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from iip.adapters.plugin_runner import (
    InMemoryPluginExecutionLedger,
    PluginRunnerError,
    PluginTrustStore,
    SignedDockerPluginRunner,
)
from iip.application.investigate import canonical_digest
from iip.application.ports import ActorContext


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 14, 12, 44, 40, tzinfo=timezone.utc)
TOKEN = "plugin-capability-token-0123456789abcdef"


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def signed_fixture() -> tuple[dict, PluginTrustStore]:
    manifest = example("plugin-manifest.json")
    manifest["spec"]["permissions"]["network"] = []
    manifest["spec"]["permissions"]["secrets"] = []
    private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"runner-test-key").digest())
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    artifact = manifest["spec"]["artifact"]
    signed = {
        "apiVersion": "iip.plugin-signature/v1",
        "pluginId": manifest["metadata"]["id"],
        "pluginVersion": manifest["metadata"]["version"],
        "protocolVersion": manifest["spec"]["protocolVersion"],
        "artifact": {
            "type": artifact["type"],
            "reference": artifact["reference"],
            "digest": artifact["digest"],
        },
    }
    payload = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
    artifact["signature"] = {
        "algorithm": "ed25519",
        "keyId": "runner-test-2026",
        "value": encoded(private.sign(payload)),
    }
    trust = PluginTrustStore.from_json(
        json.dumps(
            {
                "keys": [
                    {
                        "keyId": "runner-test-2026",
                        "publisher": manifest["metadata"]["publisher"],
                        "publicKey": encoded(public),
                    }
                ]
            }
        )
    )
    return manifest, trust


def scoped_documents(manifest: dict) -> tuple[dict, dict]:
    session = example("plugin-session.json")
    invocation = example("plugin-invocation.json")
    digest = canonical_digest(manifest)
    session["spec"]["manifestDigest"] = digest
    session["spec"]["capabilityTokenDigest"] = (
        "sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest()
    )
    invocation["spec"]["manifestDigest"] = digest
    return session, invocation


class RecordingTransport:
    def __init__(self, output: object | bytes = None) -> None:
        self.output = output or {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ResourceCollectionResult",
        }
        self.calls: list[dict[str, object]] = []

    def run(self, image_reference, payload, *, timeout_seconds, max_output_bytes):
        self.calls.append(
            {
                "image": image_reference,
                "invocation": json.loads(payload),
                "timeout": timeout_seconds,
                "maximum": max_output_bytes,
            }
        )
        if isinstance(self.output, bytes):
            return self.output
        return json.dumps(self.output, separators=(",", ":")).encode()


class FailingTransport(RecordingTransport):
    def run(self, image_reference, payload, *, timeout_seconds, max_output_bytes):
        super().run(
            image_reference,
            payload,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        raise PluginRunnerError("plugin.runtime.deadline-exceeded")


class LeakyTransport(RecordingTransport):
    def run(self, image_reference, payload, *, timeout_seconds, max_output_bytes):
        raise PluginRunnerError("provider secret and stack detail")


class SignedDockerPluginRunnerTests(unittest.TestCase):
    actor = ActorContext("plugin-host", "local", ("developer",))

    def runner(self, trust, transport=None, ledger=None):
        return SignedDockerPluginRunner(
            trust,
            transport=transport or RecordingTransport(),
            ledger=ledger,
            now=lambda: NOW,
            monotonic=lambda: 100.0,
        )

    def test_valid_signature_scope_and_token_produce_bounded_result(self) -> None:
        manifest, trust = signed_fixture()
        session, invocation = scoped_documents(manifest)
        transport = RecordingTransport()

        result = self.runner(trust, transport).run(
            self.actor, manifest, session, invocation, TOKEN
        )

        self.assertEqual(result["spec"]["status"], "succeeded")
        self.assertEqual(result["metadata"]["pluginVersion"], "0.3.0")
        self.assertEqual(transport.calls[0]["invocation"], invocation)
        self.assertTrue(
            transport.calls[0]["image"].endswith(manifest["spec"]["artifact"]["digest"])
        )
        self.assertNotIn(TOKEN, json.dumps(transport.calls))

    def test_signature_tampering_and_untrusted_publisher_fail_before_execution(self) -> None:
        manifest, trust = signed_fixture()
        session, invocation = scoped_documents(manifest)
        transport = RecordingTransport()
        manifest["metadata"]["version"] = "0.3.1"

        with self.assertRaisesRegex(PluginRunnerError, "signature.invalid"):
            self.runner(trust, transport).run(
                self.actor, manifest, session, invocation, TOKEN
            )

        manifest, trust = signed_fixture()
        session, invocation = scoped_documents(manifest)
        manifest["metadata"]["publisher"] = "unknown-publisher"
        with self.assertRaisesRegex(PluginRunnerError, "signature.untrusted"):
            self.runner(trust, transport).run(
                self.actor, manifest, session, invocation, TOKEN
            )
        self.assertEqual(transport.calls, [])

    def test_network_secret_and_action_permissions_are_denied(self) -> None:
        for permission, value in (
            ("network", ["kubernetes.default.svc:443"]),
            ("secrets", ["kubernetes-token"]),
            ("actions", ["workload:restart"]),
        ):
            with self.subTest(permission=permission):
                manifest, trust = signed_fixture()
                manifest["spec"]["permissions"][permission] = value
                session, invocation = scoped_documents(manifest)
                with self.assertRaisesRegex(PluginRunnerError, "permission.unsupported"):
                    self.runner(trust).run(
                        self.actor, manifest, session, invocation, TOKEN
                    )

    def test_wrong_token_cross_tenant_expiry_and_unknown_method_fail_closed(self) -> None:
        manifest, trust = signed_fixture()
        for mutation, code in (
            (lambda s, i: s["spec"].update(capabilityTokenDigest="sha256:" + "0" * 64), "request.invalid"),
            (lambda s, i: i["metadata"].update(tenantId="tenant-b"), "request.invalid"),
            (lambda s, i: i["metadata"].update(deadline="2026-08-14T12:44:39Z"), "request.expired"),
            (lambda s, i: i["spec"].update(method="delete"), "method.not-declared"),
        ):
            session, invocation = scoped_documents(manifest)
            mutation(session, invocation)
            with self.subTest(code=code):
                with self.assertRaisesRegex(PluginRunnerError, code):
                    self.runner(trust).run(
                        self.actor, manifest, session, invocation, TOKEN
                    )

    def test_completed_request_is_replayed_without_duplicate_execution(self) -> None:
        manifest, trust = signed_fixture()
        session, invocation = scoped_documents(manifest)
        ledger = InMemoryPluginExecutionLedger()
        transport = RecordingTransport()
        runner = self.runner(trust, transport, ledger)
        first = runner.run(self.actor, manifest, session, invocation, TOKEN)
        second = runner.run(self.actor, manifest, session, invocation, TOKEN)

        self.assertEqual(second, first)
        self.assertEqual(len(transport.calls), 1)

        after_expiry_transport = RecordingTransport()
        after_expiry = SignedDockerPluginRunner(
            trust,
            transport=after_expiry_transport,
            ledger=ledger,
            now=lambda: datetime(2026, 8, 14, 12, 50, tzinfo=timezone.utc),
        ).run(self.actor, manifest, session, invocation, TOKEN)
        self.assertEqual(after_expiry, first)
        self.assertEqual(after_expiry_transport.calls, [])

        conflicting = copy.deepcopy(invocation)
        conflicting["spec"]["input"]["spec"]["startSequence"] = 43
        with self.assertRaisesRegex(PluginRunnerError, "request.conflict"):
            runner.run(self.actor, manifest, session, conflicting, TOKEN)

    def test_ambiguous_claim_is_not_replayed_and_session_limit_is_atomic(self) -> None:
        manifest, trust = signed_fixture()
        session, invocation = scoped_documents(manifest)
        ledger = InMemoryPluginExecutionLedger()
        ledger.claim_plugin_invocation(
            self.actor,
            session,
            invocation,
            canonical_digest(invocation),
            "2026-08-14T12:44:40Z",
        )
        transport = RecordingTransport()

        with self.assertRaisesRegex(PluginRunnerError, "reconciliation-required"):
            self.runner(trust, transport, ledger).run(
                self.actor, manifest, session, invocation, TOKEN
            )
        self.assertEqual(transport.calls, [])

        next_invocation = copy.deepcopy(invocation)
        next_invocation["metadata"]["id"] = "pin_88888888888888888888888888888888"
        with self.assertRaisesRegex(PluginRunnerError, "limit-exceeded"):
            self.runner(trust, transport, ledger).run(
                self.actor, manifest, session, next_invocation, TOKEN
            )

    def test_runtime_and_output_failures_become_durable_terminal_results(self) -> None:
        manifest, trust = signed_fixture()
        session, invocation = scoped_documents(manifest)
        ledger = InMemoryPluginExecutionLedger()
        transport = FailingTransport()
        runner = self.runner(trust, transport, ledger)

        failed = runner.run(self.actor, manifest, session, invocation, TOKEN)
        replayed = runner.run(self.actor, manifest, session, invocation, TOKEN)

        self.assertEqual(failed, replayed)
        self.assertEqual(failed["spec"]["status"], "failed")
        self.assertEqual(
            failed["spec"]["error"]["code"],
            "plugin.runtime.deadline-exceeded",
        )
        self.assertNotIn("outputDigest", failed["spec"])
        self.assertEqual(len(transport.calls), 1)

        session, invocation = scoped_documents(manifest)
        invalid = self.runner(trust, RecordingTransport(b"[]"))
        invalid_result = invalid.run(
            self.actor, manifest, session, invocation, TOKEN
        )
        self.assertEqual(invalid_result["spec"]["status"], "failed")
        self.assertEqual(invalid_result["spec"]["error"]["code"], "plugin.output.invalid")

        session, invocation = scoped_documents(manifest)
        leaky = self.runner(trust, LeakyTransport()).run(
            self.actor, manifest, session, invocation, TOKEN
        )
        self.assertEqual(leaky["spec"]["error"]["code"], "plugin.runtime.failed")
        self.assertNotIn("secret", json.dumps(leaky))

    def test_trust_configuration_is_closed_and_key_ids_are_unique(self) -> None:
        _, trust = signed_fixture()
        self.assertIsInstance(trust, PluginTrustStore)
        for document in (
            {},
            {"keys": [], "unknown": True},
            {
                "keys": [
                    {"keyId": "same", "publisher": "a", "publicKey": "bad"},
                    {"keyId": "same", "publisher": "b", "publicKey": "bad"},
                ]
            },
        ):
            with self.assertRaisesRegex(PluginRunnerError, "configuration-invalid"):
                PluginTrustStore.from_json(json.dumps(document))


if __name__ == "__main__":
    unittest.main()
