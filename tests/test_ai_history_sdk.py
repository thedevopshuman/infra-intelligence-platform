"""Public SDK boundaries for explicit AI-history availability and retirement."""

from __future__ import annotations

from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError

from infra_intelligence_sdk import (
    AiEconomicsInvocationObservationRequest,
    AiHistoryAvailabilityReport,
    ApiError,
    Client,
)


def availability_report(*, retired: int = 0) -> dict[str, object]:
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "AiHistoryAvailabilityReport",
        "metadata": {
            "tenantId": "local",
            "generatedAt": "2026-10-04T12:00:00Z",
        },
        "spec": {
            "scope": {
                "start": "2026-10-03T12:00:00Z",
                "end": "2026-10-04T12:00:00Z",
            },
            "status": "available" if retired == 0 else "history-retired",
            "coverage": {
                "retainedUsageRecords": 7,
                "retiredUsageRecords": retired,
            },
        },
    }


def retired_http_error(url: str) -> HTTPError:
    body = json.dumps({"error": {"code": "ai.history.retired"}}).encode("utf-8")
    return HTTPError(url, 410, "Gone", hdrs=None, fp=BytesIO(body))


class AiHistorySdkTests(TestCase):
    def test_python_model_enforces_status_and_safe_coverage(self) -> None:
        available = AiHistoryAvailabilityReport.from_dict(availability_report())
        self.assertEqual(available.status, "available")
        self.assertEqual(
            available.coverage,
            {"retainedUsageRecords": 7, "retiredUsageRecords": 0},
        )

        retired = AiHistoryAvailabilityReport.from_dict(
            availability_report(retired=2)
        )
        self.assertEqual(retired.status, "history-retired")

        inconsistent = availability_report(retired=1)
        inconsistent["spec"]["status"] = "available"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "AI history availability report"):
            AiHistoryAvailabilityReport.from_dict(inconsistent)

        unsafe = availability_report()
        unsafe["spec"]["coverage"]["retainedUsageRecords"] = (  # type: ignore[index]
            9_007_199_254_740_992
        )
        with self.assertRaisesRegex(ValueError, "AI history availability report"):
            AiHistoryAvailabilityReport.from_dict(unsafe)

    def test_python_model_rejects_invalid_tenant_time_and_interval(self) -> None:
        invalid_tenant = availability_report()
        invalid_tenant["metadata"]["tenantId"] = "../local"  # type: ignore[index]

        invalid_generated_at = availability_report()
        invalid_generated_at["metadata"]["generatedAt"] = (  # type: ignore[index]
            "2026-10-04T12:00:00.000000Z"
        )

        noncanonical_start = availability_report()
        noncanonical_start["spec"]["scope"]["start"] = (  # type: ignore[index]
            "2026-10-03T12:00:00+00:00"
        )

        reversed_interval = availability_report()
        reversed_interval["spec"]["scope"] = {  # type: ignore[index]
            "start": "2026-10-04T12:00:00Z",
            "end": "2026-10-03T12:00:00Z",
        }

        overlong_interval = availability_report()
        overlong_interval["spec"]["scope"] = {  # type: ignore[index]
            "start": "2026-09-02T12:00:00Z",
            "end": "2026-10-04T12:00:00Z",
        }

        for document in (
            invalid_tenant,
            invalid_generated_at,
            noncanonical_start,
            reversed_interval,
            overlong_interval,
        ):
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    ValueError, "AI history availability report"
                ):
                    AiHistoryAvailabilityReport.from_dict(document)

    def test_python_client_uses_closed_history_interval(self) -> None:
        calls: list[str] = []
        client = Client("https://control.example", "history-token-0123456789")
        client._get = lambda path: (  # type: ignore[method-assign]
            calls.append(path) or availability_report(retired=2)
        )

        report = client.get_ai_history_availability(
            start="2026-10-03T12:00:00Z",
            end="2026-10-04T12:00:00Z",
        )

        self.assertEqual(report.status, "history-retired")
        self.assertEqual(
            calls,
            [
                "/v1/ai/economics/history-availability?"
                "start=2026-10-03T12%3A00%3A00Z&end=2026-10-04T12%3A00%3A00Z"
            ],
        )

    def test_python_clients_preserve_stable_retirement_error(self) -> None:
        client = Client("https://control.example", "history-token-0123456789")
        request = AiEconomicsInvocationObservationRequest.from_dict(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "AiEconomicsInvocationObservationRequest",
                "spec": {"traceId": "1" * 32, "spanId": "2" * 16},
            }
        )
        calls = (
            lambda: client.get_ai_history_availability(
                start="2026-10-03T12:00:00Z",
                end="2026-10-04T12:00:00Z",
            ),
            lambda: client.get_ai_allocation_report(
                start="2026-10-03T12:00:00Z",
                end="2026-10-04T12:00:00Z",
                group_by="application",
            ),
            lambda: client.observe_ai_economics_invocation(request),
        )
        for call in calls:
            with self.subTest(call=call):
                with patch(
                    "infra_intelligence_sdk.client.urlopen",
                    side_effect=retired_http_error("https://control.example"),
                ):
                    with self.assertRaises(ApiError) as raised:
                        call()
                self.assertEqual(raised.exception.status, 410)
                self.assertEqual(raised.exception.code, "ai.history.retired")


if __name__ == "__main__":
    import unittest

    unittest.main()
