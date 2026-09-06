from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import qualify_control_plane_load as load  # noqa: E402
import qualify_ingress_availability as ingress  # noqa: E402
from infra_intelligence_sdk import ControlPlaneLoadQualificationReport  # noqa: E402


REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE_DIGEST = "sha256:" + "a" * 64
TARGET_DIGEST = "sha256:" + "b" * 64
REPOSITORY = {
    "applicationVersion": "0.84.0",
    "chartVersion": "0.87.0",
    "requiredMigration": "0023_ai_model_suitability.sql",
}
STARTED = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
COMPLETED = datetime(2026, 9, 6, 12, 5, tzinfo=timezone.utc)


def success(duration: int = 25) -> ingress._Attempt:
    return ingress._Attempt(
        "runtime-identity", True, duration, document={"ignored": "after-validation"}
    )


def raw_result(
    *,
    attempts: tuple[ingress._Attempt, ...] | None = None,
    missed: int = 0,
) -> load.RawLoadResult:
    selected = attempts if attempts is not None else tuple(success() for _ in range(300))
    return load.RawLoadResult(
        started_at=STARTED,
        completed_at=COMPLETED,
        actual_duration_milliseconds=300_000,
        target_request_count=300,
        attempts=selected,
        scheduler_missed_requests=missed,
        scheduler_lags_milliseconds=tuple(2 for _ in range(300)),
    )


def build(raw: load.RawLoadResult | None = None) -> dict[str, object]:
    return load.build_report(
        revision=REVISION,
        repository=REPOSITORY,
        image_digest=IMAGE_DIGEST,
        target_binding_digest=TARGET_DIGEST,
        ca_source="system",
        raw=raw or raw_result(),
        duration_seconds=300,
        target_requests_per_second=1,
        concurrency=8,
        minimum_successful_request_basis_points=9990,
        maximum_scheduler_miss_basis_points=10,
        maximum_p95_latency_milliseconds=2000,
        maximum_p99_latency_milliseconds=5000,
        request_timeout_milliseconds=2000,
        maximum_scheduler_lag_milliseconds=1000,
    )


