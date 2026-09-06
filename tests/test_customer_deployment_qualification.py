from __future__ import annotations

import copy
import hashlib
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

import deployment_diagnostics as diagnostics  # noqa: E402
import deployment_preflight as preflight  # noqa: E402
import qualify_customer_continuity as continuity  # noqa: E402
import qualify_customer_credential_broker as customer_credential_broker  # noqa: E402
import qualify_customer_deployment as qualification  # noqa: E402
import qualify_customer_otlp_receiver as customer_otlp_receiver  # noqa: E402
import qualify_customer_oidc as oidc  # noqa: E402
import qualify_customer_policy as customer_policy  # noqa: E402
import qualify_customer_processing_continuity as processing  # noqa: E402
import qualify_customer_postgresql_continuity as postgresql  # noqa: E402
from infra_intelligence_sdk import CustomerDeploymentQualificationReport  # noqa: E402
from tests.test_customer_continuity_qualification import (  # noqa: E402
    COMPLETED,
    EVICTED,
    IMAGE_DIGEST,
    RECOVERED,
    REPOSITORY,
    REVISION,
    STARTED,
    ingress_report,
    observation,
)
from tests.test_customer_processing_continuity import (  # noqa: E402
    observation as processing_observation,
    phase as processing_phase,
)
from tests.test_customer_postgresql_continuity import (  # noqa: E402
    observation as postgresql_observation,
    objective as postgresql_objective,
    phase as postgresql_phase,
)


CONTEXT = "customer-context"
NAMESPACE = "iip-system"
RELEASE = "iip"
DEPLOYMENT = "iip-infra-intelligence"
WORKER_DEPLOYMENT = "iip-infra-intelligence-worker"
RECEIVER_DEPLOYMENT = "iip-infra-intelligence-otlp-receiver"
PREFLIGHT_AT = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
HEALTH_AT = datetime(2026, 9, 6, 12, 37, tzinfo=timezone.utc)
QUALIFIED_AT = datetime(2026, 9, 6, 12, 38, tzinfo=timezone.utc)
CLUSTER = {
    "kubernetesVersion": "v1.36.1",
    "kubectlVersion": "v1.36.1",
    "clusterBindingDigest": "sha256:" + "9" * 64,
    "namespaceDigest": preflight._digest(NAMESPACE),
}


def _digest(document: object) -> str:
    return "sha256:" + hashlib.sha256(
        (json.dumps(document, indent=2) + "\n").encode("utf-8")
    ).hexdigest()


def live_preflight() -> dict[str, object]:
    report = json.loads(
        (
            ROOT
            / "contracts/examples/customer-deployment-preflight-report.json"
        ).read_text(encoding="utf-8")
    )
    metadata = report["metadata"]
    spec = report["spec"]
    metadata.update(
        {
            "generatedAt": "2026-09-06T12:00:00Z",
            "sourceRevision": REVISION,
            "sourceDirty": False,
        }
    )
    spec["environment"] = {
        "mode": "cluster",
        "platform": "linux/amd64",
        "pythonVersion": "3.12.10",
        "helmVersion": "v4.1.3",
        **CLUSTER,
    }
    dependencies = spec["dependencies"]
    dependencies.update(
        {
            "observedCount": dependencies["configuredCount"],
            "missingCount": 0,
            "invalidCount": 0,
            "unavailableCount": 0,
            "observationDigest": "sha256:" + "8" * 64,
            "verificationStatus": "passed",
        }
    )
    for check in spec["checks"]:
        check["status"] = "passed"
        check.pop("errorCode", None)
    spec["status"] = "install-ready"
    spec["summary"] = {
        "totalChecks": 23,
        "passedChecks": 23,
        "failedChecks": 0,
        "notRunChecks": 0,
        "overallStatus": "install-ready",
    }
    metadata["id"] = preflight._report_identifier(
        source_revision=REVISION,
        source_dirty=False,
        profile=spec["profile"],
        environment=spec["environment"],
        dependencies=spec["dependencies"],
        checks=spec["checks"],
    )
    preflight.validate_report_document(report)
    return report


