from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from iip.application.ports import PolicyDecision
from scripts import qualify_customer_policy as qualification
from scripts import validate_schemas


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/examples"
SCHEMAS = ROOT / "contracts/schemas"
REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc)


def document(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class ExactDecisionPoint:
    def __init__(self, *, mismatch: str | None = None) -> None:
        self.calls: list[tuple[object, str, dict[str, object]]] = []
        self.mismatch = mismatch

    def decide(self, actor, action, resource):
        copied = dict(resource)
        self.calls.append((actor, action, copied))
        if action == "resource:read":
            decision = PolicyDecision(
                True,
                "policy.resource-read-allowed",
                "policy://tenant-a/snapshots/bundle-2026-09-07",
            )
        else:
            decision = PolicyDecision(
                False,
                "policy.separation-of-duties-denied",
                "policy://tenant-a/snapshots/bundle-2026-09-07",
            )
        if self.mismatch == "snapshot" and action == "action:execute":
            return PolicyDecision(
                decision.allowed,
                decision.reason_code,
                "policy://tenant-a/snapshots/bundle-older",
            )
        if self.mismatch == "unavailable":
            return PolicyDecision(
                False,
                "policy.unavailable",
                "policy://tenant-a/snapshots/unavailable",
            )
        return decision


class CustomerPolicyContractTests(unittest.TestCase):
    def test_examples_are_schema_and_semantically_valid(self) -> None:
        profile = document(EXAMPLES / "customer-policy-qualification-profile.json")
        report = document(EXAMPLES / "customer-policy-qualification-report.json")
        profile_schema = document(
            SCHEMAS / "customer-policy-qualification-profile.schema.json"
        )
        report_schema = document(
            SCHEMAS / "customer-policy-qualification-report.schema.json"
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

    def test_profile_requires_ordered_allow_and_deny_cases_on_one_snapshot(self) -> None:
        profile = document(EXAMPLES / "customer-policy-qualification-profile.json")
        for mutate in (
            lambda value: value["spec"]["cases"].reverse(),
            lambda value: value["spec"]["cases"].pop(),
            lambda value: value["spec"]["cases"][1]["expected"].update(
                {"policySnapshotRef": "policy://tenant-a/snapshots/other"}
            ),
            lambda value: value["spec"]["cases"][0]["resource"].update(
                {"accessToken": "not-allowed"}
            ),
        ):
            changed = copy.deepcopy(profile)
            mutate(changed)
            with self.assertRaises(qualification.CustomerPolicyQualificationError):
                qualification.validate_profile(changed)

    def test_report_recomputes_checks_summary_time_and_identity(self) -> None:
        report = document(EXAMPLES / "customer-policy-qualification-report.json")
        for mutate in (
            lambda value: value["spec"]["summary"].update({"passedChecks": 13}),
            lambda value: value["spec"]["measurements"].update(
                {"requestCount": 2}
            ),
            lambda value: value["spec"]["checks"][0].update(
                {"status": "failed", "errorCode": "customer-policy-qualification.wrong.failed"}
            ),
            lambda value: value["metadata"].update(
                {"id": "cpq_" + "0" * 32}
            ),
        ):
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.assertRaises(qualification.CustomerPolicyQualificationError):
                qualification.validate_report_document(changed)

    def test_report_rejects_sensitive_retained_fields(self) -> None:
        report = document(EXAMPLES / "customer-policy-qualification-report.json")
        report["spec"]["endpoint"] = "https://policy.example.com/v1/decision"
        with self.assertRaises(qualification.CustomerPolicyQualificationError):
            qualification.validate_report_document(report)


class CustomerPolicyBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = document(EXAMPLES / "customer-policy-qualification-profile.json")

    def protected_inputs(self, directory: Path) -> tuple[Path, Path, Path]:
        profile_path = directory / "profile.json"
        token_path = directory / "token"
        ca_path = directory / "ca.pem"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        token_path.write_text("p" * 64 + "\n", encoding="ascii")
        ca_path.write_text("test-ca", encoding="ascii")
        os.chmod(profile_path, 0o600)
        os.chmod(token_path, 0o600)
        return profile_path, token_path, ca_path

    def qualify(self, directory: Path, point: ExactDecisionPoint | None = None):
        _, token_path, ca_path = self.protected_inputs(directory)
        with patch.object(qualification, "_source_identity", return_value=(REVISION, False)):
            return qualification.qualify(
                profile=self.profile,
                endpoint="https://policy.example.com/v1/data/iip/decision",
                bearer_token_path=token_path,
                ca_bundle_path=ca_path,
                image_digest=IMAGE,
                allow_policy_observation=True,
                decision_point=point or ExactDecisionPoint(),
                now=NOW,
            )

    def test_full_flow_is_exact_bound_and_minimized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            point = ExactDecisionPoint()
            report = self.qualify(Path(temporary), point)

        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(len(point.calls), 3)
        self.assertEqual(point.calls[0][0].tenant_id, "tenant-a")
        self.assertEqual(point.calls[0][0].roles, ("approver", "developer"))
        self.assertEqual(point.calls[0][1], "resource:read")
        self.assertEqual(point.calls[-1][1], "resource:read")
        encoded = json.dumps(report, sort_keys=True)
        for protected in (
            "policy.example.com",
            "tenant-a",
            "qualification-operator",
            "resource:read",
            "policy.resource-read-allowed",
            "bundle-2026-09-07",
            "res_aaaaaaaa",
        ):
            self.assertNotIn(protected, encoded)

    def test_capability_mismatch_is_retained_as_not_qualified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self.qualify(Path(temporary), ExactDecisionPoint(mismatch="snapshot"))

        self.assertEqual(report["spec"]["status"], "not-qualified")
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(checks["snapshot-binding"]["status"], "failed")
        self.assertEqual(checks["deny-cases"]["status"], "failed")
        qualification.validate_report_document(report)

    def test_unavailable_service_never_qualifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self.qualify(Path(temporary), ExactDecisionPoint(mismatch="unavailable"))

        self.assertEqual(report["spec"]["status"], "not-qualified")
        checks = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(checks["ca-verified-tls"]["status"], "failed")
        self.assertEqual(checks["credential-accepted"]["status"], "failed")

    def test_explicit_enable_and_clean_source_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, token_path, ca_path = self.protected_inputs(directory)
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ):
                with self.assertRaises(qualification.CustomerPolicyQualificationError):
                    qualification.qualify(
                        profile=self.profile,
                        endpoint="https://policy.example.com/v1/data/iip/decision",
                        bearer_token_path=token_path,
                        ca_bundle_path=ca_path,
                        image_digest=IMAGE,
                        allow_policy_observation=False,
                        decision_point=ExactDecisionPoint(),
                        now=NOW,
                    )
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, True)
            ):
                with self.assertRaises(qualification.CustomerPolicyQualificationError):
                    qualification.qualify(
                        profile=self.profile,
                        endpoint="https://policy.example.com/v1/data/iip/decision",
                        bearer_token_path=token_path,
                        ca_bundle_path=ca_path,
                        image_digest=IMAGE,
                        allow_policy_observation=True,
                        decision_point=ExactDecisionPoint(),
                        now=NOW,
                    )

    def test_future_review_is_rejected_before_policy_calls(self) -> None:
        self.profile["metadata"]["reviewedAt"] = "2026-09-09T10:00:00Z"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, token_path, ca_path = self.protected_inputs(directory)
            point = ExactDecisionPoint()
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ):
                with self.assertRaises(qualification.CustomerPolicyQualificationError):
                    qualification.qualify(
                        profile=self.profile,
                        endpoint="https://policy.example.com/v1/data/iip/decision",
                        bearer_token_path=token_path,
                        ca_bundle_path=ca_path,
                        image_digest=IMAGE,
                        allow_policy_observation=True,
                        decision_point=point,
                        now=NOW,
                    )
            self.assertEqual(point.calls, [])

    def test_profile_and_token_require_mode_0600_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            profile_path, token_path, _ = self.protected_inputs(directory)
            os.chmod(profile_path, 0o644)
            with self.assertRaises(qualification.CustomerPolicyQualificationError):
                qualification.load_profile(profile_path)
            os.chmod(token_path, 0o644)
            with self.assertRaises(qualification.CustomerPolicyQualificationError):
                qualification.validate_bearer_file(token_path)
            symlink = directory / "token-link"
            symlink.symlink_to(token_path)
            with self.assertRaises(qualification.CustomerPolicyQualificationError):
                qualification.validate_bearer_file(symlink)

    def test_report_verifier_rebinds_profile_endpoint_image_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            profile_path, _, _ = self.protected_inputs(directory)
            report_path = directory / "report.json"
            report = json.loads(
                (EXAMPLES / "customer-policy-qualification-report.json").read_text(
                    encoding="utf-8"
                )
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
                    endpoint="https://policy.example.com/v1/data/iip/decision",
                    image_digest=IMAGE,
                    require_qualified=True,
                )
                self.assertEqual(verified["spec"]["status"], "qualified")
                with self.assertRaises(qualification.CustomerPolicyQualificationError):
                    qualification.verify_report(
                        report_path=report_path,
                        profile_path=profile_path,
                        endpoint="https://other.example.com/v1/data/iip/decision",
                        image_digest=IMAGE,
                    )
                crossed = copy.deepcopy(verified)
                crossed["spec"]["subject"]["applicationVersion"] = "9.9.9"
                metadata = dict(crossed["metadata"])
                metadata.pop("id")
                crossed["metadata"]["id"] = qualification._report_identifier(
                    metadata, crossed["spec"]
                )
                report_path.write_text(json.dumps(crossed), encoding="utf-8")
                with self.assertRaises(qualification.CustomerPolicyQualificationError):
                    qualification.verify_report(
                        report_path=report_path,
                        profile_path=profile_path,
                        endpoint="https://policy.example.com/v1/data/iip/decision",
                        image_digest=IMAGE,
                    )

    def test_report_verifier_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            profile_path, _, _ = self.protected_inputs(directory)
            report_path = directory / "report.json"
            report_path.write_text("{}", encoding="utf-8")
            link = directory / "report-link.json"
            link.symlink_to(report_path)
            with self.assertRaises(qualification.CustomerPolicyQualificationError):
                qualification.verify_report(
                    report_path=link,
                    profile_path=profile_path,
                    endpoint="https://policy.example.com/v1/data/iip/decision",
                    image_digest=IMAGE,
                )


if __name__ == "__main__":
    unittest.main()
