from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import HTTPSHandler, ProxyHandler

from infra_intelligence_sdk import (
    AiPriceCatalog,
    AiPriceCatalogImportReport,
    AwsBedrockPriceCatalogImportPolicy,
)
from iip.adapters.aws_bedrock_price_catalog import (
    AwsBedrockPriceCatalogImportError,
    derive_aws_bedrock_price_catalog_import_policy_id,
    download_aws_bedrock_price_list,
    import_aws_bedrock_price_catalog,
    validate_aws_bedrock_price_catalog_import_policy,
    verify_aws_bedrock_price_catalog_import,
)
from iip.application.qualify_ai_price_catalog import qualify_ai_price_catalog


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"
SCHEMAS = ROOT / "contracts" / "schemas"
SOURCE = ROOT / "tests" / "fixtures" / "aws-bedrock-price-list.json"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reidentify_policy(policy: dict) -> None:
    identity = {
        "tenantId": policy["metadata"]["tenantId"],
        "version": policy["metadata"]["version"],
        "spec": policy["spec"],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    policy["metadata"]["id"] = "abp_" + digest[:32]


def source_bytes(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.status = 200
        self.headers = {"Content-Length": str(len(payload))}
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, maximum_bytes: int) -> bytes:
        if len(self._payload) >= maximum_bytes:
            return self._payload[:maximum_bytes]
        return self._payload


class _Opener:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def open(self, request: object, *, timeout: int) -> _Response:
        self.request = request
        self.timeout = timeout
        return _Response(self._payload)


class AwsBedrockPriceCatalogImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SOURCE.read_bytes()
        self.policy = load(
            EXAMPLES / "aws-bedrock-price-catalog-import-policy.json"
        )
        self.catalog = load(EXAMPLES / "ai-price-catalog-provider-published.json")
        self.report = load(EXAMPLES / "ai-price-catalog-import-report.json")

    def test_examples_validate_recalculate_and_round_trip(self) -> None:
        mappings = (
            (
                "aws-bedrock-price-catalog-import-policy",
                self.policy,
                AwsBedrockPriceCatalogImportPolicy,
            ),
            (
                "ai-price-catalog-import-report",
                self.report,
                AiPriceCatalogImportReport,
            ),
            (
                "ai-price-catalog",
                self.catalog,
                AiPriceCatalog,
            ),
        )
        for schema_name, document, model in mappings:
            with self.subTest(schema=schema_name):
                schema = load(SCHEMAS / f"{schema_name}.schema.json")
                self.assertEqual(
                    [],
                    validate_schemas.instance_validation_errors(
                        schema,
                        document,
                        label=schema_name,
                    ),
                )
                self.assertEqual(document, model.from_dict(document).to_dict())

        catalog, report = import_aws_bedrock_price_catalog(
            self.source,
            self.policy,
            retrieved_at="2026-09-05T10:00:00Z",
        )
        self.assertEqual(self.catalog, catalog)
        self.assertEqual(self.report, report)
        self.assertEqual(
            self.report,
            verify_aws_bedrock_price_catalog_import(
                self.source,
                self.policy,
                self.catalog,
                self.report,
            ),
        )

    def test_policy_identity_scope_direction_and_locator_are_fail_closed(self) -> None:
        cases: list[dict] = []

        altered_id = copy.deepcopy(self.policy)
        altered_id["metadata"]["id"] = "abp_" + "f" * 32
        cases.append(altered_id)

        extra = copy.deepcopy(self.policy)
        extra["spec"]["extra"] = True
        reidentify_policy(extra)
        cases.append(extra)

        unsafe_locator = copy.deepcopy(self.policy)
        unsafe_locator["spec"]["source"]["locator"] += "?token=secret"
        reidentify_policy(unsafe_locator)
        cases.append(unsafe_locator)

        wrong_direction = copy.deepcopy(self.policy)
        wrong_direction["spec"]["entries"][0]["rates"][
            "uncachedInputTokens"
        ]["expectedAttributes"]["inferenceType"] = "Output tokens"
        reidentify_policy(wrong_direction)
        cases.append(wrong_direction)

        wrong_region = copy.deepcopy(self.policy)
        wrong_region["spec"]["entries"][0]["region"] = "eu-west-1"
        reidentify_policy(wrong_region)
        cases.append(wrong_region)

        unsorted = copy.deepcopy(self.policy)
        second = copy.deepcopy(unsorted["spec"]["entries"][0])
        second["id"] = "aaa.unsorted-price"
        unsorted["spec"]["entries"].append(second)
        reidentify_policy(unsorted)
        cases.append(unsorted)

        for policy in cases:
            with self.subTest(policy=policy):
                with self.assertRaisesRegex(
                    AwsBedrockPriceCatalogImportError,
                    "ai.price-import.policy.invalid",
                ):
                    validate_aws_bedrock_price_catalog_import_policy(policy)

        placeholder = copy.deepcopy(self.policy)
        placeholder["metadata"]["id"] = "abp_" + "0" * 32
        self.assertEqual(
            self.policy["metadata"]["id"],
            derive_aws_bedrock_price_catalog_import_policy_id(placeholder),
        )

    def test_exact_source_mapping_and_decimal_conversion_are_fail_closed(self) -> None:
        base = json.loads(self.source)
        cases: list[dict] = []

        wrong_offer = copy.deepcopy(base)
        wrong_offer["offerCode"] = "AmazonS3"
        cases.append(wrong_offer)

        wrong_version = copy.deepcopy(base)
        wrong_version["version"] = "20260905090001"
        cases.append(wrong_version)

        future = copy.deepcopy(base)
        future["publicationDate"] = "2026-09-05T10:00:01Z"
        cases.append(future)

        wrong_attribute = copy.deepcopy(base)
        wrong_attribute["products"]["ABCD1234INPUT"]["attributes"][
            "model"
        ] = "Different Model"
        cases.append(wrong_attribute)

        wrong_unit = copy.deepcopy(base)
        wrong_unit["terms"]["OnDemand"]["ABCD1234INPUT"][
            "ABCD1234INPUT.JRTCKXETXF"
        ]["priceDimensions"]["ABCD1234INPUT.JRTCKXETXF.6YS6EN2CT7"][
            "unit"
        ] = "requests"
        cases.append(wrong_unit)

        fractional_subunit = copy.deepcopy(base)
        fractional_subunit["terms"]["OnDemand"]["ABCD1234INPUT"][
            "ABCD1234INPUT.JRTCKXETXF"
        ]["priceDimensions"]["ABCD1234INPUT.JRTCKXETXF.6YS6EN2CT7"][
            "pricePerUnit"
        ]["USD"] = "0.0000000000001"
        cases.append(fractional_subunit)

        overflow = copy.deepcopy(base)
        overflow["terms"]["OnDemand"]["ABCD1234INPUT"][
            "ABCD1234INPUT.JRTCKXETXF"
        ]["priceDimensions"]["ABCD1234INPUT.JRTCKXETXF.6YS6EN2CT7"][
            "pricePerUnit"
        ]["USD"] = "9007199254.740992"
        cases.append(overflow)

        for source in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    AwsBedrockPriceCatalogImportError,
                    "ai.price-import.source.invalid",
                ):
                    import_aws_bedrock_price_catalog(
                        source_bytes(source),
                        self.policy,
                        retrieved_at="2026-09-05T10:00:00Z",
                    )

    def test_million_token_unit_converts_without_float_or_rounding(self) -> None:
        source = json.loads(self.source)
        dimension = source["terms"]["OnDemand"]["ABCD1234INPUT"][
            "ABCD1234INPUT.JRTCKXETXF"
        ]["priceDimensions"]["ABCD1234INPUT.JRTCKXETXF.6YS6EN2CT7"]
        dimension["unit"] = "1M tokens"
        dimension["pricePerUnit"]["USD"] = "3.000000000"
        policy = copy.deepcopy(self.policy)
        policy["spec"]["entries"][0]["rates"]["uncachedInputTokens"][
            "expectedUnit"
        ] = "1M tokens"
        reidentify_policy(policy)
        catalog, _ = import_aws_bedrock_price_catalog(
            source_bytes(source),
            policy,
            retrieved_at="2026-09-05T10:00:00Z",
        )
        self.assertEqual(
            catalog["spec"]["entries"][0]["rates"]["uncachedInputTokens"][
                "priceSubunitsPerMillionTokens"
            ],
            3_000_000_000,
        )

    def test_current_acquisition_is_recorded_as_the_declared_immutable_version(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["spec"]["source"]["locator"] = (
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/"
            "AmazonBedrock/current/index.json"
        )
        reidentify_policy(policy)
        catalog, _ = import_aws_bedrock_price_catalog(
            self.source,
            policy,
            retrieved_at="2026-09-05T10:00:00Z",
        )
        self.assertEqual(
            self.catalog["spec"]["source"]["locator"],
            catalog["spec"]["source"]["locator"],
        )

    def test_report_is_minimized_and_tampering_is_rejected(self) -> None:
        encoded = json.dumps(self.report)
        for prohibited in (
            "ABCD1234INPUT",
            "example.foundation-model-v1:0",
            self.policy["spec"]["source"]["locator"],
            "priceSubunitsPerMillionTokens",
            "3000000000",
        ):
            self.assertNotIn(prohibited, encoded)

        changed_catalog = copy.deepcopy(self.catalog)
        changed_catalog["spec"]["entries"][0]["rates"][
            "uncachedInputTokens"
        ]["priceSubunitsPerMillionTokens"] += 1
        changed_report = copy.deepcopy(self.report)
        changed_report["spec"]["measurements"]["rateReferenceCount"] = 6
        for source, policy, catalog, report in (
            (self.source + b"\n", self.policy, self.catalog, self.report),
            (self.source, self.policy, changed_catalog, self.report),
            (self.source, self.policy, self.catalog, changed_report),
        ):
            with self.subTest(catalog=catalog, report=report):
                with self.assertRaisesRegex(
                    AwsBedrockPriceCatalogImportError,
                    "ai.price-import.report.invalid",
                ):
                    verify_aws_bedrock_price_catalog_import(
                        source,
                        policy,
                        catalog,
                        report,
                    )

    def test_imported_catalog_can_pass_the_separate_production_qualifier(self) -> None:
        qualification_policy = load(
            EXAMPLES / "ai-price-catalog-qualification-policy.json"
        )
        report = qualify_ai_price_catalog(
            self.catalog,
            qualification_policy,
            generated_at="2026-09-05T10:30:00Z",
            qualification_level="production-catalog",
        )
        self.assertEqual("qualified", report["spec"]["status"])

    def test_download_uses_no_proxy_no_redirect_and_verified_https(self) -> None:
        opener = _Opener(self.source)
        with patch(
            "iip.adapters.aws_bedrock_price_catalog.build_opener",
            return_value=opener,
        ) as build:
            self.assertEqual(
                self.source,
                download_aws_bedrock_price_list(self.policy, timeout_seconds=17),
            )
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(item, ProxyHandler) for item in handlers))
        self.assertTrue(any(isinstance(item, HTTPSHandler) for item in handlers))
        self.assertEqual(17, opener.timeout)
        self.assertEqual(
            self.policy["spec"]["source"]["locator"],
            opener.request.full_url,
        )
        with self.assertRaisesRegex(
            AwsBedrockPriceCatalogImportError,
            "ai.price-import.download.invalid",
        ):
            download_aws_bedrock_price_list(self.policy, timeout_seconds=0)

    def test_cli_generates_and_verifies_both_exact_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = Path(temporary) / "catalog.json"
            report = Path(temporary) / "report.json"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "import_aws_bedrock_price_catalog.py"),
            ]
            identified = subprocess.run(
                command
                + [
                    "policy-id",
                    "--policy",
                    str(
                        EXAMPLES
                        / "aws-bedrock-price-catalog-import-policy.json"
                    ),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, identified.returncode, identified.stderr)
            self.assertEqual(
                self.policy["metadata"]["id"],
                identified.stdout.strip(),
            )
            generated = subprocess.run(
                command
                + [
                    "generate",
                    "--source",
                    str(SOURCE),
                    "--policy",
                    str(
                        EXAMPLES
                        / "aws-bedrock-price-catalog-import-policy.json"
                    ),
                    "--catalog-output",
                    str(catalog),
                    "--report-output",
                    str(report),
                    "--retrieved-at",
                    "2026-09-05T10:00:00Z",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, generated.returncode, generated.stderr)
            self.assertEqual(self.catalog, load(catalog))
            self.assertEqual(self.report, load(report))
            verified = subprocess.run(
                command
                + [
                    "verify",
                    "--source",
                    str(SOURCE),
                    "--policy",
                    str(
                        EXAMPLES
                        / "aws-bedrock-price-catalog-import-policy.json"
                    ),
                    "--catalog",
                    str(catalog),
                    "--report",
                    str(report),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, verified.returncode, verified.stderr)

            collision = subprocess.run(
                command
                + [
                    "generate",
                    "--source",
                    str(SOURCE),
                    "--policy",
                    str(
                        EXAMPLES
                        / "aws-bedrock-price-catalog-import-policy.json"
                    ),
                    "--catalog-output",
                    str(SOURCE),
                    "--report-output",
                    str(report),
                    "--retrieved-at",
                    "2026-09-05T10:00:00Z",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, collision.returncode)
            self.assertEqual(self.source, SOURCE.read_bytes())


if __name__ == "__main__":
    unittest.main()
