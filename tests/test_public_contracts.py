from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from infra_intelligence_sdk import Evidence, InvestigationReport, InvestigationRequest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_repo  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8"))


class PublicContractSdkTests(unittest.TestCase):
    def test_sdk_models_accept_public_contract_examples(self) -> None:
        evidence = Evidence.from_dict(example("evidence.json"))
        request = InvestigationRequest.from_dict(example("investigation-request.json"))
        report = InvestigationReport.from_dict(example("investigation-report.json"))

        self.assertEqual(evidence.to_dict()["kind"], "Evidence")
        self.assertEqual(request.to_dict()["kind"], "InvestigationRequest")
        self.assertEqual(report.to_dict()["kind"], "InvestigationReport")

    def test_sdk_models_reject_crossed_contract_kinds(self) -> None:
        with self.assertRaisesRegex(ValueError, "kind must be Evidence"):
            Evidence.from_dict(example("investigation-report.json"))
        with self.assertRaisesRegex(ValueError, "kind must be InvestigationRequest"):
            InvestigationRequest.from_dict(example("evidence.json"))
        with self.assertRaisesRegex(ValueError, "kind must be InvestigationReport"):
            InvestigationReport.from_dict(example("investigation-request.json"))

    def test_sdk_models_reject_unknown_versions(self) -> None:
        payload = example("investigation-request.json")
        payload["apiVersion"] = "iip.platform/v2"
        with self.assertRaisesRegex(ValueError, "unsupported investigation request apiVersion"):
            InvestigationRequest.from_dict(payload)


class PublicContractRepositoryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.errors: list[str] = []
        self.documents = dict(validate_repo.load_json_documents(self.errors))
        self.assertEqual(self.errors, [])

    def test_examples_satisfy_cross_contract_invariants(self) -> None:
        validate_repo.validate_examples(self.documents, self.errors)
        self.assertEqual(self.errors, [])

    def test_report_rejects_citation_outside_evidence_set(self) -> None:
        report_path = ROOT / "contracts" / "examples" / "investigation-report.json"
        report = copy.deepcopy(self.documents[report_path])
        report["spec"]["hypotheses"][0]["supportingEvidenceIds"].append(
            "evd_00000000000000000000000000000000"
        )
        self.documents[report_path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "investigation report contains citations outside its evidenceIds set",
            self.errors,
        )

    def test_report_rejects_modified_request_digest(self) -> None:
        request_path = ROOT / "contracts" / "examples" / "investigation-request.json"
        request = copy.deepcopy(self.documents[request_path])
        request["spec"]["question"] = "A different question"
        self.documents[request_path] = request

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "investigation report requestDigest must match the canonical request example",
            self.errors,
        )

    def test_report_rejects_usage_above_request_budget(self) -> None:
        report_path = ROOT / "contracts" / "examples" / "investigation-report.json"
        report = copy.deepcopy(self.documents[report_path])
        report["spec"]["usage"]["toolCalls"] = 25
        self.documents[report_path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "investigation report toolCalls exceeds request maxToolCalls",
            self.errors,
        )

    def test_report_rejects_inconsistent_terminal_reason(self) -> None:
        report_path = ROOT / "contracts" / "examples" / "investigation-report.json"
        report = copy.deepcopy(self.documents[report_path])
        report["spec"]["terminalReason"] = "runtime-error"
        self.documents[report_path] = report

        validate_repo.validate_examples(self.documents, self.errors)

        self.assertIn(
            "investigation report outcome and terminalReason are inconsistent",
            self.errors,
        )


if __name__ == "__main__":
    unittest.main()
