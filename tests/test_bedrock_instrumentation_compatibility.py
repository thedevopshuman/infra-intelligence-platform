from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_repo  # noqa: E402


class BedrockInstrumentationCompatibilityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.example = json.loads(
            (
                ROOT
                / "contracts"
                / "examples"
                / "bedrock-instrumentation-compatibility-report.json"
            ).read_text(encoding="utf-8")
        )

    def errors(self, document: object) -> list[str]:
        errors: list[str] = []
        validate_repo.validate_bedrock_instrumentation_compatibility_document(
            document, errors
        )
        return errors

    def test_example_has_closed_source_bound_offline_semantics(self) -> None:
        self.assertEqual(self.errors(self.example), [])
        self.assertEqual(
            self.example["spec"]["qualificationLevel"],
            "offline-sdk-interoperability",
        )
        self.assertFalse(self.example["spec"]["result"]["exactCostEligible"])

    def test_semantics_reject_live_overclaim_and_partial_exact_cost(self) -> None:
        live_overclaim = copy.deepcopy(self.example)
        live_overclaim["spec"]["result"]["liveProviderVerified"] = True
        self.assertTrue(self.errors(live_overclaim))

        priced_overclaim = copy.deepcopy(self.example)
        priced_overclaim["spec"]["result"]["exactCostEligible"] = True
        self.assertTrue(self.errors(priced_overclaim))

        reordered = copy.deepcopy(self.example)
        reordered["spec"]["checks"].reverse()
        self.assertTrue(self.errors(reordered))


class BedrockInstrumentationCompatibilityHarnessTests(unittest.TestCase):
    def test_dependencies_are_pinned_outside_the_product_runtime(self) -> None:
        requirements = (
            ROOT / "requirements" / "bedrock-compatibility.txt"
        ).read_text(encoding="utf-8")
        product = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("boto3==1.43.73", requirements)
        self.assertIn(
            "opentelemetry-instrumentation-botocore==0.65b0",
            requirements,
        )
        self.assertNotIn("boto3", product)
        self.assertNotIn("opentelemetry-instrumentation-botocore", product)

    def test_offline_runner_is_no_network_and_live_credentials_are_explicit(self) -> None:
        runner = (ROOT / "scripts" / "test_bedrock_instrumentation.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("--network none", runner)
        self.assertIn("IIP_BEDROCK_LIVE_TEST_ENABLED", runner)
        self.assertIn("AWS_SESSION_TOKEN", runner)
        self.assertIn("--read-only", runner)
        self.assertIn("--cap-drop ALL", runner)
        self.assertNotIn("/.aws", runner)

    def test_harness_uses_real_sdk_boundary_and_conservative_breakdowns(self) -> None:
        harness = (
            ROOT / "scripts" / "run_bedrock_instrumentation_compatibility.py"
        ).read_text(encoding="utf-8")

        self.assertIn("Stubber(client)", harness)
        self.assertIn("client.converse(**parameters)", harness)
        self.assertIn(
            'os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"',
            harness,
        )
        self.assertIn('"zeroWhenAbsent": []', harness)
        self.assertIn("BatchSpanProcessor", harness)


if __name__ == "__main__":
    unittest.main()
