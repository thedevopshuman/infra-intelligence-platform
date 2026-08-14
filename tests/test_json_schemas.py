from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def document(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class JsonSchemaValidationTests(unittest.TestCase):
    def validate(self, schema_name: str, payload: dict) -> list[str]:
        schema = document(f"contracts/schemas/{schema_name}")
        return validate_schemas.instance_validation_errors(
            schema,
            payload,
            label="test payload",
        )

    def test_all_contract_examples_validate(self) -> None:
        self.assertEqual(validate_schemas.validate_repository(), [])

    def test_invalid_date_time_is_rejected(self) -> None:
        evidence = document("contracts/examples/evidence.json")
        evidence["metadata"]["recordedAt"] = "not-a-date-time"

        errors = self.validate("evidence.schema.json", evidence)

        self.assertTrue(any("date-time" in error for error in errors))

    def test_invalid_uri_reference_is_rejected(self) -> None:
        request = document("contracts/examples/investigation-request.json")
        request["spec"]["trigger"]["source"] = "https://[invalid"

        errors = self.validate("investigation-request.schema.json", request)

        self.assertTrue(any("uri-reference" in error for error in errors))

    def test_applied_redaction_requires_a_method(self) -> None:
        evidence = document("contracts/examples/evidence.json")
        evidence["spec"]["handling"]["redaction"]["methods"] = []

        errors = self.validate("evidence.schema.json", evidence)

        self.assertTrue(any(".methods" in error for error in errors))

    def test_conclusive_report_requires_selected_agent(self) -> None:
        report = document("contracts/examples/investigation-report.json")
        del report["spec"]["agent"]

        errors = self.validate("investigation-report.schema.json", report)

        self.assertTrue(any("'agent' is a required property" in error for error in errors))

    def test_unknown_contract_property_is_rejected(self) -> None:
        request = copy.deepcopy(document("contracts/examples/investigation-request.json"))
        request["spec"]["ambientCredentials"] = True

        errors = self.validate("investigation-request.schema.json", request)

        self.assertTrue(any("ambientCredentials" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
