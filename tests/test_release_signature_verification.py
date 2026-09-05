from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release_signature_verification as signatures


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"


class RecordingRunner:
    def __init__(
        self,
        *,
        version: str = "3.1.2",
        fail_role: str | None = None,
    ) -> None:
        self.selected_version = version
        self.fail_role = fail_role
        self.requests: list[dict[str, object]] = []

    def version(self) -> str:
        return self.selected_version

    def verify(self, **request: object) -> signatures.VerifiedSignature:
        self.requests.append(dict(request))
        if self.fail_role and self.fail_role in str(request["repository"]):
            raise signatures.ReleaseSignatureError(
                "release-signature.signature.rejected"
            )
        trust = request["trust"]
        assert isinstance(trust, dict)
        candidates = trust[
            "identities" if trust["mode"] == "keyless" else "keys"
        ]
        assert isinstance(candidates, list) and isinstance(candidates[0], dict)
        return signatures.VerifiedSignature(str(candidates[0]["id"]), 1)


def manifest() -> dict[str, object]:
    return json.loads((EXAMPLES / "release-manifest.json").read_text(encoding="utf-8"))


def policy() -> dict[str, object]:
    return json.loads(
        (EXAMPLES / "release-signature-policy.json").read_text(encoding="utf-8")
    )