class ControlPlaneLoadQualificationTests(unittest.TestCase):
    def test_contract_example_is_schema_semantic_and_sdk_valid(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "contracts/schemas/control-plane-load-qualification-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        example = json.loads(
            (
                ROOT
                / "contracts/examples/control-plane-load-qualification-report.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        load.validate_report_document(example)
        self.assertEqual(
            ControlPlaneLoadQualificationReport.from_dict(example).to_dict(),
            example,
        )

    def test_successful_fixed_rate_aggregates_are_qualified_and_minimized(self) -> None:
        report = build()
        measurements = report["spec"]["measurements"]
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(measurements["targetRequestCount"], 300)
        self.assertEqual(measurements["successfulRequestBasisPoints"], 10_000)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("customer.example.test", serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("ignored", serialized)

    def test_schedule_failure_and_identity_failure_cannot_qualify(self) -> None:
        attempts = tuple(success(40) for _ in range(297)) + (
            ingress._Attempt("runtime-identity", False, 10, "identity"),
            ingress._Attempt("runtime-identity", False, 10, "transport"),
        )
        report = build(raw_result(attempts=attempts, missed=1))
        self.assertEqual(report["spec"]["status"], "not-qualified")
        by_id = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(by_id["exact-release-identity"]["status"], "failed")
        self.assertEqual(by_id["scheduler-attainment"]["status"], "failed")
        self.assertEqual(by_id["successful-request-attainment"]["status"], "failed")

    def test_rehashed_measurement_and_check_tampering_is_rejected(self) -> None:
        report = build()
        tampered = copy.deepcopy(report)
        tampered["spec"]["measurements"]["successfulRequests"] = 299
        metadata = dict(tampered["metadata"])
        metadata.pop("id")
        tampered["metadata"]["id"] = load._report_identifier(
            metadata, tampered["spec"]
        )
        with self.assertRaisesRegex(
            load.ControlPlaneLoadQualificationError,
            "control-plane-load.report.accounting-invalid",
        ):
            load.validate_report_document(tampered)

    def test_explicit_enable_and_request_volume_are_checked_before_io(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            common = {
                "base_url": "https://customer.example.test",
                "token_file": Path(temporary) / "missing-token",
                "image_digest": IMAGE_DIGEST,
                "output": Path(temporary) / "report.json",
            }
            with self.assertRaisesRegex(
                load.ControlPlaneLoadQualificationError,
                "control-plane-load.traffic.explicit-enable-required",
            ):
                load.generate_report(allow_traffic=False, **common)
            with self.assertRaisesRegex(
                load.ControlPlaneLoadQualificationError,
                "control-plane-load.objective.request-volume-exceeded",
            ):
                load.generate_report(
                    allow_traffic=True,
                    duration_seconds=3600,
                    target_requests_per_second=250,
                    **common,
                )

    def test_scheduler_accounts_for_every_fixed_rate_slot(self) -> None:
        times = iter((STARTED, COMPLETED))
        result = load._run_fixed_rate(
            duration_seconds=1,
            target_requests_per_second=50,
            concurrency=4,
            maximum_scheduler_lag_milliseconds=1000,
            requester_factory=lambda: (lambda: success(1)),
            clock=lambda: next(times),
            monotonic=__import__("time").monotonic,
            sleeper=lambda _: None,
        )
        self.assertEqual(result.target_request_count, 50)
        self.assertEqual(len(result.attempts) + result.scheduler_missed_requests, 50)
        self.assertEqual(len(result.scheduler_lags_milliseconds), 50)

    def test_late_slots_are_missed_instead_of_replayed_as_a_burst(self) -> None:
        current = [0.0]
        calls = [0]

        def monotonic() -> float:
            return current[0]

        def sleeper(seconds: float) -> None:
            current[0] += seconds

        def request() -> ingress._Attempt:
            calls[0] += 1
            current[0] += 0.7
            return success(700)

        result = load._run_fixed_rate(
            duration_seconds=1,
            target_requests_per_second=5,
            concurrency=1,
            maximum_scheduler_lag_milliseconds=100,
            requester_factory=lambda: request,
            clock=lambda: STARTED,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        self.assertEqual(len(result.attempts), calls[0])
        self.assertGreater(result.scheduler_missed_requests, 0)
        self.assertLess(calls[0], result.target_request_count)
        self.assertEqual(
            len(result.attempts) + result.scheduler_missed_requests,
            result.target_request_count,
        )

    def test_offline_verifier_rebinds_source_target_and_image(self) -> None:
        report = build()
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with (
                patch.object(load.ingress, "_git_state", return_value=(REVISION, False)),
                patch.object(
                    load.ingress,
                    "_repository_identity",
                    return_value=REPOSITORY,
                ),
                patch.object(
                    load.ingress,
                    "_checked_url",
                    return_value=(
                        "https://customer.example.test",
                        "verified-https",
                        TARGET_DIGEST,
                    ),
                ),
            ):
                verified = load.verify_report(
                    report_path=report_path,
                    base_url="https://customer.example.test",
                    image_digest=IMAGE_DIGEST,
                    require_qualified=True,
                )
                self.assertEqual(verified, report)
                with self.assertRaisesRegex(
                    load.ControlPlaneLoadQualificationError,
                    "control-plane-load.report.binding-mismatch",
                ):
                    load.verify_report(
                        report_path=report_path,
                        base_url="https://customer.example.test",
                        image_digest="sha256:" + "c" * 64,
                        require_qualified=True,
                    )

    def test_dirty_source_stops_before_the_load_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / "token"
            token.write_text("x" * 32 + "\n", encoding="ascii")
            runner_called = [False]

            def runner(**_kwargs):
                runner_called[0] = True
                return raw_result()

            with (
                patch.object(load.ingress, "_git_state", return_value=(REVISION, True)),
                patch.object(
                    load.ingress,
                    "_repository_identity",
                    return_value=REPOSITORY,
                ),
            ):
                with self.assertRaisesRegex(
                    load.ControlPlaneLoadQualificationError,
                    "control-plane-load.source.dirty",
                ):
                    load.generate_report(
                        allow_traffic=True,
                        base_url="https://customer.example.test",
                        token_file=token,
                        image_digest=IMAGE_DIGEST,
                        output=Path(temporary) / "report.json",
                        duration_seconds=300,
                        target_requests_per_second=1,
                        runner=runner,
                    )
            self.assertFalse(runner_called[0])

    def test_generation_uses_existing_secure_ingress_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / "token"
            token.write_text("x" * 32 + "\n", encoding="ascii")
            output = Path(temporary) / "report.json"
            captured: dict[str, object] = {}

            def runner(**kwargs):
                captured.update(kwargs)
                attempted = kwargs["requester_factory"]()()
                self.assertTrue(attempted.success)
                self.assertIsNone(attempted.document)
                return raw_result()

            with (
                patch.object(load.ingress, "_git_state", return_value=(REVISION, False)),
                patch.object(load.ingress, "_repository_identity", return_value=REPOSITORY),
                patch.object(load.ssl, "create_default_context", return_value=object()),
                patch.object(
                    load.ingress,
                    "_request_json",
                    return_value=ingress._Attempt(
                        "runtime-identity",
                        True,
                        3,
                        document={"tenantId": "must-not-be-retained"},
                    ),
                ),
                patch.object(
                    load.ingress,
                    "_validate_path_document",
                    side_effect=lambda attempted, _identity: attempted,
                ),
            ):
                report = load.generate_report(
                    allow_traffic=True,
                    base_url="https://customer.example.test",
                    token_file=token,
                    image_digest=IMAGE_DIGEST,
                    output=output,
                    duration_seconds=300,
                    target_requests_per_second=1,
                    runner=runner,
                )
            self.assertEqual(report["spec"]["status"], "qualified")
            self.assertTrue(output.is_file())
            self.assertEqual(captured["concurrency"], 8)


if __name__ == "__main__":
    unittest.main()
