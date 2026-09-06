from __future__ import annotations

import copy
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import qualify_customer_bedrock as qualification  # noqa: E402


REVISION = "a" * 40
IMAGE = "sha256:" + "a" * 64
STARTED = datetime(2026, 9, 8, 9, 4, tzinfo=timezone.utc)
COMPLETED = datetime(2026, 9, 8, 9, 5, tzinfo=timezone.utc)


def example(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


def live_report(profile: dict[str, object]) -> dict[str, object]:
    report = example("bedrock-converse-stream-instrumentation-compatibility-report.json")
    target = profile["spec"]["target"]
    report["metadata"]["generatedAt"] = "2026-09-08T09:04:30Z"
    report["metadata"]["sourceRevision"] = REVISION
    report["metadata"]["sourceDirty"] = False
    report["spec"]["qualificationLevel"] = "live-provider-interoperability"
    report["spec"]["profile"]["modelId"] = target["modelId"]
    report["spec"]["profile"]["region"] = target["region"]
    report["spec"]["profile"]["operation"] = target["operation"]
    report["spec"]["profile"]["invocationTarget"] = "aws-bedrock"
    report["spec"]["result"]["liveProviderVerified"] = True
    report["spec"]["result"]["streamingVerified"] = True
    report["spec"]["result"]["providerCallLatencyMilliseconds"] = 1432
    report["metadata"]["id"] = qualification._expected_compatibility_id(report)
    return report


class CustomerBedrockQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = example("customer-bedrock-qualification-profile.json")
        self.report = example("customer-bedrock-qualification-report.json")

    def _inputs(self, directory: Path) -> dict[str, Path]:
        paths = {
            "profile": directory / "profile.json",
            "credentials": directory / "credentials",
            "live": directory / "live-report.json",
            "report": directory / "report.json",
        }
        paths["profile"].write_text(json.dumps(self.profile), encoding="utf-8")
        paths["credentials"].write_text(
            "[iip-bedrock-qualification]\n"
            "aws_access_key_id = temporary-access-key-0001\n"
            "aws_secret_access_key = " + "s" * 40 + "\n"
            "aws_session_token = " + "t" * 64 + "\n",
            encoding="ascii",
        )
        os.chmod(paths["profile"], 0o600)
        os.chmod(paths["credentials"], 0o600)
        return paths

    def test_examples_are_closed_current_and_minimized(self) -> None:
        qualification.validate_profile(self.profile)
        qualification.validate_report_document(self.report)
        serialized = json.dumps(self.report)
        for forbidden in (
            "amazon.nova",
            "us-east-1",
            "customer-production",
            "iip-bedrock-qualification",
            "aws_access_key_id",
            "prompt",
            "response",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_report_identity_summary_and_validity_are_recomputed(self) -> None:
        changed = copy.deepcopy(self.report)
        changed["spec"]["checks"][0]["status"] = "failed"
        changed["spec"]["checks"][0]["errorCode"] = (
            "customer-bedrock-qualification.profile-binding.failed"
        )
        with self.assertRaises(qualification.CustomerBedrockQualificationError):
            qualification.validate_report_document(changed)

        changed = copy.deepcopy(self.report)
        changed["metadata"]["validUntil"] = "2026-09-10T09:05:00Z"
        with self.assertRaises(qualification.CustomerBedrockQualificationError):
            qualification.validate_report_document(changed)

        changed = copy.deepcopy(self.report)
        changed["spec"]["bindings"]["modelId"] = "secret-model"
        with self.assertRaises(qualification.CustomerBedrockQualificationError):
            qualification.validate_report_document(changed)

        changed = copy.deepcopy(self.report)
        changed["spec"]["measurements"]["providerCallLatencyMilliseconds"] = 60001
        metadata = dict(changed["metadata"])
        metadata.pop("id")
        changed["metadata"]["id"] = qualification._report_id(
            metadata, changed["spec"]
        )
        with self.assertRaises(qualification.CustomerBedrockQualificationError):
            qualification.validate_report_document(changed)

    def test_credentials_are_dedicated_temporary_and_protected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._inputs(Path(temporary))
            resolved = qualification.validate_credentials_file(
                paths["credentials"], "iip-bedrock-qualification"
            )
            self.assertEqual(resolved, paths["credentials"])

            os.chmod(paths["credentials"], 0o644)
            with self.assertRaises(qualification.CustomerBedrockQualificationError):
                qualification.validate_credentials_file(
                    paths["credentials"], "iip-bedrock-qualification"
                )
            os.chmod(paths["credentials"], 0o600)

            paths["credentials"].write_text(
                "[iip-bedrock-qualification]\n"
                "aws_access_key_id = temporary-access-key-0001\n"
                "aws_secret_access_key = " + "s" * 40 + "\n",
                encoding="ascii",
            )
            with self.assertRaises(qualification.CustomerBedrockQualificationError):
                qualification.validate_credentials_file(
                    paths["credentials"], "iip-bedrock-qualification"
                )

    def test_qualify_uses_exact_live_report_and_retains_no_sensitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._inputs(Path(temporary))
            raw = live_report(self.profile)
            calls: list[tuple[Path, str]] = []

            def runner(
                profile: dict[str, object], credential_path: Path, docker_bin: str
            ) -> dict[str, object]:
                self.assertEqual(profile, self.profile)
                calls.append((credential_path, docker_bin))
                return raw

            instants = iter((STARTED, COMPLETED))
            with patch.object(
                qualification,
                "_source_identity",
                side_effect=((REVISION, False), (REVISION, False)),
            ):
                report, observed = qualification.qualify(
                    profile_document=self.profile,
                    credentials_path=paths["credentials"],
                    image_digest=IMAGE,
                    allow_provider_call=True,
                    docker_bin="docker-test",
                    runner=runner,
                    clock=lambda: next(instants),
                )
            self.assertEqual(observed, raw)
            self.assertEqual(calls, [(paths["credentials"], "docker-test")])
            self.assertEqual(report["spec"]["status"], "qualified")
            self.assertEqual(report["spec"]["measurements"]["providerCallCount"], 1)
            self.assertEqual(
                report["spec"]["measurements"]["providerCallLatencyMilliseconds"],
                1432,
            )
            serialized = json.dumps(report)
            self.assertNotIn(self.profile["spec"]["target"]["modelId"], serialized)
            self.assertNotIn(self.profile["spec"]["target"]["region"], serialized)
            self.assertNotIn("temporary-access-key", serialized)

    def test_crossed_target_latency_and_offline_claim_are_rejected(self) -> None:
        raw = live_report(self.profile)
        crossed = copy.deepcopy(raw)
        crossed["spec"]["profile"]["modelId"] = "another.model-v1:0"
        crossed["metadata"]["id"] = qualification._expected_compatibility_id(crossed)
        with self.assertRaises(qualification.CustomerBedrockQualificationError):
            qualification.validate_live_report(self.profile, crossed, REVISION)

        slow = copy.deepcopy(raw)
        slow["spec"]["result"]["providerCallLatencyMilliseconds"] = 60001
        slow["metadata"]["id"] = qualification._expected_compatibility_id(slow)
        with self.assertRaises(qualification.CustomerBedrockQualificationError):
            qualification.validate_live_report(self.profile, slow, REVISION)

        offline = copy.deepcopy(raw)
        offline["spec"]["qualificationLevel"] = "offline-sdk-interoperability"
        offline["spec"]["profile"]["invocationTarget"] = "botocore-stubber"
        offline["spec"]["result"]["liveProviderVerified"] = False
        offline["metadata"]["id"] = qualification._expected_compatibility_id(offline)
        with self.assertRaises(qualification.CustomerBedrockQualificationError):
            qualification.validate_live_report(self.profile, offline, REVISION)

    def test_enable_image_clean_source_and_fresh_observation_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._inputs(Path(temporary))
            called = False

            def runner(*_: object) -> dict[str, object]:
                nonlocal called
                called = True
                return live_report(self.profile)

            with self.assertRaises(qualification.CustomerBedrockQualificationError):
                qualification.qualify(
                    profile_document=self.profile,
                    credentials_path=paths["credentials"],
                    image_digest=IMAGE,
                    allow_provider_call=False,
                    runner=runner,
                )
            self.assertFalse(called)

            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, True)
            ), self.assertRaises(qualification.CustomerBedrockQualificationError):
                qualification.qualify(
                    profile_document=self.profile,
                    credentials_path=paths["credentials"],
                    image_digest=IMAGE,
                    allow_provider_call=True,
                    runner=runner,
                )
            self.assertFalse(called)

            stale = live_report(self.profile)
            stale["metadata"]["generatedAt"] = "2026-09-07T09:04:30Z"
            stale["metadata"]["id"] = qualification._expected_compatibility_id(stale)
            instants = iter((STARTED, COMPLETED))
            with patch.object(
                qualification,
                "_source_identity",
                side_effect=((REVISION, False), (REVISION, False)),
            ), self.assertRaises(qualification.CustomerBedrockQualificationError):
                qualification.qualify(
                    profile_document=self.profile,
                    credentials_path=paths["credentials"],
                    image_digest=IMAGE,
                    allow_provider_call=True,
                    runner=lambda *_: stale,
                    clock=lambda: next(instants),
                )

    def test_offline_verifier_rebinds_profile_live_report_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._inputs(Path(temporary))
            raw = live_report(self.profile)
            report = qualification.build_report(
                profile_document=self.profile,
                live_report=raw,
                revision=REVISION,
                image_digest=IMAGE,
                started_at=STARTED,
                completed_at=COMPLETED,
                protected_credentials=True,
            )
            paths["live"].write_text(json.dumps(raw), encoding="utf-8")
            paths["report"].write_text(json.dumps(report), encoding="utf-8")
            os.chmod(paths["live"], 0o600)
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ):
                verified = qualification.verify_report(
                    report_path=paths["report"],
                    profile_path=paths["profile"],
                    live_report_path=paths["live"],
                    image_digest=IMAGE,
                    require_qualified=True,
                    now=COMPLETED + timedelta(hours=1),
                )
            self.assertEqual(verified["spec"]["status"], "qualified")

            raw["spec"]["result"]["providerCallLatencyMilliseconds"] = 1433
            raw["metadata"]["id"] = qualification._expected_compatibility_id(raw)
            paths["live"].write_text(json.dumps(raw), encoding="utf-8")
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ), self.assertRaises(qualification.CustomerBedrockQualificationError):
                qualification.verify_report(
                    report_path=paths["report"],
                    profile_path=paths["profile"],
                    live_report_path=paths["live"],
                    image_digest=IMAGE,
                    now=COMPLETED + timedelta(hours=1),
                )

            paths["live"].write_text(json.dumps(live_report(self.profile)), encoding="utf-8")
            with patch.object(
                qualification, "_source_identity", return_value=(REVISION, False)
            ), self.assertRaisesRegex(
                qualification.CustomerBedrockQualificationError,
                "report.expired",
            ):
                qualification.verify_report(
                    report_path=paths["report"],
                    profile_path=paths["profile"],
                    live_report_path=paths["live"],
                    image_digest=IMAGE,
                    now=COMPLETED + timedelta(days=2),
                )

    def test_times_are_aware_and_output_modes_are_explicit(self) -> None:
        raw = live_report(self.profile)
        with self.assertRaisesRegex(
            qualification.CustomerBedrockQualificationError,
            "report.invalid",
        ):
            qualification.build_report(
                profile_document=self.profile,
                live_report=raw,
                revision=REVISION,
                image_digest=IMAGE,
                started_at=STARTED.replace(tzinfo=None),
                completed_at=COMPLETED,
                protected_credentials=True,
            )

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            protected = directory / "protected.json"
            transportable = directory / "transportable.json"
            qualification._write_document(protected, {"value": 1}, mode=0o600)
            qualification._write_document(transportable, {"value": 1}, mode=0o644)
            self.assertEqual(stat.S_IMODE(protected.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(transportable.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
