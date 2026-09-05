from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from infra_intelligence_sdk import (
    AiAllocationReport,
    AiAttributionPolicy,
    AiCostRecord,
    AiModelSuitabilityReport,
    AiPriceCatalog,
    AiPriceCatalogQualificationPolicy,
    AiPriceCatalogQualificationReport,
    AiSavingsFinding,
    AiUsageRecord,
    AiUsageAttributionRecord,
)
from iip.application.evaluate_ai_savings import validate_ai_savings_finding
from scripts.validate_repo import ROOT, validate_ai_economics_examples


EXAMPLES = ROOT / "contracts" / "examples"
SCHEMAS = ROOT / "contracts" / "schemas"
CONTRACTS = (
    ("ai-allocation-report", AiAllocationReport),
    ("ai-usage-record", AiUsageRecord),
    ("ai-attribution-policy", AiAttributionPolicy),
    ("ai-usage-attribution-record", AiUsageAttributionRecord),
    ("ai-price-catalog", AiPriceCatalog),
    (
        "ai-price-catalog-qualification-policy",
        AiPriceCatalogQualificationPolicy,
    ),
    (
        "ai-price-catalog-qualification-report",
        AiPriceCatalogQualificationReport,
    ),
    ("ai-cost-record", AiCostRecord),
    ("ai-model-suitability-report", AiModelSuitabilityReport),
    ("ai-savings-finding", AiSavingsFinding),
)


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def example_documents() -> dict[Path, object]:
    names = (
        "ai-usage-record.json",
        "ai-allocation-report.json",
        "ai-price-catalog.json",
        "aws-bedrock-price-catalog-import-policy.json",
        "ai-price-catalog-provider-published.json",
        "ai-price-catalog-import-report.json",
        "ai-price-catalog-qualification-policy.json",
        "ai-price-catalog-qualification-report.json",
        "ai-cost-record.json",
        "ai-savings-finding.json",
        "ai-usage-recorded-event.json",
        "ai-attribution-policy.json",
        "ai-usage-attribution-record.json",
        "ai-usage-attributed-event.json",
        "ai-cost-calculated-event.json",
        "ai-savings-finding-event.json",
        "ai-retry-savings-finding-event.json",
        "ai-expensive-model-savings-finding-event.json",
    )
    return {EXAMPLES / name: load(EXAMPLES / name) for name in names}


class AiEconomicsContractTests(unittest.TestCase):
    def test_examples_validate_and_sdk_models_round_trip(self) -> None:
        checker = FormatChecker()
        for basename, model_type in CONTRACTS:
            with self.subTest(contract=basename):
                schema = load(SCHEMAS / f"{basename}.schema.json")
                example = load(EXAMPLES / f"{basename}.json")
                errors = list(
                    Draft202012Validator(
                        schema,
                        format_checker=checker,
                    ).iter_errors(example)
                )
                self.assertEqual([], errors)
                self.assertEqual(example, model_type.from_dict(example).to_dict())

        errors: list[str] = []
        validate_ai_economics_examples(example_documents(), errors)
        self.assertEqual([], errors)
        validate_ai_savings_finding(load(EXAMPLES / "ai-savings-finding.json"))
        retry = load(EXAMPLES / "ai-retry-savings-finding.json")
        schema = load(SCHEMAS / "ai-savings-finding.schema.json")
        self.assertEqual(
            [],
            list(
                Draft202012Validator(
                    schema,
                    format_checker=checker,
                ).iter_errors(retry)
            ),
        )
        self.assertEqual(retry, AiSavingsFinding.from_dict(retry).to_dict())
        validate_ai_savings_finding(retry)
        expensive = load(EXAMPLES / "ai-expensive-model-savings-finding.json")
        self.assertEqual(
            [],
            list(
                Draft202012Validator(
                    schema,
                    format_checker=checker,
                ).iter_errors(expensive)
            ),
        )
        self.assertEqual(
            expensive,
            AiSavingsFinding.from_dict(expensive).to_dict(),
        )
        validate_ai_savings_finding(expensive)

    def test_usage_contract_prohibits_content_and_raw_payload_capture(self) -> None:
        schema = load(SCHEMAS / "ai-usage-record.schema.json")
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        example = load(EXAMPLES / "ai-usage-record.json")

        content_enabled = copy.deepcopy(example)
        content_enabled["spec"]["privacy"]["contentCaptured"] = True
        self.assertTrue(list(validator.iter_errors(content_enabled)))

        prompt_added = copy.deepcopy(example)
        prompt_added["spec"]["invocation"]["prompt"] = "do not persist this"
        self.assertTrue(list(validator.iter_errors(prompt_added)))

    def test_semantic_validation_rejects_double_counted_token_subsets(self) -> None:
        documents = example_documents()
        usage = copy.deepcopy(documents[EXAMPLES / "ai-usage-record.json"])
        usage["spec"]["usage"]["cacheReadInputTokens"] = 2301
        documents[EXAMPLES / "ai-usage-record.json"] = usage

        errors: list[str] = []
        validate_ai_economics_examples(documents, errors)
        self.assertIn("AI usage cache token subsets exceed inputTokens", errors)

    def test_semantic_validation_rejects_unsupported_cost_arithmetic(self) -> None:
        documents = example_documents()
        cost = copy.deepcopy(documents[EXAMPLES / "ai-cost-record.json"])
        cost["spec"]["result"]["totalSubunits"] += 1
        documents[EXAMPLES / "ai-cost-record.json"] = cost

        errors: list[str] = []
        validate_ai_economics_examples(documents, errors)
        self.assertIn("AI cost totalSubunits must equal its line amounts", errors)

    def test_catalog_example_is_explicitly_non_production(self) -> None:
        catalog = load(EXAMPLES / "ai-price-catalog.json")
        self.assertEqual("test-fixture", catalog["spec"]["source"]["kind"])


if __name__ == "__main__":
    unittest.main()
