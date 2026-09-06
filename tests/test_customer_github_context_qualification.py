from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from iip.adapters.github_context import GithubContextBackendError
from iip.application.ports import CredentialLease
from scripts import qualify_customer_credential_broker as broker_qualification
from scripts import qualify_customer_github_context as qualification
from scripts import validate_schemas


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/examples"
SCHEMAS = ROOT / "contracts/schemas"
DEPLOY = ROOT / "deploy/context"
REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc)
CONTENT = b"# API rollout\n\nVerify the immutable image before rollback.\n"
BLOB_SHA = hashlib.sha1(
    b"blob " + str(len(CONTENT)).encode("ascii") + b"\0" + CONTENT,
    usedforsecurity=False,
).hexdigest()


def document(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class ExactBroker:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.requests: list[object] = []

    def resolve(self, request):
        self.requests.append(request)
        if self.unavailable:
            raise RuntimeError("provider detail must not be retained")
        return CredentialLease(
            "bearer",
            "short-lived-github-token-0123456789abcdef",
            "2026-09-08T10:02:00Z",
        )


class GithubTransport:
    def __init__(self, *, wrong_blob: bool = False) -> None:
        self.wrong_blob = wrong_blob
        self.calls: list[dict[str, object]] = []

    def get(self, url, headers, **options):
        self.calls.append({"url": url, "headers": dict(headers), **options})
        response = {
            "type": "file",
            "encoding": "base64",
            "size": len(CONTENT),
            "path": "runbooks/api-rollout.md",
            "sha": "0" * 40 if self.wrong_blob else BLOB_SHA,
            "content": base64.b64encode(CONTENT).decode("ascii"),
        }
        return json.dumps(response).encode("utf-8")


class CustomerGithubContextContractTests(unittest.TestCase):
    def test_examples_are_schema_and_semantically_valid(self) -> None:
        profile = document(
            EXAMPLES / "customer-github-context-qualification-profile.json"
        )
        report = document(
            EXAMPLES / "customer-github-context-qualification-report.json"
        )
        profile_schema = document(
            SCHEMAS / "customer-github-context-qualification-profile.schema.json"
        )
        report_schema = document(
            SCHEMAS / "customer-github-context-qualification-report.schema.json"
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                profile_schema, profile, label="profile"
            ),
            [],
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                report_schema, report, label="report"
            ),
            [],
        )
        qualification.validate_profile(profile)
        qualification.validate_report_document(report)
        broker_qualification.validate_profile(
            document(
                EXAMPLES
                / "customer-credential-broker-qualification-profile-github-context.json"
            )
        )

    def test_profile_rejects_unsafe_endpoint_path_size_and_secret_fields(self) -> None:
        profile = document(
            EXAMPLES / "customer-github-context-qualification-profile.json"
        )
        mutations = (
            lambda value: value["spec"]["expected"].update(
                {"endpoint": "https://api.github.com"}
            ),
            lambda value: value["spec"]["expected"].update(
                {"path": "../secrets.txt"}
            ),
            lambda value: value["spec"]["objective"].update(
                {"maximumResponseBytes": 1024, "maximumDocumentBytes": 1024}
            ),
            lambda value: value["spec"].update({"bearerToken": "not-allowed"}),
        )
        for mutate in mutations:
            changed = copy.deepcopy(profile)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(
                qualification.CustomerGithubContextQualificationError
            ):
                qualification.validate_profile(changed)

    def test_report_recomputes_identity_summary_and_minimization(self) -> None:
        report = document(
            EXAMPLES / "customer-github-context-qualification-report.json"
        )
        mutations = (
            lambda value: value["spec"]["summary"].update({"passedChecks": 19}),
            lambda value: value["spec"]["measurements"].update(
                {"returnedDocumentCount": 0}
            ),
            lambda value: value["spec"]["checks"][0].update(
                {
                    "status": "failed",
                    "errorCode": "customer-github-context-qualification.wrong.failed",
                }
            ),
            lambda value: value["metadata"].update({"id": "cgcq_" + "0" * 32}),
            lambda value: value["spec"].update(
                {"content": "protected repository document"}
            ),
        )
        for mutate in mutations:
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(
                qualification.CustomerGithubContextQualificationError
            ):
                qualification.validate_report_document(changed)


class CustomerGithubContextBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = document(
            EXAMPLES / "customer-github-context-qualification-profile.json"
        )
        self.integration = document(
            DEPLOY / "customer-github-context-qualification-integrations.example.json"
        )
        self.broker_profile = document(
            EXAMPLES
            / "customer-credential-broker-qualification-profile-github-context.json"
        )

    def inputs(self, directory: Path) -> dict[str, Path]:
        paths = {
            "profile": directory / "profile.json",
            "integration": directory / "integration.json",
            "broker_profile": directory / "broker-profile.json",
            "broker_report": directory / "broker-report.json",
            "identity": directory / "workload-token",
            "broker_ca": directory / "broker-ca.pem",
            "github_ca": directory / "github-ca.pem",
            "report": directory / "github-report.json",
        }
        paths["profile"].write_text(json.dumps(self.profile), encoding="utf-8")
        paths["integration"].write_text(
            json.dumps(self.integration), encoding="utf-8"
        )
        paths["broker_profile"].write_text(
            json.dumps(self.broker_profile), encoding="utf-8"
        )
        paths["identity"].write_text("w" * 64 + "\n", encoding="ascii")
        paths["broker_ca"].write_text("broker-ca", encoding="ascii")
        paths["github_ca"].write_text("github-ca", encoding="ascii")
        for key in ("profile", "integration", "broker_profile", "identity"):
            os.chmod(paths[key], 0o600)
        broker_report = broker_qualification.build_report(
            revision=REVISION,
            profile=self.broker_profile,
            image_digest=IMAGE,
            ca_bundle_digest=qualification._digest_bytes(b"broker-ca"),
            started_at=NOW,
            completed_at=NOW,
            maximum_latency_milliseconds=10,
            minimum_remaining_lease_seconds=120,
            observations={key: True for key in broker_qualification.CHECK_IDS},
        )
        paths["broker_report"].write_text(
            json.dumps(broker_report, indent=2) + "\n", encoding="utf-8"
        )
        return paths

    def qualify(
        self,
        paths: dict[str, Path],
        *,
        broker: ExactBroker | None = None,
        transport: GithubTransport | None = None,
    ):
        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ), patch.object(
            broker_qualification,
            "_source_identity",
            return_value=(REVISION, False),
        ):
            return qualification.qualify(
                profile=self.profile,
                integration_configuration=self.integration,
                credential_broker_report_path=paths["broker_report"],
                credential_broker_profile_path=paths["broker_profile"],
                credential_broker_endpoint="https://credential-broker.example.com",
                credential_broker_workload_identity_token_path=paths["identity"],
                credential_broker_ca_bundle_path=paths["broker_ca"],
                github_ca_bundle_path=paths["github_ca"],
                image_digest=IMAGE,
                allow_context_observation=True,
                broker=broker or ExactBroker(),
                transport=transport or GithubTransport(),
                now=NOW,
            )

    def test_full_flow_uses_exact_authority_revision_and_minimized_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.inputs(Path(temporary))
            broker = ExactBroker()
            transport = GithubTransport()
            report = self.qualify(paths, broker=broker, transport=transport)

        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(len(broker.requests), 1)
        request = broker.requests[0]
        self.assertEqual(request.provider, "github")
        self.assertEqual(request.scopes, ("repository:contents:read",))
        self.assertEqual(
            transport.calls[0]["url"],
            "https://github.example.com/api/v3/repos/platform-team/operations/"
            "contents/runbooks/api-rollout.md?"
            "ref=0123456789abcdef0123456789abcdef01234567",
        )
        self.assertEqual(
            transport.calls[0]["headers"]["X-GitHub-Api-Version"], "2026-03-10"
        )
        encoded = json.dumps(report, sort_keys=True)
        for protected in (
            "github.example.com",
            "platform-team",
            "operations",
            "api-rollout.md",
            "tenant-a",
            "investigation-runtime",
            "short-lived-github-token",
            CONTENT.decode("utf-8"),
        ):
            self.assertNotIn(protected, encoded)

    def test_bad_provider_response_is_not_qualified_and_retains_no_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.inputs(Path(temporary))
            report = self.qualify(paths, transport=GithubTransport(wrong_blob=True))

        self.assertEqual(report["spec"]["status"], "not-qualified")
        self.assertEqual(report["spec"]["measurements"]["returnedDocumentCount"], 0)
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(checks["expected-git-blob"]["status"], "failed")
        self.assertNotIn("0000000000000000000000000000000000000000", json.dumps(report))
        qualification.validate_report_document(report)

    def test_crossed_integration_or_broker_authority_fails_before_github(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.inputs(Path(temporary))
            transport = GithubTransport()
            crossed = copy.deepcopy(self.integration)
            crossed["integrations"][0]["repositories"][0]["commitSha"] = "f" * 40
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ), self.assertRaises(
                qualification.CustomerGithubContextQualificationError
            ):
                qualification.qualify(
                    profile=self.profile,
                    integration_configuration=crossed,
                    credential_broker_report_path=paths["broker_report"],
                    credential_broker_profile_path=paths["broker_profile"],
                    credential_broker_endpoint="https://credential-broker.example.com",
                    credential_broker_workload_identity_token_path=paths["identity"],
                    credential_broker_ca_bundle_path=paths["broker_ca"],
                    github_ca_bundle_path=paths["github_ca"],
                    image_digest=IMAGE,
                    allow_context_observation=True,
                    broker=ExactBroker(),
                    transport=transport,
                    now=NOW,
                )
            self.assertEqual(transport.calls, [])

            for index, case in enumerate(self.broker_profile["spec"]["cases"]):
                if index != 2:
                    case["actorId"] = "other-runtime"
            paths = self.inputs(Path(temporary))
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ), patch.object(
                broker_qualification,
                "_source_identity",
                return_value=(REVISION, False),
            ), self.assertRaises(
                qualification.CustomerGithubContextQualificationError
            ):
                qualification.qualify(
                    profile=self.profile,
                    integration_configuration=self.integration,
                    credential_broker_report_path=paths["broker_report"],
                    credential_broker_profile_path=paths["broker_profile"],
                    credential_broker_endpoint="https://credential-broker.example.com",
                    credential_broker_workload_identity_token_path=paths["identity"],
                    credential_broker_ca_bundle_path=paths["broker_ca"],
                    github_ca_bundle_path=paths["github_ca"],
                    image_digest=IMAGE,
                    allow_context_observation=True,
                    broker=ExactBroker(),
                    transport=transport,
                    now=NOW,
                )
            self.assertEqual(transport.calls, [])

    def test_protected_files_clean_source_and_explicit_enable_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.inputs(Path(temporary))
            os.chmod(paths["profile"], 0o644)
            with self.assertRaises(
                qualification.CustomerGithubContextQualificationError
            ):
                qualification.load_profile(paths["profile"])
            os.chmod(paths["profile"], 0o600)
            os.chmod(paths["identity"], 0o644)
            with self.assertRaisesRegex(
                qualification.CustomerGithubContextQualificationError,
                "credential-broker-identity.invalid",
            ):
                qualification.qualify(
                    profile=self.profile,
                    integration_configuration=self.integration,
                    credential_broker_report_path=paths["broker_report"],
                    credential_broker_profile_path=paths["broker_profile"],
                    credential_broker_endpoint="https://credential-broker.example.com",
                    credential_broker_workload_identity_token_path=paths["identity"],
                    credential_broker_ca_bundle_path=paths["broker_ca"],
                    github_ca_bundle_path=paths["github_ca"],
                    image_digest=IMAGE,
                    allow_context_observation=True,
                    broker=ExactBroker(),
                    transport=GithubTransport(),
                    now=NOW,
                )
            os.chmod(paths["identity"], 0o600)
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, True)
            ), self.assertRaises(
                qualification.CustomerGithubContextQualificationError
            ):
                qualification.qualify(
                    profile=self.profile,
                    integration_configuration=self.integration,
                    credential_broker_report_path=paths["broker_report"],
                    credential_broker_profile_path=paths["broker_profile"],
                    credential_broker_endpoint="https://credential-broker.example.com",
                    credential_broker_workload_identity_token_path=paths["identity"],
                    credential_broker_ca_bundle_path=paths["broker_ca"],
                    github_ca_bundle_path=paths["github_ca"],
                    image_digest=IMAGE,
                    allow_context_observation=True,
                    broker=ExactBroker(),
                    transport=GithubTransport(),
                    now=NOW,
                )
            with self.assertRaises(
                qualification.CustomerGithubContextQualificationError
            ):
                qualification.qualify(
                    profile=self.profile,
                    integration_configuration=self.integration,
                    credential_broker_report_path=paths["broker_report"],
                    credential_broker_profile_path=paths["broker_profile"],
                    credential_broker_endpoint="https://credential-broker.example.com",
                    credential_broker_workload_identity_token_path=paths["identity"],
                    credential_broker_ca_bundle_path=paths["broker_ca"],
                    github_ca_bundle_path=paths["github_ca"],
                    image_digest=IMAGE,
                    allow_context_observation=False,
                    broker=ExactBroker(),
                    transport=GithubTransport(),
                    now=NOW,
                )

    def test_offline_verifier_rebinds_every_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.inputs(Path(temporary))
            report = self.qualify(paths)
            paths["report"].write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ), patch.object(
                broker_qualification,
                "_source_identity",
                return_value=(REVISION, False),
            ):
                verified = qualification.verify_report(
                    report_path=paths["report"],
                    profile_path=paths["profile"],
                    integration_configuration_path=paths["integration"],
                    credential_broker_report_path=paths["broker_report"],
                    credential_broker_profile_path=paths["broker_profile"],
                    credential_broker_endpoint="https://credential-broker.example.com",
                    credential_broker_ca_bundle_path=paths["broker_ca"],
                    github_ca_bundle_path=paths["github_ca"],
                    image_digest=IMAGE,
                    require_qualified=True,
                )
            self.assertEqual(verified["spec"]["status"], "qualified")

            changed = copy.deepcopy(self.integration)
            changed["integrations"][0]["apiVersion"] = "2026-04-01"
            paths["integration"].write_text(json.dumps(changed), encoding="utf-8")
            os.chmod(paths["integration"], 0o600)
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ), patch.object(
                broker_qualification,
                "_source_identity",
                return_value=(REVISION, False),
            ), self.assertRaises(
                qualification.CustomerGithubContextQualificationError
            ):
                qualification.verify_report(
                    report_path=paths["report"],
                    profile_path=paths["profile"],
                    integration_configuration_path=paths["integration"],
                    credential_broker_report_path=paths["broker_report"],
                    credential_broker_profile_path=paths["broker_profile"],
                    credential_broker_endpoint="https://credential-broker.example.com",
                    credential_broker_ca_bundle_path=paths["broker_ca"],
                    github_ca_bundle_path=paths["github_ca"],
                    image_digest=IMAGE,
                    require_qualified=True,
                )


if __name__ == "__main__":
    unittest.main()
