from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from iip.adapters.memory import InMemoryResourceStore
from iip.application.ports import ActorContext, PersistenceError
from iip.application.validate_ai_model_suitability import (
    InvalidAiModelSuitabilityReportError,
    MAX_VALIDITY,
    model_suitability_report_id,
    validate_ai_model_suitability_report,
)


ROOT = Path(__file__).resolve().parents[1]


def report() -> dict:
    return json.loads(
        (
            ROOT
            / "contracts"
            / "examples"
            / "ai-model-suitability-report.json"
        ).read_text(encoding="utf-8")
    )


def actor(tenant_id: str = "local") -> ActorContext:
    return ActorContext(
        "ai-savings-worker:suitability-test",
        tenant_id,
        ("ai-savings:evaluate",),
    )


class AiModelSuitabilityTests(unittest.TestCase):
    def test_valid_report_has_content_derived_identity_and_bounded_validity(self) -> None:
        document = report()
        validated = validate_ai_model_suitability_report(
            document,
            allow_test_fixtures=True,
        )

        self.assertEqual(validated.report_id, model_suitability_report_id(document))
        self.assertLessEqual(
            validated.valid_until - validated.evaluated_at,
            MAX_VALIDITY,
        )
        self.assertEqual(
            [item["id"] for item in document["spec"]["gates"]],
            ["quality", "latency", "safety", "compliance"],
        )
        self.assertTrue(
            all(value is False for value in document["spec"]["contentHandling"].values())
        )

    def test_test_fixture_is_fail_closed_by_default(self) -> None:
        with self.assertRaisesRegex(
            InvalidAiModelSuitabilityReportError,
            "ai.model-suitability.report.invalid",
        ):
            validate_ai_model_suitability_report(report())

    def test_rejects_content_scope_tampering_and_overlong_validity(self) -> None:
        mutations = (
            ("content", lambda value: value["spec"]["contentHandling"].update({"promptContentPersisted": True})),
            ("same-model", lambda value: value["spec"]["scope"].update({"candidateModelId": value["spec"]["scope"]["referenceModelId"]})),
            ("gate", lambda value: value["spec"]["gates"][0].update({"status": "failed"})),
            ("validity", lambda value: value["metadata"].update({"validUntil": "2027-01-04T09:00:00Z"})),
            ("credential", lambda value: value["spec"]["source"].update({"locator": "https://user:secret@example.test/report"})),
            ("signed-query", lambda value: value["spec"]["source"].update({"locator": "https://example.test/report?X-Amz-Signature=abc"})),
            ("unsupported-scheme", lambda value: value["spec"]["source"].update({"locator": "file:///tmp/report.json"})),
            ("relative-https", lambda value: value["spec"]["source"].update({"locator": "https:relative-report"})),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                document = report()
                mutate(document)
                document["metadata"]["id"] = model_suitability_report_id(document)
                with self.assertRaisesRegex(
                    InvalidAiModelSuitabilityReportError,
                    "ai.model-suitability.report.invalid",
                ):
                    validate_ai_model_suitability_report(
                        document,
                        allow_test_fixtures=True,
                    )

    def test_immutable_tenant_registration_is_idempotent(self) -> None:
        store = InMemoryResourceStore()
        document = report()

        first = store.register_ai_model_suitability_report(
            actor(),
            document,
            allow_test_fixtures=True,
        )
        second = store.register_ai_model_suitability_report(
            actor(),
            document,
            allow_test_fixtures=True,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(store._ai_model_suitability_reports), 1)
        cross_tenant = copy.deepcopy(document)
        with self.assertRaisesRegex(PersistenceError, "storage.request.invalid"):
            store.register_ai_model_suitability_report(
                actor("other"),
                cross_tenant,
                allow_test_fixtures=True,
            )


if __name__ == "__main__":
    unittest.main()
