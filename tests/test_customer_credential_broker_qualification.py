from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from iip.adapters.credential_broker import CredentialBrokerUnavailableError
from iip.application.ports import CredentialLease
from scripts import qualify_customer_credential_broker as qualification
from scripts import validate_schemas


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/examples"
SCHEMAS = ROOT / "contracts/schemas"
REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc)


def document(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class ExactBroker:
    def __init__(self, *, mismatch: str | None = None) -> None:
        self.requests: list[object] = []
        self.mismatch = mismatch

    def resolve(self, request):
        self.requests.append(request)
        exact = (
            request.tenant_id == "tenant-a"
            and request.actor_id == "investigation-runtime"
            and request.integration_id == "kubernetes-production"
            and request.credential_ref
            == "credential://kubernetes/production/events-reader"
            and request.provider == "kubernetes"
            and request.scopes == ("events:read", "resources:read")
        )
        if self.mismatch == "unavailable":
            raise CredentialBrokerUnavailableError(
                "credential.broker.upstream.unavailable"
            )
        if exact and self.mismatch != "exact-denied":
            expiry = datetime.fromisoformat(
                request.deadline.replace("Z", "+00:00")
            ) + timedelta(seconds=60)
            if self.mismatch == "invalid-lease":
                expiry = NOW + timedelta(seconds=10)
            return CredentialLease(
                "bearer",
                "short-lived-provider-token-0123456789abcdef",
                expiry.isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
        if self.mismatch == "scope-issued" and request.scopes[-1] == "workloads:patch":
            expiry = datetime.fromisoformat(
                request.deadline.replace("Z", "+00:00")
            ) + timedelta(seconds=60)
            return CredentialLease(
                "bearer",
                "scope-escalated-provider-token-0123456789abcdef",
                expiry.isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
        raise CredentialBrokerUnavailableError(
            "credential.broker.request.denied"
        )


class CustomerCredentialBrokerContractTests(unittest.TestCase):
    def test_examples_are_schema_and_semantically_valid(self) -> None:
        profile = document(
            EXAMPLES / "customer-credential-broker-qualification-profile.json"
        )
        report = document(
            EXAMPLES / "customer-credential-broker-qualification-report.json"
        )
        profile_schema = document(
            SCHEMAS / "customer-credential-broker-qualification-profile.schema.json"
        )
        report_schema = document(
            SCHEMAS / "customer-credential-broker-qualification-report.schema.json"
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

    def test_profile_requires_closed_single_field_authority_denials(self) -> None:
        profile = document(
            EXAMPLES / "customer-credential-broker-qualification-profile.json"
        )
        mutations = (
            lambda value: value["spec"]["cases"].reverse(),
            lambda value: value["spec"]["cases"][1].update(
                {"actorId": "also-changed"}
            ),
            lambda value: value["spec"]["cases"][5].update(
                {"scopes": ["workloads:patch"]}
            ),
            lambda value: value["spec"]["objective"].update(
                {"maximumLeaseSeconds": 60, "leaseRequestDeadlineSeconds": 120}
            ),
            lambda value: value["spec"].update({"bearerToken": "not-allowed"}),
        )
        for mutate in mutations:
            changed = copy.deepcopy(profile)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(
                qualification.CustomerCredentialBrokerQualificationError
            ):
                qualification.validate_profile(changed)

    def test_report_recomputes_checks_summary_time_counts_and_identity(self) -> None:
        report = document(
            EXAMPLES / "customer-credential-broker-qualification-report.json"
        )
        mutations = (
            lambda value: value["spec"]["summary"].update({"passedChecks": 17}),
            lambda value: value["spec"]["measurements"].update({"requestCount": 7}),
            lambda value: value["spec"]["checks"][0].update(
                {
                    "status": "failed",
                    "errorCode": "customer-credential-broker-qualification.wrong.failed",
                }
            ),
            lambda value: value["metadata"].update(
                {"id": "ccbq_" + "0" * 32}
            ),
        )
        for mutate in mutations:
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(
                qualification.CustomerCredentialBrokerQualificationError
            ):
                qualification.validate_report_document(changed)

    def test_report_rejects_sensitive_retained_fields(self) -> None:
        report = document(
            EXAMPLES / "customer-credential-broker-qualification-report.json"
        )
        report["spec"]["endpoint"] = "https://credential-broker.example.com"
        with self.assertRaises(
            qualification.CustomerCredentialBrokerQualificationError
        ):
            qualification.validate_report_document(report)


class CustomerCredentialBrokerBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = document(
            EXAMPLES / "customer-credential-broker-qualification-profile.json"
        )

    def protected_inputs(self, directory: Path) -> tuple[Path, Path, Path]:
        profile_path = directory / "profile.json"
        identity_path = directory / "workload-token"
        ca_path = directory / "ca.pem"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        identity_path.write_text("w" * 64 + "\n", encoding="ascii")
        ca_path.write_text("test-ca", encoding="ascii")
        os.chmod(profile_path, 0o600)
        os.chmod(identity_path, 0o600)
        return profile_path, identity_path, ca_path

    def qualify(self, directory: Path, point: ExactBroker | None = None):
        _, identity_path, ca_path = self.protected_inputs(directory)
        with patch.object(
            qualification, "_source_identity", return_value=(REVISION, False)
        ):
            return qualification.qualify(
                profile=self.profile,
                endpoint="https://credential-broker.example.com",
                workload_identity_token_path=identity_path,
                ca_bundle_path=ca_path,
                image_digest=IMAGE,
                allow_credential_observation=True,
                broker=point or ExactBroker(),
                now=NOW,
            )

    def test_full_flow_is_exact_bound_and_minimized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            point = ExactBroker()
            report = self.qualify(Path(temporary), point)
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(len(point.requests), 8)
        encoded = json.dumps(report, sort_keys=True)
        for protected in (
            "credential-broker.example.com",
            "tenant-a",
            "tenant-b",
            "investigation-runtime",
            "kubernetes-production",
            "events:read",
            "events-reader",
            "short-lived-provider-token",
        ):
            self.assertNotIn(protected, encoded)

    def test_authority_or_lease_mismatch_is_retained_as_not_qualified(self) -> None:
        for mismatch, expected_check in (
            ("scope-issued", "scope-escalation-denied"),
            ("invalid-lease", "lease-bounds"),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                report = self.qualify(Path(temporary), ExactBroker(mismatch=mismatch))
            checks = {item["id"]: item for item in report["spec"]["checks"]}
            self.assertEqual(report["spec"]["status"], "not-qualified")
            self.assertEqual(checks[expected_check]["status"], "failed")
            qualification.validate_report_document(report)

    def test_unavailable_broker_never_qualifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self.qualify(
                Path(temporary), ExactBroker(mismatch="unavailable")
            )
        self.assertEqual(report["spec"]["status"], "not-qualified")
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(checks["ca-verified-tls"]["status"], "failed")
        self.assertEqual(checks["workload-identity-accepted"]["status"], "failed")

    def test_upstream_failures_do_not_count_as_authority_denials(self) -> None:
        class IntermittentBroker(ExactBroker):
            def resolve(self, request):
                try:
                    return super().resolve(request)
                except CredentialBrokerUnavailableError:
                    raise CredentialBrokerUnavailableError(
                        "credential.broker.upstream.unavailable"
                    ) from None

        with tempfile.TemporaryDirectory() as temporary:
            report = self.qualify(Path(temporary), IntermittentBroker())
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(report["spec"]["status"], "not-qualified")
        self.assertEqual(checks["cross-tenant-denied"]["status"], "failed")
        self.assertEqual(checks["scope-escalation-denied"]["status"], "failed")

    def test_explicit_enable_clean_source_and_nonfuture_review_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, identity_path, ca_path = self.protected_inputs(directory)
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ):
                with self.assertRaises(
                    qualification.CustomerCredentialBrokerQualificationError
                ):
                    qualification.qualify(
                        profile=self.profile,
                        endpoint="https://credential-broker.example.com",
                        workload_identity_token_path=identity_path,
                        ca_bundle_path=ca_path,
                        image_digest=IMAGE,
                        allow_credential_observation=False,
                        broker=ExactBroker(),
                        now=NOW,
                    )
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, True)
            ):
                with self.assertRaises(
                    qualification.CustomerCredentialBrokerQualificationError
                ):
                    qualification.qualify(
                        profile=self.profile,
                        endpoint="https://credential-broker.example.com",
                        workload_identity_token_path=identity_path,
                        ca_bundle_path=ca_path,
                        image_digest=IMAGE,
                        allow_credential_observation=True,
                        broker=ExactBroker(),
                        now=NOW,
                    )
            self.profile["metadata"]["reviewedAt"] = "2026-09-09T10:00:00Z"
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ):
                with self.assertRaises(
                    qualification.CustomerCredentialBrokerQualificationError
                ):
                    qualification.qualify(
                        profile=self.profile,
                        endpoint="https://credential-broker.example.com",
                        workload_identity_token_path=identity_path,
                        ca_bundle_path=ca_path,
                        image_digest=IMAGE,
                        allow_credential_observation=True,
                        broker=ExactBroker(),
                        now=NOW,
                    )

    def test_profile_and_identity_require_mode_0600_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            profile_path, identity_path, _ = self.protected_inputs(directory)
            os.chmod(profile_path, 0o644)
            with self.assertRaises(
                qualification.CustomerCredentialBrokerQualificationError
            ):
                qualification.load_profile(profile_path)
            os.chmod(identity_path, 0o644)
            with self.assertRaises(
                qualification.CustomerCredentialBrokerQualificationError
            ):
                qualification.validate_workload_identity_file(identity_path)
            symlink = directory / "identity-link"
            symlink.symlink_to(identity_path)
            with self.assertRaises(
                qualification.CustomerCredentialBrokerQualificationError
            ):
                qualification.validate_workload_identity_file(symlink)

    def test_report_verifier_rebinds_profile_endpoint_ca_image_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            profile_path, _, ca_path = self.protected_inputs(directory)
            report_path = directory / "report.json"
            report = json.loads(
                (
                    EXAMPLES
                    / "customer-credential-broker-qualification-report.json"
                ).read_text(encoding="utf-8")
            )
            report["spec"]["subject"]["applicationVersion"] = qualification.APPLICATION_VERSION
            metadata = dict(report["metadata"])
            metadata.pop("id")
            report["metadata"]["id"] = qualification._report_identifier(metadata, report["spec"])
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ):
                verified = qualification.verify_report(
                    report_path=report_path,
                    profile_path=profile_path,
                    endpoint="https://credential-broker.example.com",
                    ca_bundle_path=ca_path,
                    image_digest=IMAGE,
                    require_qualified=True,
                )
                self.assertEqual(verified["spec"]["status"], "qualified")
                ca_path.write_text("other-ca", encoding="ascii")
                with self.assertRaises(
                    qualification.CustomerCredentialBrokerQualificationError
                ):
                    qualification.verify_report(
                        report_path=report_path,
                        profile_path=profile_path,
                        endpoint="https://credential-broker.example.com",
                        ca_bundle_path=ca_path,
                        image_digest=IMAGE,
                    )

    def test_report_verifier_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            profile_path, _, ca_path = self.protected_inputs(directory)
            report_path = directory / "report.json"
            report_path.write_text("{}", encoding="utf-8")
            link = directory / "report-link.json"
            link.symlink_to(report_path)
            with self.assertRaises(
                qualification.CustomerCredentialBrokerQualificationError
            ):
                qualification.verify_report(
                    report_path=link,
                    profile_path=profile_path,
                    endpoint="https://credential-broker.example.com",
                    ca_bundle_path=ca_path,
                    image_digest=IMAGE,
                )


if __name__ == "__main__":
    unittest.main()
