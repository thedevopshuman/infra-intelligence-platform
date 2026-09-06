from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import qualify_customer_processing_continuity as processing  # noqa: E402
import qualify_customer_sustained_workload as workload  # noqa: E402
from infra_intelligence_sdk import (  # noqa: E402
    CustomerSustainedWorkloadProfile,
    CustomerSustainedWorkloadQualificationReport,
)


REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE_DIGEST = "sha256:" + "a" * 64
API_TARGET = "https://iip.example.test"
OTLP_TARGET = "https://otlp.example.test"
STARTED = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
COMPLETED = STARTED + timedelta(minutes=15)
REPOSITORY = {
    "applicationVersion": "0.84.0",
    "chartVersion": "0.87.0",
    "requiredMigration": "0023_ai_model_suitability.sql",
}


def example(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


def profile(
    *,
    reviewed_at: datetime = STARTED - timedelta(hours=2),
    valid_until: datetime = STARTED + timedelta(days=7),
) -> dict[str, object]:
    document = example("customer-sustained-workload-profile.json")
    metadata = document["metadata"]
    spec = document["spec"]
    metadata["reviewedAt"] = workload._timestamp(reviewed_at)
    metadata["validUntil"] = workload._timestamp(valid_until)
    spec["release"]["sourceRevision"] = REVISION
    without_id = dict(metadata)
    without_id.pop("id", None)
    metadata["id"] = workload._profile_identifier(without_id, spec)
    return document


def raw_result(
    *,
    probe_missed: int = 0,
    workflow_missed: int = 0,
    api_failures: int = 0,
    receiver_failures: int = 0,
    workflow_failures: int = 0,
    api_latency: int = 25,
    receiver_latency: int = 35,
    workflow_latency: int = 600,
) -> workload.RawWorkloadResult:
    probe_attempt_count = 1800 - probe_missed
    probes = tuple(
        workload.ProbeAttempt(
            api_success=index >= api_failures,
            api_latency_milliseconds=api_latency,
            receiver_success=index >= receiver_failures,
            receiver_latency_milliseconds=receiver_latency,
        )
        for index in range(probe_attempt_count)
    )
    workflow_attempt_count = 15 - workflow_missed
    workflows = tuple(
        workload.WorkflowAttempt(
            accepted=True,
            completed=index >= workflow_failures,
            poll_attempts=3,
            completion_milliseconds=workflow_latency,
        )
        for index in range(workflow_attempt_count)
    )
    return workload.RawWorkloadResult(
        started_at=STARTED,
        completed_at=COMPLETED,
        actual_duration_milliseconds=900_000,
        target_probe_cycles=1800,
        target_workflows=15,
        probe_attempts=probes,
        probe_scheduler_missed=probe_missed,
        probe_scheduler_lags_milliseconds=tuple(2 for _ in range(1800)),
        workflow_attempts=workflows,
        workflow_scheduler_missed=workflow_missed,
        workflow_scheduler_lags_milliseconds=tuple(2 for _ in range(15)),
    )


def build(
    selected_profile: dict[str, object] | None = None,
    raw: workload.RawWorkloadResult | None = None,
) -> dict[str, object]:
    return dict(
        workload.build_report(
            profile=selected_profile or profile(),
            raw=raw or raw_result(),
            api_target_digest=workload._digest(API_TARGET),
            otlp_target_digest=workload._digest(OTLP_TARGET),
            api_ca_source="custom",
            otlp_ca_source="custom",
        )
    )


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.api_target_digest = workload._digest(API_TARGET)
        self.otlp_target_digest = workload._digest(OTLP_TARGET)
        self.api_ca_source = "custom"
        self.otlp_ca_source = "custom"
        self.resource_validated = False
        self.__class__.instances.append(self)

    def validate_resource(self) -> None:
        self.resource_validated = True


class _SchedulerClient:
    def __init__(self, *, probe_delay: float = 0.0) -> None:
        self.probe_delay = probe_delay
        self.probe_calls = 0

    def probe_cycle_measurement(self) -> tuple[bool, int, bool, int]:
        self.probe_calls += 1
        if self.probe_delay:
            time.sleep(self.probe_delay)
        return True, 1, True, 1

    def start_workflow(self, phase: str) -> tuple[str, float]:
        return "inv_" + phase, time.monotonic()

    def finish_workflow(
        self,
        investigation_id: str,
        started: float,
        maximum_milliseconds: int,
        sleeper,
    ) -> dict[str, int]:
        del investigation_id, started, maximum_milliseconds, sleeper
        return {
            "workflowSubmitted": 1,
            "workflowCompleted": 1,
            "workflowFailures": 0,
            "workflowPollAttempts": 1,
            "workflowCompletionMilliseconds": 1,
        }


class CustomerSustainedWorkloadTests(unittest.TestCase):
    def test_contract_examples_are_schema_semantic_and_sdk_valid(self) -> None:
        profile_schema = json.loads(
            (
                ROOT
                / "contracts/schemas/customer-sustained-workload-profile.schema.json"
            ).read_text(encoding="utf-8")
        )
        report_schema = json.loads(
            (
                ROOT
                / "contracts/schemas/customer-sustained-workload-qualification-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        profile_example = example("customer-sustained-workload-profile.json")
        report_example = example(
            "customer-sustained-workload-qualification-report.json"
        )
        Draft202012Validator(
            profile_schema, format_checker=FormatChecker()
        ).validate(profile_example)
        Draft202012Validator(
            report_schema, format_checker=FormatChecker()
        ).validate(report_example)
        workload.validate_profile(profile_example)
        workload.validate_report_document(report_example)
        typed_profile = CustomerSustainedWorkloadProfile.from_dict(profile_example)
        typed_report = CustomerSustainedWorkloadQualificationReport.from_dict(
            report_example
        )
        self.assertEqual(typed_profile.release, profile_example["spec"]["release"])
        self.assertEqual(typed_profile.to_dict(), profile_example)
        self.assertEqual(typed_report.status, "qualified")
        self.assertEqual(typed_report.to_dict(), report_example)

    def test_successful_result_is_qualified_minimized_and_expiry_bounded(self) -> None:
        selected_profile = profile()
        report = build(selected_profile)
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["passedChecks"], 22)
        self.assertLessEqual(
            workload._parse_time(report["metadata"]["validUntil"], "invalid"),
            workload._parse_time(
                selected_profile["metadata"]["validUntil"], "invalid"
            ),
        )
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (
            "customer-qualification",
            "capacity-operator",
            "res_0123456789abcdef0123456789abcdef",
            "customer-sustained-workload",
            API_TARGET,
            OTLP_TARGET,
        ):
            self.assertNotIn(forbidden, serialized)

    def test_scheduler_and_path_failures_cannot_qualify(self) -> None:
        report = build(
            raw=raw_result(
                probe_missed=2,
                workflow_missed=1,
                api_failures=1,
                receiver_failures=1,
                workflow_failures=1,
            )
        )
        self.assertEqual(report["spec"]["status"], "not-qualified")
        checks = {item["id"]: item["status"] for item in report["spec"]["checks"]}
        self.assertEqual(checks["probe-scheduler-attainment"], "failed")
        self.assertEqual(checks["workflow-scheduler-attainment"], "failed")
        self.assertEqual(checks["api-success-attainment"], "failed")
        self.assertEqual(checks["receiver-success-attainment"], "failed")
        self.assertEqual(checks["workflow-completion-attainment"], "failed")

    def test_rehashed_measurement_and_check_tampering_is_rejected(self) -> None:
        report = build()
        tampered = copy.deepcopy(report)
        tampered["spec"]["measurements"]["probes"]["attemptedCycles"] = 1799
        metadata = dict(tampered["metadata"])
        metadata.pop("id")
        tampered["metadata"]["id"] = workload._report_identifier(
            metadata, tampered["spec"]
        )
        with self.assertRaisesRegex(
            workload.CustomerSustainedWorkloadError,
            "customer-sustained-workload.report.measurements-invalid",
        ):
            workload.validate_report_document(tampered)

        tampered = copy.deepcopy(report)
        tampered["spec"]["checks"][0] = {
            "id": "source-binding",
            "status": "failed",
            "errorCode": "customer-sustained-workload.source.invalid",
        }
        metadata = dict(tampered["metadata"])
        metadata.pop("id")
        tampered["metadata"]["id"] = workload._report_identifier(
            metadata, tampered["spec"]
        )
        with self.assertRaisesRegex(
            workload.CustomerSustainedWorkloadError,
            "customer-sustained-workload.report.invalid",
        ):
            workload.validate_report_document(tampered)

    def test_profile_relationships_and_content_identity_are_fail_closed(self) -> None:
        invalid = profile()
        invalid["spec"]["objective"]["maximumApiP95LatencyMilliseconds"] = 6000
        metadata = dict(invalid["metadata"])
        metadata.pop("id")
        invalid["metadata"]["id"] = workload._profile_identifier(
            metadata, invalid["spec"]
        )
        with self.assertRaisesRegex(
            workload.CustomerSustainedWorkloadError,
            "customer-sustained-workload.objective.invalid",
        ):
            workload.validate_profile(invalid)

        invalid = profile()
        invalid["spec"]["targets"]["apiBaseUrl"] = "https://user@example.test"
        metadata = dict(invalid["metadata"])
        metadata.pop("id")
        invalid["metadata"]["id"] = workload._profile_identifier(
            metadata, invalid["spec"]
        )
        with self.assertRaisesRegex(
            workload.CustomerSustainedWorkloadError,
            "customer-sustained-workload.profile.invalid",
        ):
            workload.validate_profile(invalid)

        invalid = profile()
        invalid["metadata"]["actorId"] = "changed-operator"
        with self.assertRaisesRegex(
            workload.CustomerSustainedWorkloadError,
            "customer-sustained-workload.profile.invalid",
        ):
            workload.validate_profile(invalid)

    def test_explicit_enable_is_checked_before_any_input_io(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            with self.assertRaisesRegex(
                workload.CustomerSustainedWorkloadError,
                "customer-sustained-workload.traffic.enable-required",
            ):
                workload.generate_report(
                    allow_traffic=False,
                    profile_path=missing,
                    api_token_file=missing,
                    otlp_token_file=missing,
                    otlp_client_cert_file=missing,
                    otlp_client_key_file=missing,
                    output=missing,
                )

    def test_protected_profile_rejects_permissions_and_symlinks(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX permission semantics required")
        with tempfile.TemporaryDirectory() as temporary:
            profile_path = Path(temporary) / "profile.json"
            profile_path.write_text(json.dumps(profile()), encoding="utf-8")
            profile_path.chmod(0o644)
            with self.assertRaisesRegex(
                workload.CustomerSustainedWorkloadError,
                "customer-sustained-workload.profile.unreadable",
            ):
                workload.load_profile(profile_path)
            profile_path.chmod(0o400)
            with self.assertRaisesRegex(
                workload.CustomerSustainedWorkloadError,
                "customer-sustained-workload.profile.unreadable",
            ):
                workload.load_profile(profile_path)
            profile_path.chmod(0o600)
            self.assertEqual(
                workload.load_profile(profile_path)["kind"], workload.PROFILE_KIND
            )
            linked = Path(temporary) / "linked.json"
            linked.symlink_to(profile_path)
            with self.assertRaisesRegex(
                workload.CustomerSustainedWorkloadError,
                "customer-sustained-workload.profile.unreadable",
            ):
                workload.load_profile(linked)

    def test_profile_id_command_accepts_document_without_previous_id(self) -> None:
        selected_profile = profile()
        selected_profile["metadata"].pop("id")
        expected = workload._profile_identifier(
            selected_profile["metadata"], selected_profile["spec"]
        )
        with tempfile.TemporaryDirectory() as temporary:
            profile_path = Path(temporary) / "profile.json"
            profile_path.write_text(json.dumps(selected_profile), encoding="utf-8")
            profile_path.chmod(0o600)
            with patch("builtins.print") as output:
                self.assertEqual(
                    workload.main(["profile-id", "--profile", str(profile_path)]),
                    0,
                )
            output.assert_called_once_with(expected)

    def test_runner_accounts_for_slots_and_skips_late_probe_bursts(self) -> None:
        clients: list[_SchedulerClient] = []

        def factory() -> _SchedulerClient:
            client = _SchedulerClient(probe_delay=0.45 if not clients else 0.0)
            clients.append(client)
            return client

        result = workload._run_sustained_workload(
            duration_seconds=1,
            probe_cycles_per_second=5,
            probe_concurrency=1,
            workflow_submissions_per_minute=60,
            workflow_concurrency=1,
            maximum_scheduler_lag_milliseconds=100,
            maximum_workflow_completion_milliseconds=1000,
            client_factory=factory,
            clock=lambda: STARTED,
        )
        self.assertEqual(result.target_probe_cycles, 5)
        self.assertEqual(result.target_workflows, 1)
        self.assertGreater(result.probe_scheduler_missed, 0)
        self.assertLess(clients[0].probe_calls, result.target_probe_cycles)
        self.assertEqual(
            len(result.probe_attempts) + result.probe_scheduler_missed,
            result.target_probe_cycles,
        )
        self.assertEqual(
            len(result.workflow_attempts) + result.workflow_scheduler_missed,
            result.target_workflows,
        )
        self.assertEqual(len(result.probe_scheduler_lags_milliseconds), 5)
        self.assertEqual(len(result.workflow_scheduler_lags_milliseconds), 1)

    def test_processing_client_returns_separate_api_and_receiver_latency(self) -> None:
        client = object.__new__(processing.ProcessingClient)
        client.api_opener = object()
        client.otlp_opener = object()
        client.api_base_url = API_TARGET
        client.otlp_base_url = OTLP_TARGET
        client.api_token = "api-token"
        client.otlp_token = "otlp-token"
        client.request_timeout_milliseconds = 1000
        client._runtime_identity_valid = lambda payload: payload == b"{}"
        client._metric_payload = lambda: b"metric"
        responses = (
            (200, "application/json", b"{}"),
            (200, "application/x-protobuf", b""),
        )
        with (
            patch.object(processing, "_request", side_effect=responses),
            patch.object(
                processing.time,
                "monotonic",
                side_effect=(0.0, 0.012, 1.0, 1.034),
            ),
        ):
            measured = client.probe_cycle_measurement()
        self.assertEqual(measured, (True, 12, True, 35))

    def test_generate_uses_validated_resource_and_writes_minimized_report(self) -> None:
        selected_profile = profile()
        _FakeClient.instances = []
        runner_arguments: dict[str, object] = {}

        def runner(**kwargs: object) -> workload.RawWorkloadResult:
            runner_arguments.update(kwargs)
            return raw_result()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(selected_profile), encoding="utf-8")
            profile_path.chmod(0o600)
            protected = [root / name for name in ("api", "otlp", "cert", "key")]
            for path in protected:
                path.write_text("protected-input\n", encoding="ascii")
                path.chmod(0o600)
            output = root / "report.json"
            with (
                patch.object(workload, "_source_identity", return_value=(REVISION, False)),
                patch.object(workload, "_repository_identity", return_value=REPOSITORY),
            ):
                report = workload.generate_report(
                    allow_traffic=True,
                    profile_path=profile_path,
                    api_token_file=protected[0],
                    otlp_token_file=protected[1],
                    otlp_client_cert_file=protected[2],
                    otlp_client_key_file=protected[3],
                    output=output,
                    api_ca_file=root / "api-ca",
                    otlp_ca_file=root / "otlp-ca",
                    runner=runner,
                    client_factory=_FakeClient,
                    now=STARTED,
                )
            self.assertTrue(_FakeClient.instances[0].resource_validated)
            self.assertEqual(
                _FakeClient.instances[0].kwargs["qualification_id"],
                "customer-sustained-workload",
            )
            self.assertEqual(runner_arguments["duration_seconds"], 900)
            self.assertEqual(report["spec"]["status"], "qualified")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
            self.assertEqual(output.stat().st_mode & 0o777, 0o644)
            self.assertNotIn("protected-input", output.read_text(encoding="utf-8"))

    def test_offline_verifier_rebinds_profile_source_targets_and_expiry(self) -> None:
        selected_profile = profile()
        report = build(selected_profile)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_path = root / "profile.json"
            report_path = root / "report.json"
            profile_path.write_text(json.dumps(selected_profile), encoding="utf-8")
            profile_path.chmod(0o600)
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with (
                patch.object(workload, "_source_identity", return_value=(REVISION, False)),
                patch.object(workload, "_repository_identity", return_value=REPOSITORY),
            ):
                verified = workload.verify_report(
                    profile_path=profile_path,
                    report_path=report_path,
                    require_qualified=True,
                    now=COMPLETED + timedelta(minutes=1),
                )
                self.assertEqual(verified, report)
                with self.assertRaisesRegex(
                    workload.CustomerSustainedWorkloadError,
                    "customer-sustained-workload.report.expired",
                ):
                    workload.verify_report(
                        profile_path=profile_path,
                        report_path=report_path,
                        require_qualified=True,
                        now=COMPLETED + timedelta(days=1, seconds=1),
                    )

            crossed = profile()
            crossed["spec"]["targets"]["apiBaseUrl"] = "https://other.example.test"
            metadata = dict(crossed["metadata"])
            metadata.pop("id")
            crossed["metadata"]["id"] = workload._profile_identifier(
                metadata, crossed["spec"]
            )
            profile_path.write_text(json.dumps(crossed), encoding="utf-8")
            profile_path.chmod(0o600)
            with (
                patch.object(workload, "_source_identity", return_value=(REVISION, False)),
                patch.object(workload, "_repository_identity", return_value=REPOSITORY),
            ):
                with self.assertRaisesRegex(
                    workload.CustomerSustainedWorkloadError,
                    "customer-sustained-workload.report.binding-mismatch",
                ):
                    workload.verify_report(
                        profile_path=profile_path,
                        report_path=report_path,
                        now=COMPLETED + timedelta(minutes=1),
                    )

            selected_profile = profile(valid_until=COMPLETED + timedelta(hours=2))
            report = build(selected_profile)
            report["metadata"]["validUntil"] = workload._timestamp(
                COMPLETED + timedelta(hours=3)
            )
            metadata = dict(report["metadata"])
            metadata.pop("id")
            report["metadata"]["id"] = workload._report_identifier(
                metadata, report["spec"]
            )
            workload.validate_report_document(report)
            profile_path.write_text(json.dumps(selected_profile), encoding="utf-8")
            profile_path.chmod(0o600)
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with (
                patch.object(workload, "_source_identity", return_value=(REVISION, False)),
                patch.object(workload, "_repository_identity", return_value=REPOSITORY),
            ):
                with self.assertRaisesRegex(
                    workload.CustomerSustainedWorkloadError,
                    "customer-sustained-workload.report.binding-mismatch",
                ):
                    workload.verify_report(
                        profile_path=profile_path,
                        report_path=report_path,
                        now=COMPLETED + timedelta(minutes=1),
                    )


if __name__ == "__main__":
    unittest.main()