def report(
    runner: RecordingRunner | None = None,
    selected_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    release = manifest()
    revision = release["metadata"]["revision"]  # type: ignore[index]
    return signatures.build_report(
        manifest=release,
        manifest_digest="sha256:" + "1" * 64,
        policy=selected_policy or policy(),
        runner=runner or RecordingRunner(),
        source_revision=revision,
        source_dirty=True,
        generated_at="2026-09-07T01:00:00Z",
        platform_name="darwin/arm64",
        python_version="3.14.3",
    )


class ReleaseSignaturePolicyTests(unittest.TestCase):
    def test_policy_is_closed_ordered_and_requires_current_cosign_profile(self) -> None:
        document = policy()
        signatures.validate_policy_document(document)

        for mutation in (
            lambda value: value.update({"unknown": True}),
            lambda value: value["spec"].update({"cosignVersion": "3.0.6"}),
            lambda value: value["spec"]["artifacts"].reverse(),
            lambda value: value["spec"]["artifacts"][1].update(
                {"repository": value["spec"]["artifacts"][0]["repository"]}
            ),
        ):
            changed = copy.deepcopy(document)
            mutation(changed)
            with self.subTest(changed=changed), self.assertRaises(
                signatures.ReleaseSignatureError
            ):
                signatures.validate_policy_document(changed)

    def test_production_gate_rejects_local_mode_and_placeholder_identity(self) -> None:
        with self.assertRaisesRegex(
            signatures.ReleaseSignatureError,
            "release-signature.policy.placeholder",
        ):
            signatures.validate_policy_document(policy(), promotion=True)

        local = policy()
        local["spec"] = {  # type: ignore[index]
            "profile": "local-public-key-v1",
            "cosignVersion": "3.1.2",
            "transparencyMode": "disabled-local-only",
            "artifacts": [
                {
                    "role": role,
                    "repository": f"registry.local/iip/{role}",
                    "trust": {
                        "mode": "public-key",
                        "keys": [
                            {
                                "id": "local-test-key",
                                "path": "/tmp/iip-cosign.pub",
                                "sha256": "sha256:" + "a" * 64,
                            }
                        ],
                    },
                }
                for role in signatures.ROLES
            ],
        }
        signatures.validate_policy_document(local)
        with self.assertRaisesRegex(
            signatures.ReleaseSignatureError,
            "release-signature.policy.not-promotable",
        ):
            signatures.validate_policy_document(local, promotion=True)


class ReleaseSignatureReportTests(unittest.TestCase):
    def test_exact_manifest_digests_are_verified_and_output_is_minimized(self) -> None:
        runner = RecordingRunner()
        document = report(runner)

        self.assertEqual(document["spec"]["status"], "verified")  # type: ignore[index]
        self.assertEqual(len(runner.requests), 2)
        release = manifest()
        expected = (
            release["spec"]["image"]["indexDigest"],  # type: ignore[index]
            release["spec"]["pluginMediationBridgeImage"]["indexDigest"],  # type: ignore[index]
        )
        for request, digest in zip(runner.requests, expected):
            self.assertEqual(request["digest"], digest)
            self.assertEqual(
                request["reference"],
                f"{request['repository']}@{digest}",
            )
            self.assertNotIn(":latest", str(request["reference"]))

        encoded = json.dumps(document, sort_keys=True)
        for artifact in policy()["spec"]["artifacts"]:  # type: ignore[index]
            self.assertNotIn(artifact["repository"], encoded)
            for identity in artifact["trust"]["identities"]:
                self.assertNotIn(identity["certificateIdentity"], encoded)
                self.assertNotIn(identity["certificateOidcIssuer"], encoded)
        signatures.validate_report_document(document)
        self.assertEqual(document["metadata"]["id"], signatures.report_id(document))  # type: ignore[index]

    def test_one_rejected_artifact_rejects_report_without_provider_text(self) -> None:
        document = report(RecordingRunner(fail_role="plugin-mediation-bridge"))

        self.assertEqual(document["spec"]["status"], "rejected")  # type: ignore[index]
        self.assertEqual(
            document["spec"]["artifacts"][1]["errorCode"],  # type: ignore[index]
            "release-signature.signature.rejected",
        )
        self.assertEqual(document["spec"]["summary"]["failedChecks"],  # type: ignore[index]
                         3)
        signatures.validate_report_document(document)

    def test_semantic_validation_rejects_summary_identity_and_order_tampering(self) -> None:
        document = report()
        for mutate in (
            lambda value: value["spec"]["summary"].update({"passedChecks": 8}),
            lambda value: value["spec"]["artifacts"].reverse(),
            lambda value: value["spec"]["checks"].reverse(),
            lambda value: value["metadata"].update(
                {"id": "rsv_" + "0" * 32}
            ),
        ):
            changed = copy.deepcopy(document)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(
                signatures.ReleaseSignatureError,
                "release-signature.report.invalid",
            ):
                signatures.validate_report_document(changed)

    def test_semantic_validation_rejects_rebound_source_and_tool_versions(self) -> None:
        document = report()
        mutations = (
            lambda value: value["spec"]["release"].update(
                {"revision": "f" * 40}
            ),
            lambda value: value["spec"]["environment"].update(
                {"cosignVersion": "3.1.3"}
            ),
        )
        for mutate in mutations:
            changed = copy.deepcopy(document)
            mutate(changed)
            changed["metadata"]["id"] = signatures.report_id(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(
                signatures.ReleaseSignatureError,
                "release-signature.report.invalid",
            ):
                signatures.validate_report_document(changed)

    def test_source_and_tool_version_mismatch_fail_closed(self) -> None:
        release = manifest()
        with self.assertRaisesRegex(
            signatures.ReleaseSignatureError,
            "release-signature.source.mismatch",
        ):
            signatures.build_report(
                manifest=release,
                manifest_digest="sha256:" + "1" * 64,
                policy=policy(),
                runner=RecordingRunner(),
                source_revision="f" * 40,
                source_dirty=False,
                generated_at="2026-09-07T01:00:00Z",
                platform_name="linux/amd64",
                python_version="3.12.10",
            )

        mismatched = report(RecordingRunner(version="3.1.3"))
        self.assertEqual(mismatched["spec"]["status"], "rejected")  # type: ignore[index]
        self.assertEqual(mismatched["spec"]["checks"][3]["errorCode"],  # type: ignore[index]
                         "release-signature.tool.version-mismatch")


class SubprocessCosignRunnerTests(unittest.TestCase):
    def test_keyless_command_preserves_claim_checks_and_exact_identity(self) -> None:
        selected_policy = policy()
        artifact = selected_policy["spec"]["artifacts"][0]  # type: ignore[index]
        repository = artifact["repository"]
        digest = "sha256:" + "a" * 64
        reference = f"{repository}@{digest}"
        identity = artifact["trust"]["identities"][0]
        output = [
            {
                "critical": {
                    "identity": {"docker-reference": repository},
                    "image": {"docker-manifest-digest": digest},
                    "type": "https://sigstore.dev/cosign/sign/v1",
                },
                "optional": {"GIT_VERSION": "v3.1.2"},
            }
        ]
        calls: list[tuple[str, ...]] = []

        def completed(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            del kwargs
            calls.append(command)
            if "version" in command:
                body = json.dumps({"gitVersion": "v3.1.2"}).encode()
            else:
                body = json.dumps(output).encode()
            return subprocess.CompletedProcess(command, 0, body, b"")

        runner = signatures.SubprocessCosignRunner("/opt/cosign")
        with patch("subprocess.run", side_effect=completed):
            self.assertEqual(runner.version(), "3.1.2")
            verified = runner.verify(
                reference=reference,
                repository=repository,
                digest=digest,
                trust=artifact["trust"],
                transparency_mode="required",
            )

        self.assertEqual(verified.signature_count, 1)
        command = calls[1]
        self.assertIn("--certificate-identity", command)
        self.assertIn(identity["certificateIdentity"], command)
        self.assertIn("--certificate-oidc-issuer", command)
        self.assertIn(identity["certificateOidcIssuer"], command)
        self.assertNotIn("--check-claims=false", command)
        self.assertFalse(any("insecure" in item for item in command))
        self.assertEqual(command[-1], reference)

    def test_output_must_bind_exact_repository_digest_and_payload_type(self) -> None:
        repository = "ghcr.io/acme/control-plane"
        digest = "sha256:" + "a" * 64
        reference = f"{repository}@{digest}"
        document = [
            {
                "critical": {
                    "identity": {"docker-reference": reference},
                    "image": {"docker-manifest-digest": digest},
                    "type": "https://sigstore.dev/cosign/sign/v1",
                },
                "optional": {},
            }
        ]
        self.assertEqual(
            signatures._validated_cosign_output(
                json.dumps(document).encode(),
                repository=repository,
                reference=reference,
                digest=digest,
            ),
            1,
        )

        mutations = (
            lambda value: value[0]["critical"]["identity"].update(
                {"docker-reference": "ghcr.io/acme/other"}
            ),
            lambda value: value[0]["critical"]["image"].update(
                {"docker-manifest-digest": "sha256:" + "b" * 64}
            ),
            lambda value: value[0]["critical"].update(
                {"type": "cosign container image signature"}
            ),
        )
        for mutate in mutations:
            changed = copy.deepcopy(document)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(
                signatures.ReleaseSignatureError,
                "release-signature.tool.output-invalid",
            ):
                signatures._validated_cosign_output(
                    json.dumps(changed).encode(),
                    repository=repository,
                    reference=reference,
                    digest=digest,
                )

    def test_local_public_key_is_digest_bound_before_cosign(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            public_key = Path(temporary) / "cosign.pub"
            public_key.write_text("fixture-public-key\n", encoding="utf-8")
            trust = {
                "mode": "public-key",
                "keys": [
                    {
                        "id": "local-test-key",
                        "path": str(public_key),
                        "sha256": "sha256:"
                        + hashlib.sha256(public_key.read_bytes()).hexdigest(),
                    }
                ],
            }
            with patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ("cosign",), 1, b"", b"sensitive provider error"
                ),
            ) as invoked, self.assertRaisesRegex(
                signatures.ReleaseSignatureError,
                "release-signature.signature.rejected",
            ):
                signatures.SubprocessCosignRunner().verify(
                    reference="registry.local/iip/control@sha256:" + "a" * 64,
                    repository="registry.local/iip/control",
                    digest="sha256:" + "a" * 64,
                    trust=trust,
                    transparency_mode="disabled-local-only",
                )
            command = invoked.call_args.args[0]
            self.assertIn("--insecure-ignore-tlog=true", command)
            self.assertNotIn("sensitive provider error", str(command))


class ReleaseSignatureDockerProfileTests(unittest.TestCase):
    def test_docker_profile_is_pinned_isolated_and_tests_tampering(self) -> None:
        script = (ROOT / "scripts" / "test_release_signatures.sh").read_text(
            encoding="utf-8"
        )

        self.assertRegex(script, r"cosign/cosign@sha256:[a-f0-9]{64}")
        self.assertIn("--network none", script)
        self.assertIn("--read-only", script)
        self.assertIn("--cap-drop ALL", script)
        self.assertIn("--security-opt no-new-privileges:true", script)
        self.assertIn("sign-blob", script)
        self.assertIn("verify-blob", script)
        self.assertIn("signing-config create", script)
        self.assertIn("--no-default-rekor", script)
        self.assertNotIn("--tlog-upload=false", script)
        self.assertIn("tampered", script)


if __name__ == "__main__":
    unittest.main()
