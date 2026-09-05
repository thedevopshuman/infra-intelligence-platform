from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_repo  # noqa: E402


class OpenAIInstrumentationCompatibilityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.example = json.loads(
            (
                ROOT
                / "contracts"
                / "examples"
                / "openai-instrumentation-compatibility-report.json"
            ).read_text(encoding="utf-8")
        )

    def errors(self, document: object) -> list[str]:
        errors: list[str] = []
        validate_repo.validate_openai_instrumentation_compatibility_document(
            document,
            errors,
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


class OpenAIInstrumentationCompatibilityHarnessTests(unittest.TestCase):
    def test_dependencies_are_pinned_outside_the_product_runtime(self) -> None:
        requirements = (
            ROOT / "requirements" / "openai-compatibility.txt"
        ).read_text(encoding="utf-8")
        product = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("openai==3.8.0", requirements)
        self.assertIn(
            "opentelemetry-instrumentation-genai-openai==1.1b0",
            requirements,
        )
        self.assertNotIn("openai==", product)
        self.assertNotIn("opentelemetry-instrumentation-genai-openai", product)

    def test_offline_runner_is_no_network_and_live_secret_is_explicit(self) -> None:
        runner = (ROOT / "scripts" / "test_openai_instrumentation.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("--network none", runner)
        self.assertIn("IIP_OPENAI_LIVE_TEST_ENABLED", runner)
        self.assertIn("OPENAI_API_KEY", runner)
        self.assertIn("--read-only", runner)
        self.assertIn("--cap-drop ALL", runner)
        self.assertNotIn("/.config/openai", runner)

    def test_harness_uses_real_sdk_and_conservative_breakdowns(self) -> None:
        harness = (
            ROOT / "scripts" / "run_openai_instrumentation_compatibility.py"
        ).read_text(encoding="utf-8")

        self.assertIn("client.chat.completions.create(", harness)
        self.assertIn('"OTEL_INSTRUMENTATION_GENAI_EMIT_EVENT"] = "false"', harness)
        self.assertIn('"zeroWhenAbsent": []', harness)
        self.assertIn("ThreadingHTTPServer", harness)
        self.assertIn("BatchSpanProcessor", harness)


if __name__ == "__main__":
    unittest.main()