def healthy_diagnostic() -> dict[str, object]:
    report = json.loads(
        (
            ROOT / "contracts/examples/deployment-diagnostic-report.json"
        ).read_text(encoding="utf-8")
    )
    binding, identity = diagnostics._target(
        context=CONTEXT,
        namespace=NAMESPACE,
        release_name=RELEASE,
        image_digest=IMAGE_DIGEST,
    )
    report["metadata"].update(
        {
            "generatedAt": "2026-09-06T12:37:00Z",
            "sourceRevision": REVISION,
            "sourceDirty": False,
        }
    )
    report["spec"]["targetBindingDigest"] = binding
    report["spec"]["expectedIdentity"] = identity
    report["spec"]["environment"]["observedAt"] = "2026-09-06T12:37:00Z"
    for component in report["spec"]["components"]:
        if component["state"] != "not-observed":
            component["identity"] = identity
            component["identityStatus"] = "match"
    diagnostics.validate_report(report)
    return report


def continuity_inputs() -> tuple[dict[str, object], dict[str, object]]:
    ingress_document = ingress_report()
    continuity_document = continuity.build_report(
        revision=REVISION,
        repository=REPOSITORY,
        image_digest=IMAGE_DIGEST,
        ingress_report=ingress_document,
        ingress_report_digest="sha256:" + "7" * 64,
        context=CONTEXT,
        namespace=NAMESPACE,
        deployment_name=DEPLOYMENT,
        kubernetes_version="v1.36.1",
        before=observation(pod_uid="old-uid"),
        after=observation(pod_uid="new-uid"),
        started_at=STARTED,
        eviction_started_at=EVICTED,
        recovered_at=RECOVERED,
        completed_at=COMPLETED,
        recovery_seconds=5,
        sample_count=721,
        interval_milliseconds=500,
        minimum_window_seconds=300,
        minimum_baseline_seconds=60,
        minimum_post_recovery_seconds=60,
        minimum_availability_basis_points=9990,
        maximum_p95_latency_milliseconds=2000,
        request_timeout_milliseconds=2000,
        maximum_recovery_seconds=120,
    )
    return ingress_document, continuity_document


