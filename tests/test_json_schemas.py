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

    def test_reconciliation_observation_requires_snapshot_id(self) -> None:
        resource = document("contracts/examples/resource.json")
        resource["metadata"]["observation"]["mode"] = "reconciliation"

        errors = self.validate("resource.schema.json", resource)

        self.assertTrue(any("snapshotId" in error for error in errors))

    def test_deleted_resource_rejects_mutable_state(self) -> None:
        resource = document("contracts/examples/resource.json")
        resource["status"]["lifecycle"] = "deleted"

        errors = self.validate("resource.schema.json", resource)

        self.assertTrue(any("attributes" in error for error in errors))

    def test_reconciliation_collection_request_requires_snapshot_id(self) -> None:
        request = document("contracts/examples/resource-collection-request.json")
        request["spec"]["mode"] = "reconciliation"

        errors = self.validate("resource-collection-request.schema.json", request)

        self.assertTrue(any("snapshotId" in error for error in errors))

    def test_incomplete_collection_result_cannot_advance_checkpoint(self) -> None:
        result = document("contracts/examples/resource-collection-result.json")
        result["spec"]["completion"]["status"] = "partial"
        result["spec"]["completion"]["reasonCode"] = "collector.output-limited"

        errors = self.validate("resource-collection-result.schema.json", result)

        self.assertTrue(any("checkpoint" in error for error in errors))

    def test_complete_collection_result_rejects_reason_code(self) -> None:
        result = document("contracts/examples/resource-collection-result.json")
        result["spec"]["completion"]["reasonCode"] = "collector.provider-error"

        errors = self.validate("resource-collection-result.schema.json", result)

        self.assertTrue(any("reasonCode" in error for error in errors))

    def test_resume_requires_checkpoint_and_nonempty_provider_cursor_map(self) -> None:
        request = document("contracts/examples/resource-collection-request.json")
        request["spec"]["resume"] = {"checkpoint": "opaque"}

        errors = self.validate("resource-collection-request.schema.json", request)

        self.assertTrue(any("providerCursors" in error for error in errors))

    def test_incomplete_collection_result_cannot_return_provider_cursors(self) -> None:
        result = document("contracts/examples/resource-collection-result.json")
        completion = result["spec"]["completion"]
        completion.update(
            {"status": "failed", "reasonCode": "collector.provider-error"}
        )
        del completion["checkpoint"]

        errors = self.validate("resource-collection-result.schema.json", result)

        self.assertTrue(errors)

    def test_page_with_more_items_requires_next_cursor(self) -> None:
        page = document("contracts/examples/page-info.json")
        del page["nextCursor"]

        errors = self.validate("page-info.schema.json", page)

        self.assertTrue(any("nextCursor" in error for error in errors))

    def test_terminal_page_prohibits_next_cursor(self) -> None:
        page = document("contracts/examples/page-info.json")
        page["hasMore"] = False

        errors = self.validate("page-info.schema.json", page)

        self.assertTrue(any("nextCursor" in error for error in errors))

    def test_neighborhood_rejects_unbounded_depth(self) -> None:
        neighborhood = document("contracts/examples/resource-neighborhood.json")
        neighborhood["spec"]["depth"] = 2

        errors = self.validate("resource-neighborhood.schema.json", neighborhood)

        self.assertTrue(any("depth" in error for error in errors))

    def test_evaluation_scenario_rejects_unknown_hard_gate(self) -> None:
        scenario = document("contracts/examples/evaluation-scenario.json")
        scenario["spec"]["scoring"]["hardGates"].append("narrative-quality")

        errors = self.validate("evaluation-scenario.schema.json", scenario)

        self.assertTrue(any("hardGates" in error for error in errors))

    def test_evaluation_scenario_requires_complete_timelines(self) -> None:
        scenario = document("contracts/examples/evaluation-scenario.json")
        page = scenario["spec"]["fixtures"]["timelines"][0]["spec"]["page"]
        page["hasMore"] = True
        page["nextCursor"] = "p1.more"

        errors = self.validate("evaluation-scenario.schema.json", scenario)

        self.assertTrue(any("hasMore" in error for error in errors))

    def test_telemetry_request_rejects_vendor_query_language(self) -> None:
        request = document("contracts/examples/telemetry-evidence-request.json")
        request["spec"]["query"]["promql"] = "up"

        errors = self.validate("telemetry-evidence-request.schema.json", request)

        self.assertTrue(any("promql" in error for error in errors))

    def test_no_data_telemetry_result_cannot_contain_series(self) -> None:
        result = document("contracts/examples/telemetry-evidence-result.json")
        result["spec"]["status"] = "no-data"

        errors = self.validate("telemetry-evidence-result.schema.json", result)

        self.assertTrue(any("series" in error for error in errors))

    def test_partial_telemetry_result_requires_stable_warning(self) -> None:
        result = document("contracts/examples/telemetry-evidence-result.json")
        result["spec"]["status"] = "partial"

        errors = self.validate("telemetry-evidence-result.schema.json", result)

        self.assertTrue(any("warnings" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
