from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from infra_intelligence_sdk import Client, EventOutboxRetentionReport


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "schemas" / "event-outbox-retention-report.schema.json"
EXAMPLE = ROOT / "contracts" / "examples" / "event-outbox-retention-report.json"
TOKEN = "retention-token-0123456789abcdef0123456789abcdef"


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class EventOutboxRetentionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = load(SCHEMA)
        self.example = load(EXAMPLE)
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(
            self.schema,
            format_checker=FormatChecker(),
        )

    def assert_invalid(self, document: dict[str, object]) -> None:
        self.assertTrue(list(self.validator.iter_errors(document)))

    def test_example_validates_and_sdk_round_trips(self) -> None:
        self.assertEqual([], list(self.validator.iter_errors(self.example)))
        report = EventOutboxRetentionReport.from_dict(self.example)
        self.assertEqual(self.example, report.to_dict())

        client = Client("https://control.example", TOKEN)
        client._get = (  # type: ignore[method-assign]
            lambda path: self.example
            if path == "/v1/operations/events/retention"
            else {}
        )
        self.assertEqual(self.example, client.get_event_outbox_retention().to_dict())

    def test_python_sdk_rejects_wrong_report_kind(self) -> None:
        wrong_kind = copy.deepcopy(self.example)
        wrong_kind["kind"] = "EvidenceRetentionReport"

        with self.assertRaisesRegex(
            ValueError,
            "event outbox retention report kind must be EventOutboxRetentionReport",
        ):
            EventOutboxRetentionReport.from_dict(wrong_kind)

        client = Client("https://control.example", TOKEN)
        client._get = lambda path: wrong_kind  # type: ignore[method-assign]
        with self.assertRaisesRegex(ValueError, "kind must be"):
            client.get_event_outbox_retention()

    def test_schema_enforces_expressible_mode_status_and_audit_relations(self) -> None:
        observe_expiration = copy.deepcopy(self.example)
        observe_expiration["spec"]["rows"]["expired"] = 1
        observe_expiration["spec"]["rows"]["remainingEligible"] = 11
        observe_expiration["spec"]["auditRef"] = (
            "audit://tenant-acme/event-outbox-retention/1"
        )
        self.assert_invalid(observe_expiration)

        expire_disabled = copy.deepcopy(self.example)
        expire_disabled["spec"]["mode"] = "expire"
        expire_disabled["spec"]["status"] = "disabled"
        expire_disabled["spec"]["policy"]["enabled"] = False
        self.assert_invalid(expire_disabled)

        disabled_enabled = copy.deepcopy(self.example)
        disabled_enabled["spec"]["status"] = "disabled"
        self.assert_invalid(disabled_enabled)

        current_remaining = copy.deepcopy(self.example)
        current_remaining["spec"]["status"] = "current"
        self.assert_invalid(current_remaining)

        cleanup_complete = copy.deepcopy(self.example)
        cleanup_complete["spec"]["rows"]["eligible"] = 0
        cleanup_complete["spec"]["rows"]["remainingEligible"] = 0
        cleanup_complete["spec"]["status"] = "cleanup-required"
        self.assert_invalid(cleanup_complete)

        audit_without_expiration = copy.deepcopy(self.example)
        audit_without_expiration["spec"]["auditRef"] = (
            "audit://tenant-acme/event-outbox-retention/1"
        )
        self.assert_invalid(audit_without_expiration)

        expiration_without_audit = copy.deepcopy(self.example)
        expiration_without_audit["spec"]["mode"] = "expire"
        expiration_without_audit["spec"]["rows"]["expired"] = 1
        expiration_without_audit["spec"]["rows"]["remainingEligible"] = 11
        self.assert_invalid(expiration_without_audit)

    def test_schema_bounds_duration_batch_and_safe_counters(self) -> None:
        for field, value in (
            ("publishedSeconds", 2_591_999),
            ("publishedSeconds", 315_360_001),
            ("batchSize", 0),
            ("batchSize", 1_001),
        ):
            with self.subTest(field=field, value=value):
                document = copy.deepcopy(self.example)
                document["spec"]["policy"][field] = value
                self.assert_invalid(document)

        unsafe_counter = copy.deepcopy(self.example)
        unsafe_counter["spec"]["rows"]["storedBefore"] = 9_007_199_254_740_992
        self.assert_invalid(unsafe_counter)


if __name__ == "__main__":
    unittest.main()