def oidc_report() -> dict[str, object]:
    profile = json.loads(
        (
            ROOT / "contracts/examples/customer-oidc-qualification-profile.json"
        ).read_text(encoding="utf-8")
    )
    profile["spec"]["oidc"]["browser"]["redirectUri"] = (
        "https://iip.example.test/console"
    )
    oidc_profile = profile["spec"]["oidc"]
    browser = oidc_profile["browser"]
    metadata = {
        "issuer": oidc_profile["issuer"],
        "authorization_endpoint": browser["authorizationEndpoint"],
        "token_endpoint": browser["tokenEndpoint"],
        "jwks_uri": oidc_profile["jwksUrl"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
    return oidc.build_report(
        revision=REVISION,
        source_dirty=False,
        profile=profile,
        api_base_url="https://iip.example.test",
        issuer_metadata=metadata,
        subject={
            **REPOSITORY,
            "contractsApiVersion": oidc.API_VERSION,
            "sourceRevision": REVISION,
            "imageDigest": IMAGE_DIGEST,
        },
        started_at=datetime(2026, 9, 6, 12, 10, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 6, 12, 10, 4, tzinfo=timezone.utc),
        discovery_response_bytes=812,
        jwks_response_bytes=438,
        jwks_key_count=2,
        token_lifetime_seconds=900,
        token_remaining_seconds=780,
    )


def policy_report() -> dict[str, object]:
    profile = json.loads(
        (
            ROOT / "contracts/examples/customer-policy-qualification-profile.json"
        ).read_text(encoding="utf-8")
    )
    profile["metadata"]["reviewedAt"] = "2026-09-06T11:00:00Z"
    return customer_policy.build_report(
        revision=REVISION,
        profile=profile,
        image_digest=IMAGE_DIGEST,
        started_at=datetime(2026, 9, 6, 12, 20, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 6, 12, 20, 1, tzinfo=timezone.utc),
        maximum_latency_milliseconds=84,
        observations={identifier: True for identifier in customer_policy.CHECK_IDS},
    )


def credential_broker_report() -> dict[str, object]:
    profile = json.loads(
        (
            ROOT
            / "contracts/examples/customer-credential-broker-qualification-profile.json"
        ).read_text(encoding="utf-8")
    )
    profile["metadata"]["reviewedAt"] = "2026-09-06T11:00:00Z"
    return customer_credential_broker.build_report(
        revision=REVISION,
        profile=profile,
        image_digest=IMAGE_DIGEST,
        ca_bundle_digest="sha256:" + "2" * 64,
        started_at=datetime(2026, 9, 6, 12, 25, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 6, 12, 25, 1, tzinfo=timezone.utc),
        maximum_latency_milliseconds=84,
        minimum_remaining_lease_seconds=120,
        observations={
            identifier: True for identifier in customer_credential_broker.CHECK_IDS
        },
    )


def otlp_receiver_report() -> dict[str, object]:
    profile = json.loads(
        (
            ROOT
            / "contracts/examples/customer-otlp-receiver-qualification-profile.json"
        ).read_text(encoding="utf-8")
    )
    profile["metadata"]["reviewedAt"] = "2026-09-06T11:00:00Z"
    return customer_otlp_receiver.build_report(
        revision=REVISION,
        repository=REPOSITORY,
        image_digest=IMAGE_DIGEST,
        api_target_digest=continuity._digest_value("https://iip.example.test"),
        profile=profile,
        api_ca_digest="sha256:" + "7" * 64,
        receiver_ca_digest="sha256:" + "8" * 64,
        client_certificate_digest="sha256:" + "9" * 64,
        started_at=datetime(2026, 9, 6, 12, 26, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 6, 12, 26, 3, tzinfo=timezone.utc),
        api_identity_valid=True,
        credentials_separate=True,
        direct_receiver_accepted_count=3,
        result=customer_otlp_receiver.ProbeResult(
            config_valid=True,
            collector_started=True,
            accepted={signal: True for signal in customer_otlp_receiver.SIGNALS},
            delivered={signal: 1 for signal in customer_otlp_receiver.SIGNALS},
            send_failed={signal: 0 for signal in customer_otlp_receiver.SIGNALS},
            queue_size={signal: 0 for signal in customer_otlp_receiver.SIGNALS},
            latency_milliseconds={signal: 900 for signal in customer_otlp_receiver.SIGNALS},
        ),
    )


def processing_report(
    continuity_document: dict[str, object],
) -> dict[str, object]:
    return processing.build_report(
        revision=REVISION,
        repository=REPOSITORY,
        image_digest=IMAGE_DIGEST,
        api_target_digest=continuity_document["spec"]["bindings"][
            "targetBindingDigest"
        ],
        otlp_target_digest=processing._digest_value(
            "https://otlp.iip.example.com:4318"
        ),
        context=CONTEXT,
        namespace=NAMESPACE,
        profile_digest="sha256:" + "5" * 64,
        worker_deployment=WORKER_DEPLOYMENT,
        receiver_deployment=RECEIVER_DEPLOYMENT,
        kubernetes_version="v1.36.1",
        api_ca_source="custom",
        otlp_ca_source="custom",
        objective={
            "minimumProbeAttemptsPerPhase": 20,
            "probeIntervalMilliseconds": 250,
            "maximumWorkflowCompletionMilliseconds": 60_000,
            "maximumRecoveryMilliseconds": 120_000,
            "requestTimeoutMilliseconds": 2_000,
        },
        phases=[
            processing_phase("baseline", reduced=False),
            processing_phase("worker-disruption", reduced=True),
            processing_phase("receiver-disruption", reduced=True),
            processing_phase("recovery", reduced=False),
        ],
        worker_before=processing_observation("worker-old"),
        worker_during=processing_observation("worker-survivor", ready=1),
        worker_after=processing_observation("worker-new"),
        worker_recovery_milliseconds=4_000,
        receiver_before=processing_observation("receiver-old"),
        receiver_during=processing_observation("receiver-survivor", ready=1),
        receiver_after=processing_observation("receiver-new"),
        receiver_recovery_milliseconds=3_500,
        started_at=datetime(2026, 9, 6, 12, 36, 10, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 6, 12, 36, 50, tzinfo=timezone.utc),
    )


def postgresql_report(processing_document: dict[str, object]) -> dict[str, object]:
    bindings = processing_document["spec"]["bindings"]
    return postgresql.build_report(
        revision=REVISION,
        repository={
            "applicationVersion": "0.84.0",
            "chartVersion": "0.87.0",
            "requiredMigration": "0023_ai_model_suitability.sql",
        },
        image_digest=IMAGE_DIGEST,
        api_target_digest=bindings["apiTargetBindingDigest"],
        otlp_target_digest=bindings["otlpTargetBindingDigest"],
        database_target_digest="sha256:" + "4" * 64,
        context=CONTEXT,
        namespace=NAMESPACE,
        profile_digest="sha256:" + "3" * 64,
        api_ca_source="custom",
        otlp_ca_source="custom",
        database_client_identity="password",
        objective=postgresql_objective(),
        initial=postgresql_observation(7, server="1"),
        promoted=postgresql_observation(8, server="2"),
        connection_attempts=8,
        connection_failures=2,
        promotion_milliseconds=5_000,
        phases=[postgresql_phase(identifier) for identifier in postgresql.PHASES],
        started_at=datetime(2026, 9, 6, 12, 36, 51, tzinfo=timezone.utc),
        promotion_wait_started_at=datetime(2026, 9, 6, 12, 36, 52, tzinfo=timezone.utc),
        promotion_observed_at=datetime(2026, 9, 6, 12, 36, 57, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 6, 12, 36, 59, tzinfo=timezone.utc),
    )


def inputs() -> tuple[dict[str, object], ...]:
    ingress_document, continuity_document = continuity_inputs()
    processing_document = processing_report(continuity_document)
    return (
        live_preflight(),
        healthy_diagnostic(),
        ingress_document,
        oidc_report(),
        policy_report(),
        credential_broker_report(),
        otlp_receiver_report(),
        continuity_document,
        processing_document,
        postgresql_report(processing_document),
    )


def build(
    *,
    preflight_document: dict[str, object] | None = None,
    diagnostic_document: dict[str, object] | None = None,
    ingress_document: dict[str, object] | None = None,
    oidc_document: dict[str, object] | None = None,
    policy_document: dict[str, object] | None = None,
    credential_broker_document: dict[str, object] | None = None,
    otlp_receiver_document: dict[str, object] | None = None,
    continuity_document: dict[str, object] | None = None,
    processing_document: dict[str, object] | None = None,
    postgresql_document: dict[str, object] | None = None,
) -> dict[str, object]:
    documents = inputs()
    selected = (
        preflight_document or documents[0],
        diagnostic_document or documents[1],
        ingress_document or documents[2],
        oidc_document or documents[3],
        policy_document or documents[4],
        credential_broker_document or documents[5],
        otlp_receiver_document or documents[6],
        continuity_document or documents[7],
        processing_document or documents[8],
        postgresql_document or documents[9],
    )
    return qualification.build_report(
        preflight_report=selected[0],
        preflight_digest=_digest(selected[0]),
        diagnostic_report=selected[1],
        diagnostic_digest=_digest(selected[1]),
        ingress_report=selected[2],
        ingress_digest=_digest(selected[2]),
        oidc_report=selected[3],
        oidc_digest=_digest(selected[3]),
        policy_report=selected[4],
        policy_digest=_digest(selected[4]),
        credential_broker_report=selected[5],
        credential_broker_digest=_digest(selected[5]),
        otlp_receiver_report=selected[6],
        otlp_receiver_digest=_digest(selected[6]),
        continuity_report=selected[7],
        continuity_digest=_digest(selected[7]),
        processing_report=selected[8],
        processing_digest=_digest(selected[8]),
        postgresql_report=selected[9],
        postgresql_digest=_digest(selected[9]),
        context=CONTEXT,
        namespace=NAMESPACE,
        release_name=RELEASE,
        deployment_name=DEPLOYMENT,
        worker_deployment_name=WORKER_DEPLOYMENT,
        receiver_deployment_name=RECEIVER_DEPLOYMENT,
        image_digest=IMAGE_DIGEST,
        current_cluster_environment=CLUSTER,
        now=QUALIFIED_AT,
    )


class CustomerDeploymentQualificationTests(unittest.TestCase):
    def test_contract_example_is_schema_semantic_and_sdk_valid(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "contracts/schemas/customer-deployment-qualification-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        example = json.loads(
            (
                ROOT
                / "contracts/examples/customer-deployment-qualification-report.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        qualification.validate_report_document(example)
        self.assertEqual(
            CustomerDeploymentQualificationReport.from_dict(example).to_dict(),
            example,
        )

    def test_exact_bound_inputs_produce_minimized_qualified_report(self) -> None:
        report = build()
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(report["spec"]["summary"]["passedEvidence"], 10)
        serialized = json.dumps(report, sort_keys=True)
        for forbidden in (
            CONTEXT,
            NAMESPACE,
            DEPLOYMENT,
            WORKER_DEPLOYMENT,
            RECEIVER_DEPLOYMENT,
            "old-uid",
            "new-uid",
            "worker-old",
            "receiver-old",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_crossed_processing_target_is_rejected(self) -> None:
        documents = inputs()
        crossed = copy.deepcopy(documents[8])
        crossed["spec"]["bindings"]["apiTargetBindingDigest"] = (
            "sha256:" + "f" * 64
        )
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.processing.crossed",
        ):
            build(processing_document=crossed)

    def test_crossed_oidc_target_is_rejected(self) -> None:
        documents = inputs()
        crossed = copy.deepcopy(documents[3])
        crossed["spec"]["bindings"]["apiTargetBindingDigest"] = (
            "sha256:" + "f" * 64
        )
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.oidc.crossed",
        ):
            build(oidc_document=crossed)

    def test_crossed_database_target_is_rejected(self) -> None:
        documents = inputs()
        crossed = copy.deepcopy(documents[9])
        crossed["spec"]["bindings"]["otlpTargetBindingDigest"] = (
            "sha256:" + "f" * 64
        )
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.database.crossed",
        ):
            build(postgresql_document=crossed)

    def test_crossed_policy_subject_is_rejected(self) -> None:
        documents = inputs()
        crossed = copy.deepcopy(documents[4])
        crossed["spec"]["subject"]["imageDigest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.release.crossed",
        ):
            build(policy_document=crossed)

    def test_crossed_credential_broker_subject_is_rejected(self) -> None:
        documents = inputs()
        crossed = copy.deepcopy(documents[5])
        crossed["spec"]["subject"]["imageDigest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.release.crossed",
        ):
            build(credential_broker_document=crossed)

    def test_crossed_otlp_receiver_target_is_rejected(self) -> None:
        documents = inputs()
        crossed = copy.deepcopy(documents[6])
        crossed["spec"]["bindings"]["receiverEndpointBindingDigest"] = (
            "sha256:" + "f" * 64
        )
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.otlp-receiver.crossed",
        ):
            build(otlp_receiver_document=crossed)

    def test_crossed_source_and_current_cluster_are_rejected(self) -> None:
        crossed = healthy_diagnostic()
        crossed["metadata"]["sourceRevision"] = "f" * 40
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.source.crossed",
        ):
            build(diagnostic_document=crossed)
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.cluster.changed",
        ):
            documents = inputs()
            qualification.build_report(
                preflight_report=documents[0],
                preflight_digest=_digest(documents[0]),
                diagnostic_report=documents[1],
                diagnostic_digest=_digest(documents[1]),
                ingress_report=documents[2],
                ingress_digest=_digest(documents[2]),
                oidc_report=documents[3],
                oidc_digest=_digest(documents[3]),
                policy_report=documents[4],
                policy_digest=_digest(documents[4]),
                credential_broker_report=documents[5],
                credential_broker_digest=_digest(documents[5]),
                otlp_receiver_report=documents[6],
                otlp_receiver_digest=_digest(documents[6]),
                continuity_report=documents[7],
                continuity_digest=_digest(documents[7]),
                processing_report=documents[8],
                processing_digest=_digest(documents[8]),
                postgresql_report=documents[9],
                postgresql_digest=_digest(documents[9]),
                context=CONTEXT,
                namespace=NAMESPACE,
                release_name=RELEASE,
                deployment_name=DEPLOYMENT,
                worker_deployment_name=WORKER_DEPLOYMENT,
                receiver_deployment_name=RECEIVER_DEPLOYMENT,
                image_digest=IMAGE_DIGEST,
                current_cluster_environment={**CLUSTER, "clusterBindingDigest": "sha256:" + "6" * 64},
                now=QUALIFIED_AT,
            )

    def test_stale_or_non_post_continuity_evidence_cannot_qualify(self) -> None:
        stale = build()
        stale["spec"]["measurements"]["qualifiedAt"] = "2026-09-09T12:38:00Z"
        stale["metadata"]["generatedAt"] = "2026-09-09T12:38:00Z"
        stale["spec"]["measurements"]["oldestEvidenceAgeSeconds"] = 261480
        expected = qualification._expected_checks(stale)
        stale["spec"]["checks"] = expected
        failed = sum(item["status"] == "failed" for item in expected)
        stale["spec"]["status"] = "not-qualified"
        stale["spec"]["summary"].update(
            {
                "passedChecks": len(qualification.CHECK_IDS) - failed,
                "failedChecks": failed,
                "overallStatus": "not-qualified",
            }
        )
        metadata = dict(stale["metadata"])
        metadata.pop("id")
        stale["metadata"]["id"] = qualification._report_identifier(
            metadata, stale["spec"]
        )
        qualification.validate_report_document(stale)
        by_id = {item["id"]: item for item in stale["spec"]["checks"]}
        self.assertEqual(by_id["evidence-freshness"]["status"], "failed")

        before = healthy_diagnostic()
        before["metadata"]["generatedAt"] = "2026-09-06T12:29:00Z"
        before["spec"]["environment"]["observedAt"] = "2026-09-06T12:29:00Z"
        report = build(diagnostic_document=before)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        by_id = {item["id"]: item for item in report["spec"]["checks"]}
        self.assertEqual(by_id["post-continuity-health"]["status"], "failed")

    def test_rehashed_arithmetic_or_status_tampering_is_rejected(self) -> None:
        report = build()
        tampered = copy.deepcopy(report)
        tampered["spec"]["summary"]["passedChecks"] = 13
        metadata = dict(tampered["metadata"])
        metadata.pop("id")
        tampered["metadata"]["id"] = qualification._report_identifier(
            metadata, tampered["spec"]
        )
        with self.assertRaisesRegex(
            qualification.CustomerDeploymentQualificationError,
            "customer-deployment-qualification.report.summary-invalid",
        ):
            qualification.validate_report_document(tampered)

    def test_qualify_observes_current_cluster_and_retains_failed_status(self) -> None:
        documents = inputs()
        rejected = copy.deepcopy(documents[1])
        rejected["spec"]["status"] = "attention-required"
        selected = (
            documents[0],
            _digest(documents[0]),
            rejected,
            _digest(rejected),
            documents[2],
            _digest(documents[2]),
            documents[3],
            _digest(documents[3]),
            documents[4],
            _digest(documents[4]),
            documents[5],
            _digest(documents[5]),
            documents[6],
            _digest(documents[6]),
            documents[7],
            _digest(documents[7]),
            documents[8],
            _digest(documents[8]),
            documents[9],
            _digest(documents[9]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.json"
            with (
                patch.object(qualification, "_validate_inputs", return_value=selected),
                patch.object(
                    qualification.preflight,
                    "_cluster_environment",
                    return_value=(CLUSTER, {"id": "cluster-api", "status": "passed"}),
                ),
            ):
                report = qualification.qualify(
                    preflight_path=Path("preflight.json"),
                    diagnostic_path=Path("diagnostic.json"),
                    ingress_path=Path("ingress.json"),
                    oidc_path=Path("oidc.json"),
                    oidc_profile_path=Path("oidc-profile.json"),
                    oidc_api_base_url="https://iip.example.test",
                    policy_path=Path("policy.json"),
                    policy_profile_path=Path("policy-profile.json"),
                    policy_endpoint="https://policy.example.com/v1/data/iip/decision",
                    credential_broker_path=Path("credential-broker.json"),
                    credential_broker_profile_path=Path("credential-broker-profile.json"),
                    credential_broker_endpoint="https://credential-broker.example.com",
                    credential_broker_ca_bundle_path=Path("credential-broker-ca.pem"),
                    otlp_receiver_path=Path("otlp-receiver.json"),
                    otlp_receiver_profile_path=Path("otlp-receiver-profile.json"),
                    otlp_receiver_api_base_url="https://iip.example.test",
                    otlp_receiver_endpoint="https://otlp.iip.example.com:4318",
                    otlp_receiver_api_ca_path=Path("otlp-api-ca.pem"),
                    otlp_receiver_ca_path=Path("otlp-receiver-ca.pem"),
                    otlp_receiver_client_certificate_path=Path("otlp-client.crt"),
                    continuity_path=Path("continuity.json"),
                    processing_path=Path("processing.json"),
                    processing_profile_path=Path("processing-profile.json"),
                    processing_api_base_url="https://api.example.test",
                    processing_otlp_base_url="https://otlp.example.test:4318",
                    postgresql_path=Path("postgresql.json"),
                    postgresql_profile_path=Path("postgresql-profile.json"),
                    postgresql_database_host="database.example.test",
                    postgresql_database_port=5432,
                    values=[Path("values.yaml")],
                    context=CONTEXT,
                    namespace=NAMESPACE,
                    release_name=RELEASE,
                    deployment_name=DEPLOYMENT,
                    worker_deployment_name=WORKER_DEPLOYMENT,
                    receiver_deployment_name=RECEIVER_DEPLOYMENT,
                    image_digest=IMAGE_DIGEST,
                    output=output,
                    now=lambda: QUALIFIED_AT,
                )
            self.assertEqual(report["spec"]["status"], "not-qualified")
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
