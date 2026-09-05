from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from iip.application.qualify_ai_price_catalog import (
    AiPriceCatalogQualificationError,
    qualify_ai_price_catalog,
    validate_ai_price_catalog_qualification_policy,
    verify_ai_price_catalog_qualification_report,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"
SCHEMAS = ROOT / "contracts" / "schemas"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def fixture(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text())


def reidentify_policy(policy: dict) -> None:
    identity = {
        "tenantId": policy["metadata"]["tenantId"],
        "version": policy["metadata"]["version"],
        "spec": policy["spec"],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    policy["metadata"]["id"] = "apqp_" + digest[:32]


class AiPriceCatalogQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = fixture("ai-price-catalog")
        self.policy = fixture("ai-price-catalog-qualification-policy")
        self.report = fixture("ai-price-catalog-qualification-report")

    def test_examples_are_schema_valid_and_recalculate_exactly(self) -> None:
        for name in (
            "ai-price-catalog-qualification-policy",
            "ai-price-catalog-qualification-report",
        ):
            schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
            self.assertEqual(
                validate_schemas.instance_validation_errors(
                    schema,
                    fixture(name),
                    label=name,
                ),
                [],
            )
        self.assertEqual(
            qualify_ai_price_catalog(
                self.catalog,
                self.policy,
                generated_at="2026-09-05T10:00:00Z",
                qualification_level="offline-static",
            ),
            self.report,
        )
        self.assertEqual(
            verify_ai_price_catalog_qualification_report(
                self.report,
                self.catalog,
                self.policy,
                evaluated_at="2026-09-05T12:00:00Z",
            ),
            self.report,
        )

    def test_policy_identity_order_and_closed_shape_are_fail_closed(self) -> None:
        cases: list[dict] = []
        changed_id = copy.deepcopy(self.policy)
        changed_id["metadata"]["id"] = "apqp_" + "f" * 32
        cases.append(changed_id)
        extra = copy.deepcopy(self.policy)
        extra["spec"]["extra"] = True
        cases.append(extra)
        duplicate = copy.deepcopy(self.policy)
        duplicate["spec"]["requiredScopes"].append(
            copy.deepcopy(duplicate["spec"]["requiredScopes"][0])
        )
        reidentify_policy(duplicate)
        cases.append(duplicate)
        unsorted = copy.deepcopy(self.policy)
        second = copy.deepcopy(unsorted["spec"]["requiredScopes"][0])
        second["modelId"] = "aaa-model"
        unsorted["spec"]["requiredScopes"].append(second)
        reidentify_policy(unsorted)
        cases.append(unsorted)
        for policy in cases:
            with self.subTest(policy=policy):
                with self.assertRaisesRegex(
                    AiPriceCatalogQualificationError,
                    "ai.price-qualification.policy.invalid",
                ):
                    validate_ai_price_catalog_qualification_policy(policy)

    def test_production_profile_rejects_fixture_but_accepts_reviewed_source(self) -> None:
        fixture_report = qualify_ai_price_catalog(
            self.catalog,
            self.policy,
            generated_at="2026-09-05T10:00:00Z",
            qualification_level="production-catalog",
        )
        self.assertEqual(fixture_report["spec"]["status"], "unqualified")
        self.assertEqual(
            fixture_report["spec"]["checks"][0]["errorCode"],
            "ai.price-qualification.source-profile.invalid",
        )

        reviewed = copy.deepcopy(self.catalog)
        reviewed["spec"]["source"].update(
            {
                "kind": "operator-managed",
                "locator": "urn:customer:approved-ai-prices:2026-09-05",
            }
        )
        report = qualify_ai_price_catalog(
            reviewed,
            self.policy,
            generated_at="2026-09-05T10:00:00Z",
            qualification_level="production-catalog",
        )
        self.assertEqual(report["spec"]["status"], "qualified")

        reviewed["spec"]["source"].update(
            {
                "kind": "provider-published",
                "locator": "https://pricing.example.test:443/catalog.json",
            }
        )
        report = qualify_ai_price_catalog(
            reviewed,
            self.policy,
            generated_at="2026-09-05T10:00:00Z",
            qualification_level="production-catalog",
        )
        self.assertEqual(report["spec"]["status"], "qualified")

        for locator in (
            "urn:provider:prices:v1",
            "https://pricing.example.test:8443/catalog.json",
            "https://pricing.example.test/catalog.json?signature=secret",
        ):
            with self.subTest(locator=locator):
                invalid = copy.deepcopy(reviewed)
                invalid["spec"]["source"]["locator"] = locator
                result = qualify_ai_price_catalog(
                    invalid,
                    self.policy,
                    generated_at="2026-09-05T10:00:00Z",
                    qualification_level="production-catalog",
                )
                self.assertEqual(result["spec"]["status"], "unqualified")
                self.assertEqual(result["spec"]["checks"][0]["status"], "failed")

    def test_stale_future_and_bad_publication_order_are_explicit(self) -> None:
        stale = copy.deepcopy(self.catalog)
        stale["spec"]["source"]["retrievedAt"] = "2026-08-01T00:00:00Z"
        stale["metadata"]["publishedAt"] = "2026-08-01T00:01:00Z"
        report = qualify_ai_price_catalog(
            stale,
            self.policy,
            generated_at="2026-09-05T10:00:00Z",
            qualification_level="offline-static",
        )
        self.assertEqual(
            report["spec"]["checks"][1]["errorCode"],
            "ai.price-qualification.source.stale",
        )

        reversed_order = copy.deepcopy(self.catalog)
        reversed_order["metadata"]["publishedAt"] = "2026-09-05T08:54:59Z"
        report = qualify_ai_price_catalog(
            reversed_order,
            self.policy,
            generated_at="2026-09-05T10:00:00Z",
            qualification_level="offline-static",
        )
        self.assertEqual(
            report["spec"]["checks"][2]["errorCode"],
            "ai.price-qualification.publication-order.invalid",
        )

    def test_overlap_missing_and_ambiguous_scope_counts_are_recalculated(self) -> None:
        overlap = copy.deepcopy(self.catalog)
        duplicate = copy.deepcopy(overlap["spec"]["entries"][0])
        duplicate["id"] = "aws-bedrock.overlapping-price"
        overlap["spec"]["entries"].append(duplicate)
        report = qualify_ai_price_catalog(
            overlap,
            self.policy,
            generated_at="2026-09-05T10:00:00Z",
            qualification_level="offline-static",
        )
        measurements = report["spec"]["measurements"]
        self.assertEqual(measurements["overlappingEntryPairCount"], 1)
        self.assertEqual(measurements["ambiguousScopeCount"], 1)
        self.assertEqual(report["spec"]["checks"][3]["status"], "failed")
        self.assertEqual(report["spec"]["checks"][4]["status"], "failed")

        missing_policy = copy.deepcopy(self.policy)
        missing_policy["spec"]["requiredScopes"][0]["region"] = "eu-west-1"
        reidentify_policy(missing_policy)
        report = qualify_ai_price_catalog(
            self.catalog,
            missing_policy,
            generated_at="2026-09-05T10:00:00Z",
            qualification_level="offline-static",
        )
        self.assertEqual(report["spec"]["measurements"]["missingScopeCount"], 1)

    def test_report_tampering_expiry_and_wrong_sources_are_rejected(self) -> None:
        for mutation in (
            lambda value: value["spec"]["measurements"].update(
                {"coveredScopeCount": 0}
            ),
            lambda value: value["metadata"].update({"id": "apq_" + "f" * 32}),
            lambda value: value["spec"]["catalog"].update(
                {"sourceHash": "sha256:" + "f" * 64}
            ),
        ):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(self.report)
                mutation(changed)
                with self.assertRaisesRegex(
                    AiPriceCatalogQualificationError,
                    "ai.price-qualification.report.invalid",
                ):
                    verify_ai_price_catalog_qualification_report(
                        changed,
                        self.catalog,
                        self.policy,
                        evaluated_at="2026-09-05T12:00:00Z",
                    )
        with self.assertRaisesRegex(
            AiPriceCatalogQualificationError,
            "ai.price-qualification.report.invalid",
        ):
            verify_ai_price_catalog_qualification_report(
                self.report,
                self.catalog,
                self.policy,
                evaluated_at="2026-09-06T10:00:00Z",
            )

    def test_report_contains_no_rates_or_source_locator(self) -> None:
        encoded = json.dumps(self.report)
        self.assertNotIn("priceSubunitsPerMillionTokens", encoded)
        self.assertNotIn("locator", encoded)
        self.assertNotIn("example.foundation-model-v1", encoded)

    def test_cli_generates_and_verifies_exact_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "qualification.json"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "qualify_ai_price_catalog.py"),
                "generate",
                "--catalog",
                str(EXAMPLES / "ai-price-catalog.json"),
                "--policy",
                str(EXAMPLES / "ai-price-catalog-qualification-policy.json"),
                "--output",
                str(output),
                "--qualification-level",
                "offline-static",
                "--generated-at",
                "2026-09-05T10:00:00Z",
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output.read_text()), self.report)
            verified = subprocess.run(
                command[:2]
                + [
                    "verify",
                    "--report",
                    str(output),
                    "--catalog",
                    str(EXAMPLES / "ai-price-catalog.json"),
                    "--policy",
                    str(EXAMPLES / "ai-price-catalog-qualification-policy.json"),
                    "--evaluated-at",
                    "2026-09-05T12:00:00Z",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)


if __name__ == "__main__":
    unittest.main()
