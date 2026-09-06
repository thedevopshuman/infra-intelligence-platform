from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from infra_intelligence_sdk import AiFinopsRuntimeCompatibilityReport


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ai_finops_fixture as compatibility  # noqa: E402


class AiFinopsRuntimeCompatibilityTests(unittest.TestCase):
    def report(self, *, source_dirty: bool = False) -> dict[str, object]:
        return dict(
            compatibility.build_compatibility_report(
                generated_at=datetime(2026, 9, 8, 10, 5, tzinfo=timezone.utc),
                source_revision="0123456789abcdef0123456789abcdef01234567",
                source_dirty=source_dirty,
                platform="linux/arm64",
                container_runtime_version="28.3.3",
                application_version="0.84.0",
                measurements=compatibility.COMPATIBILITY_MEASUREMENTS,
            )
        )

    def test_example_is_generated_schema_valid_and_sdk_readable(self) -> None:
        report = self.report()
        example = json.loads(
            (
                ROOT
                / "contracts/examples/ai-finops-runtime-compatibility-report.json"
            ).read_text(encoding="utf-8")
        )
        schema = json.loads(
            (
                ROOT
                / "contracts/schemas/ai-finops-runtime-compatibility-report.schema.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(report, example)
        self.assertEqual(
            [],
            list(
                Draft202012Validator(
                    schema,
                    format_checker=FormatChecker(),
                ).iter_errors(report)
            ),
        )
        self.assertEqual(
            report,
            AiFinopsRuntimeCompatibilityReport.from_dict(report).to_dict(),
        )

    def test_identity_summary_and_clean_source_are_recomputed(self) -> None:
        report = self.report()
        compatibility.validate_compatibility_report(report, require_clean=True)

        crossed = copy.deepcopy(report)
        crossed["metadata"]["id"] = "afc_" + "f" * 32
        with self.assertRaisesRegex(ValueError, "identity.invalid"):
            compatibility.validate_compatibility_report(crossed)

        bad_summary = copy.deepcopy(report)
        bad_summary["spec"]["summary"]["passedChecks"] = 13
        bad_summary["metadata"]["id"] = compatibility._compatibility_identifier(
            {
                key: value
                for key, value in bad_summary["metadata"].items()
                if key != "id"
            },
            bad_summary["spec"],
        )
        with self.assertRaisesRegex(ValueError, "summary.invalid"):
            compatibility.validate_compatibility_report(bad_summary)

        crossed_status = copy.deepcopy(report)
        crossed_status["spec"]["status"] = "incompatible"
        crossed_status["metadata"]["id"] = compatibility._compatibility_identifier(
            {
                key: value
                for key, value in crossed_status["metadata"].items()
                if key != "id"
            },
            crossed_status["spec"],
        )
        with self.assertRaisesRegex(ValueError, "summary.invalid"):
            compatibility.validate_compatibility_report(crossed_status)

        with self.assertRaisesRegex(ValueError, "source.dirty"):
            compatibility.validate_compatibility_report(
                self.report(source_dirty=True),
                require_clean=True,
            )

    def test_report_is_minimized_and_gate_writes_then_verifies_it(self) -> None:
        serialized = json.dumps(self.report(), sort_keys=True).lower()
        for prohibited in (
            compatibility.KNOWN_MODEL,
            compatibility.CANDIDATE_MODEL,
            compatibility.CHANNEL_TOKEN,
            compatibility.CONTROL_TOKEN,
            "pricesubunitspermilliontokens",
            "totalsubunits",
        ):
            self.assertNotIn(prohibited.lower(), serialized)

        gate = (ROOT / "scripts/test_ai_finops.sh").read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("--report \"$IIP_AI_FINOPS_RUNTIME_REPORT\"", gate)
        self.assertIn("--source-revision", gate)
        self.assertIn("--source-dirty", gate)
        self.assertIn("verify-ai-finops-runtime-report:", makefile)

    def test_current_verifier_rebinds_source_and_application_version(self) -> None:
        report = self.report()
        with patch.object(
            compatibility,
            "_source_identity",
            return_value=(
                "0123456789abcdef0123456789abcdef01234567",
                False,
                "0.84.0",
            ),
        ):
            compatibility.verify_current_compatibility_report(
                report,
                require_clean=True,
            )

        with patch.object(
            compatibility,
            "_source_identity",
            return_value=("f" * 40, False, "0.84.0"),
        ):
            with self.assertRaisesRegex(ValueError, "revision-mismatch"):
                compatibility.verify_current_compatibility_report(
                    report,
                    require_clean=True,
                )

        with patch.object(
            compatibility,
            "_source_identity",
            return_value=(
                "0123456789abcdef0123456789abcdef01234567",
                False,
                "0.85.0",
            ),
        ):
            with self.assertRaisesRegex(ValueError, "application-version.mismatch"):
                compatibility.verify_current_compatibility_report(
                    report,
                    require_clean=True,
                )


if __name__ == "__main__":
    unittest.main()
