#!/usr/bin/env python3
"""Aggregate exact customer install, identity, continuity, and database evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

import deployment_diagnostics as diagnostics
import deployment_preflight as preflight
import qualify_customer_continuity as continuity
import qualify_customer_credential_broker as customer_credential_broker
import qualify_customer_otlp_receiver as customer_otlp_receiver
import qualify_customer_oidc as oidc
import qualify_customer_policy as customer_policy
import qualify_customer_processing_continuity as processing
import qualify_customer_postgresql_continuity as postgresql
import qualify_ingress_availability as ingress


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts/schemas/customer-deployment-qualification-report.schema.json"
API_VERSION = "iip.platform/v1alpha1"
KIND = "CustomerDeploymentQualificationReport"
QUALIFICATION_LEVEL = "single-cluster-database-identity-policy-broker-receiver-prerequisites-v7"
REPORT_ID = re.compile(r"^cdq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_VALUES_FILES = 8
EVIDENCE_DEFINITIONS = (
    (
        "live-install-preflight",
        "CustomerDeploymentPreflightReport",
        "install-ready",
        "customer-deployment-qualification.preflight.not-install-ready",
    ),
    (
        "post-install-health",
        "DeploymentDiagnosticReport",
        "healthy",
        "customer-deployment-qualification.diagnostic.not-healthy",
    ),
    (
        "customer-ingress",
        "IngressAvailabilityQualificationReport",
        "qualified",
        "customer-deployment-qualification.ingress.not-qualified",
    ),
    (
        "customer-oidc",
        "CustomerOidcQualificationReport",
        "qualified",
        "customer-deployment-qualification.oidc.not-qualified",
    ),
    (
        "customer-policy",
        "CustomerPolicyQualificationReport",
        "qualified",
        "customer-deployment-qualification.policy.not-qualified",
    ),
    (
        "customer-credential-broker",
        "CustomerCredentialBrokerQualificationReport",
        "qualified",
        "customer-deployment-qualification.credential-broker.not-qualified",
    ),
    (
        "customer-otlp-receiver",
        "CustomerOtlpReceiverQualificationReport",
        "qualified",
        "customer-deployment-qualification.otlp-receiver.not-qualified",
    ),
    (
        "control-plane-continuity",
        "CustomerContinuityQualificationReport",
        "qualified",
        "customer-deployment-qualification.continuity.not-qualified",
    ),
    (
        "worker-receiver-processing",
        "CustomerProcessingContinuityQualificationReport",
        "qualified",
        "customer-deployment-qualification.processing.not-qualified",
    ),
    (
        "postgresql-primary-promotion",
        "CustomerPostgreSQLContinuityQualificationReport",
        "qualified",
        "customer-deployment-qualification.database.not-qualified",
    ),
)
CHECK_IDS = (
    "source-binding",
    "profile-binding",
    "exact-release-identity",
    "explicit-current-cluster",
    "live-install-preflight",
    "post-continuity-health",
    "customer-ingress",
    "customer-oidc",
    "customer-policy",
    "customer-credential-broker",
    "customer-otlp-receiver",
    "control-plane-continuity",
    "worker-receiver-processing",
    "postgresql-primary-promotion",
    "continuity-ingress-chain",
    "oidc-target-chain",
    "policy-binding-chain",
    "credential-broker-binding-chain",
    "otlp-receiver-binding-chain",
    "processing-target-chain",
    "database-target-chain",
    "evidence-order",
    "evidence-freshness",
    "minimized-output",
)
LIMITATIONS = (
    "single-customer-cluster",
    "planned-sequential-api-worker-receiver-pod-disruptions",
    "point-in-time-dependency-observation",
    "artifact-publication-signatures-vulnerabilities-not-qualified",
    "database-topology-fencing-and-rpo-not-qualified",
    "regional-database-disaster-recovery-not-qualified",
    "customer-oidc-interactive-policy-lifecycle-other-integrations-and-live-ai-not-qualified",
    "regional-slo-and-capacity-not-qualified",
    "design-partner-legal-brand-governance-not-qualified",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "baseUrl",
        "context",
        "credential",
        "deploymentName",
        "host",
        "namespace",
        "releaseName",
        "repository",
        "secret",
        "token",
        "url",
    }
)


class CustomerDeploymentQualificationError(RuntimeError):
    """A stable customer deployment qualification failure."""


def _fail(code: str) -> None:
    raise CustomerDeploymentQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("customer-deployment-qualification.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _load_document(path: Path, code: str) -> tuple[Mapping[str, Any], str]:
    candidate = path.expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file():
            _fail(code)
        payload = candidate.read_bytes()
    except OSError:
        _fail(code)
    if len(payload) > MAX_DOCUMENT_BYTES:
        _fail(code)
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(document, Mapping):
        _fail(code)
    return document, "sha256:" + hashlib.sha256(payload).hexdigest()


def _file_digest(path: Path, code: str) -> str:
    try:
        payload = path.expanduser().read_bytes()
    except OSError:
        _fail(code)
    if len(payload) > MAX_DOCUMENT_BYTES:
        _fail(code)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    candidate = path.expanduser()
    if candidate.is_symlink():
        _fail("customer-deployment-qualification.output.invalid")
    destination = candidate.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, destination)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _fail("customer-deployment-qualification.output.invalid")


def _report_identifier(
    metadata_without_id: Mapping[str, object], spec: Mapping[str, object]
) -> str:
    return "cdq_" + hashlib.sha256(
        _canonical({"metadata": metadata_without_id, "spec": spec})
    ).hexdigest()[:32]


def _check(identifier: str, passed: bool, error_code: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = error_code
    return result


def _report_status(document: Mapping[str, Any]) -> str:
    spec = _mapping(
        document.get("spec"), "customer-deployment-qualification.input.invalid"
    )
    status = spec.get("status")
    if not isinstance(status, str):
        _fail("customer-deployment-qualification.input.invalid")
    return status


def _evidence(
    *,
    identifier: str,
    contract_kind: str,
    success_status: str,
    error_code: str,
    document: Mapping[str, Any],
    digest: str,
) -> dict[str, str]:
    metadata = _mapping(
        document.get("metadata"), "customer-deployment-qualification.input.invalid"
    )
    report_id = metadata.get("id")
    status = _report_status(document)
    if not isinstance(report_id, str) or DIGEST.fullmatch(digest) is None:
        _fail("customer-deployment-qualification.input.invalid")
    result = {
        "id": identifier,
        "contractKind": contract_kind,
        "reportId": report_id,
        "reportDigest": digest,
        "observedStatus": status,
        "status": "passed" if status == success_status else "rejected",
    }
    if status != success_status:
        result["errorCode"] = error_code
    return result


def _subject_and_bindings(
    *,
    preflight_report: Mapping[str, Any],
    diagnostic_report: Mapping[str, Any],
    ingress_report: Mapping[str, Any],
    oidc_report: Mapping[str, Any],
    policy_report: Mapping[str, Any],
    credential_broker_report: Mapping[str, Any],
    otlp_receiver_report: Mapping[str, Any],
    continuity_report: Mapping[str, Any],
    processing_report: Mapping[str, Any],
    postgresql_report: Mapping[str, Any],
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    worker_deployment_name: str,
    receiver_deployment_name: str,
    image_digest: str,
    current_cluster_environment: Mapping[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    preflight_metadata = _mapping(
        preflight_report.get("metadata"),
        "customer-deployment-qualification.preflight.invalid",
    )
    preflight_spec = _mapping(
        preflight_report.get("spec"),
        "customer-deployment-qualification.preflight.invalid",
    )
    preflight_profile = _mapping(
        preflight_spec.get("profile"),
        "customer-deployment-qualification.preflight.invalid",
    )
    preflight_environment = _mapping(
        preflight_spec.get("environment"),
        "customer-deployment-qualification.preflight.invalid",
    )
    diagnostic_metadata = _mapping(
        diagnostic_report.get("metadata"),
        "customer-deployment-qualification.diagnostic.invalid",
    )
    diagnostic_spec = _mapping(
        diagnostic_report.get("spec"),
        "customer-deployment-qualification.diagnostic.invalid",
    )
    diagnostic_identity = _mapping(
        diagnostic_spec.get("expectedIdentity"),
        "customer-deployment-qualification.diagnostic.invalid",
    )
    ingress_metadata = _mapping(
        ingress_report.get("metadata"),
        "customer-deployment-qualification.ingress.invalid",
    )
    ingress_spec = _mapping(
        ingress_report.get("spec"),
        "customer-deployment-qualification.ingress.invalid",
    )
    oidc_metadata = _mapping(
        oidc_report.get("metadata"),
        "customer-deployment-qualification.oidc.invalid",
    )
    oidc_spec = _mapping(
        oidc_report.get("spec"),
        "customer-deployment-qualification.oidc.invalid",
    )
    oidc_subject = _mapping(
        oidc_spec.get("subject"),
        "customer-deployment-qualification.oidc.invalid",
    )
    oidc_bindings = _mapping(
        oidc_spec.get("bindings"),
        "customer-deployment-qualification.oidc.invalid",
    )
    policy_metadata = _mapping(
        policy_report.get("metadata"),
        "customer-deployment-qualification.policy.invalid",
    )
    policy_spec = _mapping(
        policy_report.get("spec"),
        "customer-deployment-qualification.policy.invalid",
    )
    policy_subject = _mapping(
        policy_spec.get("subject"),
        "customer-deployment-qualification.policy.invalid",
    )
    policy_bindings = _mapping(
        policy_spec.get("bindings"),
        "customer-deployment-qualification.policy.invalid",
    )
    credential_broker_metadata = _mapping(
        credential_broker_report.get("metadata"),
        "customer-deployment-qualification.credential-broker.invalid",
    )
    credential_broker_spec = _mapping(
        credential_broker_report.get("spec"),
        "customer-deployment-qualification.credential-broker.invalid",
    )
    credential_broker_subject = _mapping(
        credential_broker_spec.get("subject"),
        "customer-deployment-qualification.credential-broker.invalid",
    )
    credential_broker_bindings = _mapping(
        credential_broker_spec.get("bindings"),
        "customer-deployment-qualification.credential-broker.invalid",
    )
    otlp_receiver_metadata = _mapping(
        otlp_receiver_report.get("metadata"),
        "customer-deployment-qualification.otlp-receiver.invalid",
    )
    otlp_receiver_spec = _mapping(
        otlp_receiver_report.get("spec"),
        "customer-deployment-qualification.otlp-receiver.invalid",
    )
    otlp_receiver_subject = _mapping(
        otlp_receiver_spec.get("subject"),
        "customer-deployment-qualification.otlp-receiver.invalid",
    )
    otlp_receiver_bindings = _mapping(
        otlp_receiver_spec.get("bindings"),
        "customer-deployment-qualification.otlp-receiver.invalid",
    )
    continuity_metadata = _mapping(
        continuity_report.get("metadata"),
        "customer-deployment-qualification.continuity.invalid",
    )
    continuity_spec = _mapping(
        continuity_report.get("spec"),
        "customer-deployment-qualification.continuity.invalid",
    )
    continuity_subject = _mapping(
        continuity_spec.get("subject"),
        "customer-deployment-qualification.continuity.invalid",
    )
    continuity_bindings = _mapping(
        continuity_spec.get("bindings"),
        "customer-deployment-qualification.continuity.invalid",
    )
    processing_metadata = _mapping(
        processing_report.get("metadata"),
        "customer-deployment-qualification.processing.invalid",
    )
    processing_spec = _mapping(
        processing_report.get("spec"),
        "customer-deployment-qualification.processing.invalid",
    )
    processing_subject = _mapping(
        processing_spec.get("subject"),
        "customer-deployment-qualification.processing.invalid",
    )
    processing_bindings = _mapping(
        processing_spec.get("bindings"),
        "customer-deployment-qualification.processing.invalid",
    )
    postgresql_metadata = _mapping(
        postgresql_report.get("metadata"),
        "customer-deployment-qualification.database.invalid",
    )
    postgresql_spec = _mapping(
        postgresql_report.get("spec"),
        "customer-deployment-qualification.database.invalid",
    )
    postgresql_subject = _mapping(
        postgresql_spec.get("subject"),
        "customer-deployment-qualification.database.invalid",
    )
    postgresql_bindings = _mapping(
        postgresql_spec.get("bindings"),
        "customer-deployment-qualification.database.invalid",
    )

    revisions = {
        preflight_metadata.get("sourceRevision"),
        diagnostic_metadata.get("sourceRevision"),
        ingress_metadata.get("sourceRevision"),
        oidc_metadata.get("sourceRevision"),
        oidc_subject.get("sourceRevision"),
        policy_metadata.get("sourceRevision"),
        policy_subject.get("sourceRevision"),
        credential_broker_metadata.get("sourceRevision"),
        credential_broker_subject.get("sourceRevision"),
        otlp_receiver_metadata.get("sourceRevision"),
        otlp_receiver_subject.get("sourceRevision"),
        continuity_metadata.get("sourceRevision"),
        continuity_subject.get("sourceRevision"),
        processing_metadata.get("sourceRevision"),
        processing_subject.get("sourceRevision"),
        postgresql_metadata.get("sourceRevision"),
        postgresql_subject.get("sourceRevision"),
    }
    if len(revisions) != 1 or None in revisions:
        _fail("customer-deployment-qualification.source.crossed")
    revision = str(next(iter(revisions)))
    expected_identity = {
        "applicationVersion": continuity_subject.get("applicationVersion"),
        "chartVersion": continuity_subject.get("chartVersion"),
        "imageDigest": image_digest,
    }
    if (
        preflight_profile.get("applicationVersion")
        != continuity_subject.get("applicationVersion")
        or preflight_profile.get("chartVersion")
        != continuity_subject.get("chartVersion")
        or diagnostic_identity != expected_identity
        or continuity_subject.get("imageDigest") != image_digest
        or continuity_subject.get("contractsApiVersion") != API_VERSION
        or oidc_subject != continuity_subject
        or policy_subject
        != {
            "applicationVersion": continuity_subject.get("applicationVersion"),
            "contractsApiVersion": continuity_subject.get("contractsApiVersion"),
            "sourceRevision": continuity_subject.get("sourceRevision"),
            "imageDigest": continuity_subject.get("imageDigest"),
        }
        or credential_broker_subject
        != {
            "applicationVersion": continuity_subject.get("applicationVersion"),
            "contractsApiVersion": continuity_subject.get("contractsApiVersion"),
            "sourceRevision": continuity_subject.get("sourceRevision"),
            "imageDigest": continuity_subject.get("imageDigest"),
        }
        or otlp_receiver_subject != continuity_subject
        or processing_subject != continuity_subject
        or postgresql_subject != continuity_subject
        or ingress_spec.get("targetIdentity")
        != {
            **{key: continuity_subject.get(key) for key in (
                "applicationVersion",
                "contractsApiVersion",
                "requiredMigration",
                "sourceRevision",
                "chartVersion",
                "imageDigest",
            )},
            "buildMode": "release",
        }
    ):
        _fail("customer-deployment-qualification.release.crossed")
    if preflight_environment.get("mode") != "cluster":
        _fail("customer-deployment-qualification.preflight.not-live")
    expected_context = continuity._digest_value({"kubernetesContext": context})
    expected_namespace = continuity._digest_value({"namespace": namespace})
    expected_deployment = continuity._digest_value(
        {
            "context": context,
            "namespace": namespace,
            "deployment": deployment_name,
        }
    )
    if (
        continuity_bindings.get("kubernetesContextBindingDigest")
        != expected_context
        or continuity_bindings.get("namespaceBindingDigest")
        != expected_namespace
        or continuity_bindings.get("deploymentBindingDigest")
        != expected_deployment
        or preflight_environment.get("namespaceDigest")
        != preflight._digest(namespace)
    ):
        _fail("customer-deployment-qualification.target.crossed")
    processing_components = processing_bindings.get("components")
    if not isinstance(processing_components, list):
        _fail("customer-deployment-qualification.processing.invalid")
    expected_processing_components = (
        ("workflow-worker", worker_deployment_name),
        ("otlp-receiver", receiver_deployment_name),
    )
    if (
        oidc_bindings.get("apiTargetBindingDigest")
        != continuity_bindings.get("targetBindingDigest")
    ):
        _fail("customer-deployment-qualification.oidc.crossed")
    if (
        processing_bindings.get("apiTargetBindingDigest")
        != continuity_bindings.get("targetBindingDigest")
        or processing_bindings.get("kubernetesContextBindingDigest")
        != processing._digest_value(context)
        or processing_bindings.get("namespaceBindingDigest")
        != processing._digest_value(namespace)
        or [
            (
                item.get("id"),
                item.get("deploymentBindingDigest"),
            )
            for item in processing_components
            if isinstance(item, Mapping)
        ]
        != [
            (identifier, processing._digest_value(deployment))
            for identifier, deployment in expected_processing_components
        ]
    ):
        _fail("customer-deployment-qualification.processing.crossed")
    if (
        otlp_receiver_bindings.get("apiTargetBindingDigest")
        != continuity_bindings.get("targetBindingDigest")
        or otlp_receiver_bindings.get("receiverEndpointBindingDigest")
        != processing_bindings.get("otlpTargetBindingDigest")
    ):
        _fail("customer-deployment-qualification.otlp-receiver.crossed")
    if (
        postgresql_bindings.get("apiTargetBindingDigest")
        != processing_bindings.get("apiTargetBindingDigest")
        or postgresql_bindings.get("otlpTargetBindingDigest")
        != processing_bindings.get("otlpTargetBindingDigest")
        or postgresql_bindings.get("kubernetesContextBindingDigest")
        != processing_bindings.get("kubernetesContextBindingDigest")
        or postgresql_bindings.get("namespaceBindingDigest")
        != processing_bindings.get("namespaceBindingDigest")
    ):
        _fail("customer-deployment-qualification.database.crossed")
    diagnostic_binding, _ = diagnostics._target(
        context=context,
        namespace=namespace,
        release_name=release_name,
        image_digest=image_digest,
    )
    if diagnostic_spec.get("targetBindingDigest") != diagnostic_binding:
        _fail("customer-deployment-qualification.target.crossed")
    if current_cluster_environment is not None:
        for key in ("kubernetesVersion", "clusterBindingDigest", "namespaceDigest"):
            if preflight_environment.get(key) != current_cluster_environment.get(key):
                _fail("customer-deployment-qualification.cluster.changed")

    profile_name = preflight_profile.get("name")
    required_migration = continuity_subject.get("requiredMigration")
    if not isinstance(profile_name, str) or not isinstance(required_migration, str):
        _fail("customer-deployment-qualification.profile.invalid")
    subject = {
        "profile": profile_name,
        "applicationVersion": str(continuity_subject["applicationVersion"]),
        "chartVersion": str(continuity_subject["chartVersion"]),
        "contractsApiVersion": API_VERSION,
        "requiredMigration": required_migration,
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    bindings = {
        "clusterBindingDigest": str(preflight_environment["clusterBindingDigest"]),
        "namespaceBindingDigest": str(preflight_environment["namespaceDigest"]),
        "releaseBindingDigest": _digest_value(
            {"cluster": preflight_environment["clusterBindingDigest"], "release": release_name}
        ),
        "deploymentBindingDigest": str(
            continuity_bindings["deploymentBindingDigest"]
        ),
        "continuityTargetBindingDigest": str(
            continuity_bindings["targetBindingDigest"]
        ),
        "oidcProfileDigest": str(oidc_bindings["profileDigest"]),
        "oidcIssuerMetadataDigest": str(
            oidc_bindings["issuerMetadataDigest"]
        ),
        "policyEndpointBindingDigest": str(
            policy_bindings["endpointBindingDigest"]
        ),
        "policyProfileDigest": str(policy_bindings["profileDigest"]),
        "policySnapshotSetDigest": str(
            policy_bindings["snapshotSetDigest"]
        ),
        "credentialBrokerEndpointBindingDigest": str(
            credential_broker_bindings["endpointBindingDigest"]
        ),
        "credentialBrokerProfileDigest": str(
            credential_broker_bindings["profileDigest"]
        ),
        "credentialBrokerAuthoritySetDigest": str(
            credential_broker_bindings["authoritySetDigest"]
        ),
        "credentialBrokerCaBundleDigest": str(
            credential_broker_bindings["caBundleDigest"]
        ),
        "otlpReceiverApiTargetBindingDigest": str(
            otlp_receiver_bindings["apiTargetBindingDigest"]
        ),
        "otlpReceiverEndpointBindingDigest": str(
            otlp_receiver_bindings["receiverEndpointBindingDigest"]
        ),
        "otlpReceiverProfileDigest": str(
            otlp_receiver_bindings["profileDigest"]
        ),
        "otlpReceiverSignalSetDigest": str(
            otlp_receiver_bindings["signalSetDigest"]
        ),
        "otlpReceiverApiCaBundleDigest": str(
            otlp_receiver_bindings["apiCaBundleDigest"]
        ),
        "otlpReceiverCaBundleDigest": str(
            otlp_receiver_bindings["receiverCaBundleDigest"]
        ),
        "otlpReceiverClientCertificateDigest": str(
            otlp_receiver_bindings["clientCertificateDigest"]
        ),
        "processingOtlpTargetBindingDigest": str(
            processing_bindings["otlpTargetBindingDigest"]
        ),
        "processingProfileDigest": str(processing_bindings["profileDigest"]),
        "workerDeploymentBindingDigest": str(
            processing_components[0]["deploymentBindingDigest"]
        ),
        "receiverDeploymentBindingDigest": str(
            processing_components[1]["deploymentBindingDigest"]
        ),
        "databaseTargetBindingDigest": str(
            postgresql_bindings["databaseTargetBindingDigest"]
        ),
        "databaseProfileDigest": str(postgresql_bindings["profileDigest"]),
    }
    return subject, bindings


def _input_times(
    *,
    preflight_report: Mapping[str, Any],
    diagnostic_report: Mapping[str, Any],
    oidc_report: Mapping[str, Any],
    policy_report: Mapping[str, Any],
    credential_broker_report: Mapping[str, Any],
    otlp_receiver_report: Mapping[str, Any],
    continuity_report: Mapping[str, Any],
    processing_report: Mapping[str, Any],
    postgresql_report: Mapping[str, Any],
) -> tuple[
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
    datetime,
]:
    preflight_metadata = _mapping(
        preflight_report.get("metadata"),
        "customer-deployment-qualification.time.invalid",
    )
    diagnostic_spec = _mapping(
        diagnostic_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    diagnostic_environment = _mapping(
        diagnostic_spec.get("environment"),
        "customer-deployment-qualification.time.invalid",
    )
    oidc_spec = _mapping(
        oidc_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    oidc_measurements = _mapping(
        oidc_spec.get("measurements"),
        "customer-deployment-qualification.time.invalid",
    )
    policy_spec = _mapping(
        policy_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    policy_measurements = _mapping(
        policy_spec.get("measurements"),
        "customer-deployment-qualification.time.invalid",
    )
    credential_broker_spec = _mapping(
        credential_broker_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    credential_broker_measurements = _mapping(
        credential_broker_spec.get("measurements"),
        "customer-deployment-qualification.time.invalid",
    )
    otlp_receiver_spec = _mapping(
        otlp_receiver_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    otlp_receiver_measurements = _mapping(
        otlp_receiver_spec.get("measurements"),
        "customer-deployment-qualification.time.invalid",
    )
    continuity_spec = _mapping(
        continuity_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    continuity_measurements = _mapping(
        continuity_spec.get("measurements"),
        "customer-deployment-qualification.time.invalid",
    )
    processing_spec = _mapping(
        processing_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    processing_measurements = _mapping(
        processing_spec.get("measurements"),
        "customer-deployment-qualification.time.invalid",
    )
    postgresql_spec = _mapping(
        postgresql_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    postgresql_measurements = _mapping(
        postgresql_spec.get("measurements"),
        "customer-deployment-qualification.time.invalid",
    )
    return (
        _parse_timestamp(
            preflight_metadata.get("generatedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            oidc_measurements.get("startedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            oidc_measurements.get("completedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            policy_measurements.get("startedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            policy_measurements.get("completedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            credential_broker_measurements.get("startedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            credential_broker_measurements.get("completedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            otlp_receiver_measurements.get("startedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            otlp_receiver_measurements.get("completedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            continuity_measurements.get("startedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            continuity_measurements.get("completedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            processing_measurements.get("startedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            processing_measurements.get("completedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            postgresql_measurements.get("startedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            postgresql_measurements.get("completedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            diagnostic_environment.get("observedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
    )


def build_report(
    *,
    preflight_report: Mapping[str, Any],
    preflight_digest: str,
    diagnostic_report: Mapping[str, Any],
    diagnostic_digest: str,
    ingress_report: Mapping[str, Any],
    ingress_digest: str,
    oidc_report: Mapping[str, Any],
    oidc_digest: str,
    policy_report: Mapping[str, Any],
    policy_digest: str,
    credential_broker_report: Mapping[str, Any],
    credential_broker_digest: str,
    otlp_receiver_report: Mapping[str, Any],
    otlp_receiver_digest: str,
    continuity_report: Mapping[str, Any],
    continuity_digest: str,
    processing_report: Mapping[str, Any],
    processing_digest: str,
    postgresql_report: Mapping[str, Any],
    postgresql_digest: str,
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    worker_deployment_name: str,
    receiver_deployment_name: str,
    image_digest: str,
    current_cluster_environment: Mapping[str, str] | None,
    maximum_evidence_age_seconds: int = 86400,
    maximum_clock_skew_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        isinstance(maximum_evidence_age_seconds, bool)
        or not 300 <= maximum_evidence_age_seconds <= 604800
        or isinstance(maximum_clock_skew_seconds, bool)
        or not 0 <= maximum_clock_skew_seconds <= 900
    ):
        _fail("customer-deployment-qualification.objective.invalid")
    instant_input = now or datetime.now(timezone.utc)
    if instant_input.tzinfo is None:
        _fail("customer-deployment-qualification.time.invalid")
    instant = instant_input.astimezone(timezone.utc)
    subject, bindings = _subject_and_bindings(
        preflight_report=preflight_report,
        diagnostic_report=diagnostic_report,
        ingress_report=ingress_report,
        oidc_report=oidc_report,
        policy_report=policy_report,
        credential_broker_report=credential_broker_report,
        otlp_receiver_report=otlp_receiver_report,
        continuity_report=continuity_report,
        processing_report=processing_report,
        postgresql_report=postgresql_report,
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        worker_deployment_name=worker_deployment_name,
        receiver_deployment_name=receiver_deployment_name,
        image_digest=image_digest,
        current_cluster_environment=current_cluster_environment,
    )
    (
        preflight_at,
        oidc_started,
        oidc_completed,
        policy_started,
        policy_completed,
        credential_broker_started,
        credential_broker_completed,
        otlp_receiver_started,
        otlp_receiver_completed,
        continuity_started,
        continuity_completed,
        processing_started,
        processing_completed,
        postgresql_started,
        postgresql_completed,
        health_observed,
    ) = _input_times(
        preflight_report=preflight_report,
        diagnostic_report=diagnostic_report,
        oidc_report=oidc_report,
        policy_report=policy_report,
        credential_broker_report=credential_broker_report,
        otlp_receiver_report=otlp_receiver_report,
        continuity_report=continuity_report,
        processing_report=processing_report,
        postgresql_report=postgresql_report,
    )
    skew = maximum_clock_skew_seconds
    ordered = (
        preflight_at <= oidc_started
        and oidc_started <= oidc_completed
        and oidc_completed <= policy_started
        and policy_started <= policy_completed
        and policy_completed <= credential_broker_started
        and credential_broker_started <= credential_broker_completed
        and credential_broker_completed <= otlp_receiver_started
        and otlp_receiver_started <= otlp_receiver_completed
        and otlp_receiver_completed <= continuity_started
        and continuity_started <= continuity_completed
        and continuity_completed <= processing_started
        and processing_started <= processing_completed
        and processing_completed <= postgresql_started
        and postgresql_started <= postgresql_completed
        and postgresql_completed <= health_observed
    )
    times = (
        preflight_at,
        oidc_started,
        oidc_completed,
        policy_started,
        policy_completed,
        credential_broker_started,
        credential_broker_completed,
        otlp_receiver_started,
        otlp_receiver_completed,
        continuity_started,
        continuity_completed,
        processing_started,
        processing_completed,
        postgresql_started,
        postgresql_completed,
        health_observed,
    )
    fresh = all(
        instant.timestamp() - maximum_evidence_age_seconds
        <= value.timestamp()
        <= instant.timestamp() + skew
        for value in times
    )
    oldest_age = max(0, math.ceil((instant - min(times)).total_seconds()))

    evidence = [
        _evidence(
            identifier=definition[0],
            contract_kind=definition[1],
            success_status=definition[2],
            error_code=definition[3],
            document=document,
            digest=digest,
        )
        for definition, document, digest in zip(
            EVIDENCE_DEFINITIONS,
            (
                preflight_report,
                diagnostic_report,
                ingress_report,
                oidc_report,
                policy_report,
                credential_broker_report,
                otlp_receiver_report,
                continuity_report,
                processing_report,
                postgresql_report,
            ),
            (
                preflight_digest,
                diagnostic_digest,
                ingress_digest,
                oidc_digest,
                policy_digest,
                credential_broker_digest,
                otlp_receiver_digest,
                continuity_digest,
                processing_digest,
                postgresql_digest,
            ),
        )
    ]
    evidence_by_id = {item["id"]: item for item in evidence}
    checks = [
        _check("source-binding", True, "customer-deployment-qualification.source.crossed"),
        _check("profile-binding", True, "customer-deployment-qualification.profile.crossed"),
        _check("exact-release-identity", True, "customer-deployment-qualification.release.crossed"),
        _check("explicit-current-cluster", True, "customer-deployment-qualification.cluster.changed"),
        _check(
            "live-install-preflight",
            evidence_by_id["live-install-preflight"]["status"] == "passed",
            "customer-deployment-qualification.preflight.not-install-ready",
        ),
        _check(
            "post-continuity-health",
            evidence_by_id["post-install-health"]["status"] == "passed" and ordered,
            "customer-deployment-qualification.diagnostic.not-post-continuity-healthy",
        ),
        _check(
            "customer-ingress",
            evidence_by_id["customer-ingress"]["status"] == "passed",
            "customer-deployment-qualification.ingress.not-qualified",
        ),
        _check(
            "customer-oidc",
            evidence_by_id["customer-oidc"]["status"] == "passed",
            "customer-deployment-qualification.oidc.not-qualified",
        ),
        _check(
            "customer-policy",
            evidence_by_id["customer-policy"]["status"] == "passed",
            "customer-deployment-qualification.policy.not-qualified",
        ),
        _check(
            "customer-credential-broker",
            evidence_by_id["customer-credential-broker"]["status"] == "passed",
            "customer-deployment-qualification.credential-broker.not-qualified",
        ),
        _check(
            "customer-otlp-receiver",
            evidence_by_id["customer-otlp-receiver"]["status"] == "passed",
            "customer-deployment-qualification.otlp-receiver.not-qualified",
        ),
        _check(
            "control-plane-continuity",
            evidence_by_id["control-plane-continuity"]["status"] == "passed",
            "customer-deployment-qualification.continuity.not-qualified",
        ),
        _check(
            "worker-receiver-processing",
            evidence_by_id["worker-receiver-processing"]["status"] == "passed",
            "customer-deployment-qualification.processing.not-qualified",
        ),
        _check(
            "postgresql-primary-promotion",
            evidence_by_id["postgresql-primary-promotion"]["status"] == "passed",
            "customer-deployment-qualification.database.not-qualified",
        ),
        _check("continuity-ingress-chain", True, "customer-deployment-qualification.ingress.crossed"),
        _check("oidc-target-chain", True, "customer-deployment-qualification.oidc.crossed"),
        _check("policy-binding-chain", True, "customer-deployment-qualification.policy.crossed"),
        _check("credential-broker-binding-chain", True, "customer-deployment-qualification.credential-broker.crossed"),
        _check("otlp-receiver-binding-chain", True, "customer-deployment-qualification.otlp-receiver.crossed"),
        _check("processing-target-chain", True, "customer-deployment-qualification.processing.crossed"),
        _check("database-target-chain", True, "customer-deployment-qualification.database.crossed"),
        _check("evidence-order", ordered, "customer-deployment-qualification.evidence.order-invalid"),
        _check("evidence-freshness", fresh, "customer-deployment-qualification.evidence.stale"),
        _check("minimized-output", True, "customer-deployment-qualification.output.not-minimized"),
    ]
    failed_checks = sum(item["status"] == "failed" for item in checks)
    rejected_evidence = sum(item["status"] == "rejected" for item in evidence)
    status = (
        "qualified"
        if failed_checks == 0 and rejected_evidence == 0
        else "not-qualified"
    )
    generated_at = _timestamp(instant)
    spec: dict[str, Any] = {
        "status": status,
        "qualificationLevel": QUALIFICATION_LEVEL,
        "subject": subject,
        "bindings": bindings,
        "objective": {
            "maximumEvidenceAgeSeconds": maximum_evidence_age_seconds,
            "maximumClockSkewSeconds": maximum_clock_skew_seconds,
            "requirePostContinuityHealth": True,
        },
        "measurements": {
            "preflightGeneratedAt": _timestamp(preflight_at),
            "oidcStartedAt": _timestamp(oidc_started),
            "oidcCompletedAt": _timestamp(oidc_completed),
            "policyStartedAt": _timestamp(policy_started),
            "policyCompletedAt": _timestamp(policy_completed),
            "credentialBrokerStartedAt": _timestamp(credential_broker_started),
            "credentialBrokerCompletedAt": _timestamp(credential_broker_completed),
            "otlpReceiverStartedAt": _timestamp(otlp_receiver_started),
            "otlpReceiverCompletedAt": _timestamp(otlp_receiver_completed),
            "continuityStartedAt": _timestamp(continuity_started),
            "continuityCompletedAt": _timestamp(continuity_completed),
            "processingStartedAt": _timestamp(processing_started),
            "processingCompletedAt": _timestamp(processing_completed),
            "databaseContinuityStartedAt": _timestamp(postgresql_started),
            "databaseContinuityCompletedAt": _timestamp(postgresql_completed),
            "postContinuityHealthObservedAt": _timestamp(health_observed),
            "qualifiedAt": generated_at,
            "oldestEvidenceAgeSeconds": oldest_age,
        },
        "evidence": evidence,
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": {
            "requiredEvidence": len(evidence),
            "passedEvidence": len(evidence) - rejected_evidence,
            "rejectedEvidence": rejected_evidence,
            "totalChecks": len(checks),
            "passedChecks": len(checks) - failed_checks,
            "failedChecks": failed_checks,
            "overallStatus": status,
        },
    }
    metadata_without_id: dict[str, object] = {
        "generatedAt": generated_at,
        "sourceRevision": subject["sourceRevision"],
        "sourceDirty": False,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "id": _report_identifier(metadata_without_id, spec),
            **metadata_without_id,
        },
        "spec": spec,
    }
    validate_report_document(report)
    return report


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _expected_checks(report: Mapping[str, Any]) -> list[dict[str, str]]:
    spec = _mapping(
        report.get("spec"), "customer-deployment-qualification.report.invalid"
    )
    evidence = spec.get("evidence")
    measurements = _mapping(
        spec.get("measurements"),
        "customer-deployment-qualification.report.invalid",
    )
    objective = _mapping(
        spec.get("objective"), "customer-deployment-qualification.report.invalid"
    )
    if not isinstance(evidence, list) or len(evidence) != len(EVIDENCE_DEFINITIONS):
        _fail("customer-deployment-qualification.report.invalid")
    evidence_by_id = {
        str(_mapping(item, "customer-deployment-qualification.report.invalid").get("id")): _mapping(
            item, "customer-deployment-qualification.report.invalid"
        )
        for item in evidence
    }
    preflight_at = _parse_timestamp(
        measurements.get("preflightGeneratedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    oidc_started = _parse_timestamp(
        measurements.get("oidcStartedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    oidc_completed = _parse_timestamp(
        measurements.get("oidcCompletedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    policy_started = _parse_timestamp(
        measurements.get("policyStartedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    policy_completed = _parse_timestamp(
        measurements.get("policyCompletedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    credential_broker_started = _parse_timestamp(
        measurements.get("credentialBrokerStartedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    credential_broker_completed = _parse_timestamp(
        measurements.get("credentialBrokerCompletedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    otlp_receiver_started = _parse_timestamp(
        measurements.get("otlpReceiverStartedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    otlp_receiver_completed = _parse_timestamp(
        measurements.get("otlpReceiverCompletedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    continuity_started = _parse_timestamp(
        measurements.get("continuityStartedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    continuity_completed = _parse_timestamp(
        measurements.get("continuityCompletedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    processing_started = _parse_timestamp(
        measurements.get("processingStartedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    processing_completed = _parse_timestamp(
        measurements.get("processingCompletedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    postgresql_started = _parse_timestamp(
        measurements.get("databaseContinuityStartedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    postgresql_completed = _parse_timestamp(
        measurements.get("databaseContinuityCompletedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    health_observed = _parse_timestamp(
        measurements.get("postContinuityHealthObservedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    qualified_at = _parse_timestamp(
        measurements.get("qualifiedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    skew = objective.get("maximumClockSkewSeconds")
    maximum_age = objective.get("maximumEvidenceAgeSeconds")
    if (
        isinstance(skew, bool)
        or not isinstance(skew, int)
        or isinstance(maximum_age, bool)
        or not isinstance(maximum_age, int)
    ):
        _fail("customer-deployment-qualification.report.objective-invalid")
    ordered = (
        preflight_at <= oidc_started
        and oidc_started <= oidc_completed
        and oidc_completed <= policy_started
        and policy_started <= policy_completed
        and policy_completed <= credential_broker_started
        and credential_broker_started <= credential_broker_completed
        and credential_broker_completed <= otlp_receiver_started
        and otlp_receiver_started <= otlp_receiver_completed
        and otlp_receiver_completed <= continuity_started
        and continuity_started <= continuity_completed
        and continuity_completed <= processing_started
        and processing_started <= processing_completed
        and processing_completed <= postgresql_started
        and postgresql_started <= postgresql_completed
        and postgresql_completed <= health_observed
    )
    values = (
        preflight_at,
        oidc_started,
        oidc_completed,
        policy_started,
        policy_completed,
        credential_broker_started,
        credential_broker_completed,
        otlp_receiver_started,
        otlp_receiver_completed,
        continuity_started,
        continuity_completed,
        processing_started,
        processing_completed,
        postgresql_started,
        postgresql_completed,
        health_observed,
    )
    fresh = all(
        qualified_at.timestamp() - maximum_age
        <= value.timestamp()
        <= qualified_at.timestamp() + skew
        for value in values
    )
    expected_age = max(0, math.ceil((qualified_at - min(values)).total_seconds()))
    if measurements.get("oldestEvidenceAgeSeconds") != expected_age:
        _fail("customer-deployment-qualification.report.age-invalid")
    return [
        _check("source-binding", True, "customer-deployment-qualification.source.crossed"),
        _check("profile-binding", True, "customer-deployment-qualification.profile.crossed"),
        _check("exact-release-identity", True, "customer-deployment-qualification.release.crossed"),
        _check("explicit-current-cluster", True, "customer-deployment-qualification.cluster.changed"),
        _check(
            "live-install-preflight",
            evidence_by_id.get("live-install-preflight", {}).get("status") == "passed",
            "customer-deployment-qualification.preflight.not-install-ready",
        ),
        _check(
            "post-continuity-health",
            evidence_by_id.get("post-install-health", {}).get("status") == "passed" and ordered,
            "customer-deployment-qualification.diagnostic.not-post-continuity-healthy",
        ),
        _check(
            "customer-ingress",
            evidence_by_id.get("customer-ingress", {}).get("status") == "passed",
            "customer-deployment-qualification.ingress.not-qualified",
        ),
        _check(
            "customer-oidc",
            evidence_by_id.get("customer-oidc", {}).get("status") == "passed",
            "customer-deployment-qualification.oidc.not-qualified",
        ),
        _check(
            "customer-policy",
            evidence_by_id.get("customer-policy", {}).get("status") == "passed",
            "customer-deployment-qualification.policy.not-qualified",
        ),
        _check(
            "customer-credential-broker",
            evidence_by_id.get("customer-credential-broker", {}).get("status")
            == "passed",
            "customer-deployment-qualification.credential-broker.not-qualified",
        ),
        _check(
            "customer-otlp-receiver",
            evidence_by_id.get("customer-otlp-receiver", {}).get("status")
            == "passed",
            "customer-deployment-qualification.otlp-receiver.not-qualified",
        ),
        _check(
            "control-plane-continuity",
            evidence_by_id.get("control-plane-continuity", {}).get("status") == "passed",
            "customer-deployment-qualification.continuity.not-qualified",
        ),
        _check(
            "worker-receiver-processing",
            evidence_by_id.get("worker-receiver-processing", {}).get("status")
            == "passed",
            "customer-deployment-qualification.processing.not-qualified",
        ),
        _check(
            "postgresql-primary-promotion",
            evidence_by_id.get("postgresql-primary-promotion", {}).get("status")
            == "passed",
            "customer-deployment-qualification.database.not-qualified",
        ),
        _check("continuity-ingress-chain", True, "customer-deployment-qualification.ingress.crossed"),
        _check("oidc-target-chain", True, "customer-deployment-qualification.oidc.crossed"),
        _check("policy-binding-chain", True, "customer-deployment-qualification.policy.crossed"),
        _check("credential-broker-binding-chain", True, "customer-deployment-qualification.credential-broker.crossed"),
        _check("otlp-receiver-binding-chain", True, "customer-deployment-qualification.otlp-receiver.crossed"),
        _check("processing-target-chain", True, "customer-deployment-qualification.processing.crossed"),
        _check("database-target-chain", True, "customer-deployment-qualification.database.crossed"),
        _check("evidence-order", ordered, "customer-deployment-qualification.evidence.order-invalid"),
        _check("evidence-freshness", fresh, "customer-deployment-qualification.evidence.stale"),
        _check(
            "minimized-output",
            not _has_forbidden_key(report),
            "customer-deployment-qualification.output.not-minimized",
        ),
    ]


def validate_report_document(report: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-deployment-qualification.schema.unavailable")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail("customer-deployment-qualification.report.schema-invalid")
    metadata = _mapping(
        report.get("metadata"), "customer-deployment-qualification.report.invalid"
    )
    spec = _mapping(
        report.get("spec"), "customer-deployment-qualification.report.invalid"
    )
    if metadata.get("generatedAt") != _mapping(
        spec.get("measurements"), "customer-deployment-qualification.report.invalid"
    ).get("qualifiedAt"):
        _fail("customer-deployment-qualification.report.time-invalid")
    subject = _mapping(
        spec.get("subject"), "customer-deployment-qualification.report.invalid"
    )
    if metadata.get("sourceRevision") != subject.get("sourceRevision"):
        _fail("customer-deployment-qualification.report.source-invalid")
    evidence = spec.get("evidence")
    if not isinstance(evidence, list):
        _fail("customer-deployment-qualification.report.evidence-invalid")
    for definition, item in zip(EVIDENCE_DEFINITIONS, evidence):
        current = _mapping(
            item, "customer-deployment-qualification.report.evidence-invalid"
        )
        expected_passed = current.get("observedStatus") == definition[2]
        if (
            current.get("id") != definition[0]
            or current.get("contractKind") != definition[1]
            or (current.get("status") == "passed") != expected_passed
            or (
                not expected_passed
                and current.get("errorCode") != definition[3]
            )
        ):
            _fail("customer-deployment-qualification.report.evidence-invalid")
    expected_checks = _expected_checks(report)
    if spec.get("checks") != expected_checks or [item["id"] for item in expected_checks] != list(CHECK_IDS):
        _fail("customer-deployment-qualification.report.checks-invalid")
    if spec.get("limitations") != list(LIMITATIONS):
        _fail("customer-deployment-qualification.report.limitations-invalid")
    rejected = sum(item.get("status") == "rejected" for item in evidence if isinstance(item, Mapping))
    failed = sum(item["status"] == "failed" for item in expected_checks)
    status = "qualified" if rejected == 0 and failed == 0 else "not-qualified"
    expected_summary = {
        "requiredEvidence": len(EVIDENCE_DEFINITIONS),
        "passedEvidence": len(EVIDENCE_DEFINITIONS) - rejected,
        "rejectedEvidence": rejected,
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed,
        "failedChecks": failed,
        "overallStatus": status,
    }
    if spec.get("status") != status or spec.get("summary") != expected_summary:
        _fail("customer-deployment-qualification.report.summary-invalid")
    metadata_without_id = dict(metadata)
    report_id = metadata_without_id.pop("id", None)
    if (
        not isinstance(report_id, str)
        or REPORT_ID.fullmatch(report_id) is None
        or report_id != _report_identifier(metadata_without_id, spec)
    ):
        _fail("customer-deployment-qualification.report.id-invalid")


def _validate_inputs(
    *,
    preflight_path: Path,
    diagnostic_path: Path,
    ingress_path: Path,
    oidc_path: Path,
    oidc_profile_path: Path,
    oidc_api_base_url: str,
    policy_path: Path,
    policy_profile_path: Path,
    policy_endpoint: str,
    credential_broker_path: Path,
    credential_broker_profile_path: Path,
    credential_broker_endpoint: str,
    credential_broker_ca_bundle_path: Path,
    otlp_receiver_path: Path,
    otlp_receiver_profile_path: Path,
    otlp_receiver_api_base_url: str,
    otlp_receiver_endpoint: str,
    otlp_receiver_api_ca_path: Path | None,
    otlp_receiver_ca_path: Path,
    otlp_receiver_client_certificate_path: Path,
    continuity_path: Path,
    processing_path: Path,
    processing_profile_path: Path,
    postgresql_path: Path,
    postgresql_profile_path: Path,
    postgresql_database_host: str,
    postgresql_database_port: int,
    processing_api_base_url: str,
    processing_otlp_base_url: str,
    values: Sequence[Path],
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    worker_deployment_name: str,
    receiver_deployment_name: str,
    image_digest: str,
    helm: str,
) -> tuple[
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
]:
    if not values or len(values) > MAX_VALUES_FILES:
        _fail("customer-deployment-qualification.values.invalid")
    preflight_report, preflight_digest = _load_document(
        preflight_path, "customer-deployment-qualification.preflight.unreadable"
    )
    diagnostic_report, diagnostic_digest = _load_document(
        diagnostic_path, "customer-deployment-qualification.diagnostic.unreadable"
    )
    ingress_report, ingress_digest = _load_document(
        ingress_path, "customer-deployment-qualification.ingress.unreadable"
    )
    oidc_report, oidc_digest = _load_document(
        oidc_path, "customer-deployment-qualification.oidc.unreadable"
    )
    policy_report, policy_digest = _load_document(
        policy_path, "customer-deployment-qualification.policy.unreadable"
    )
    credential_broker_report, credential_broker_digest = _load_document(
        credential_broker_path,
        "customer-deployment-qualification.credential-broker.unreadable",
    )
    otlp_receiver_report, otlp_receiver_digest = _load_document(
        otlp_receiver_path,
        "customer-deployment-qualification.otlp-receiver.unreadable",
    )
    continuity_report, continuity_digest = _load_document(
        continuity_path, "customer-deployment-qualification.continuity.unreadable"
    )
    processing_report, processing_digest = _load_document(
        processing_path, "customer-deployment-qualification.processing.unreadable"
    )
    postgresql_report, postgresql_digest = _load_document(
        postgresql_path, "customer-deployment-qualification.database.unreadable"
    )
    try:
        preflight.verify_report(
            preflight_path,
            values=values,
            namespace=namespace,
            release_name=release_name,
            helm=helm,
            require_clean=True,
            require_install_ready=False,
        )
        diagnostics.verify_report(
            diagnostic_report,
            context=context,
            namespace=namespace,
            release_name=release_name,
            image_digest=image_digest,
            require_clean=True,
            require_healthy=False,
        )
        ingress.validate_report_document(ingress_report)
        oidc.verify_report(
            report_path=oidc_path,
            profile_path=oidc_profile_path,
            api_base_url=oidc_api_base_url,
            image_digest=image_digest,
            require_qualified=False,
        )
        customer_policy.verify_report(
            report_path=policy_path,
            profile_path=policy_profile_path,
            endpoint=policy_endpoint,
            image_digest=image_digest,
            require_qualified=False,
        )
        customer_credential_broker.verify_report(
            report_path=credential_broker_path,
            profile_path=credential_broker_profile_path,
            endpoint=credential_broker_endpoint,
            ca_bundle_path=credential_broker_ca_bundle_path,
            image_digest=image_digest,
            require_qualified=False,
        )
        customer_otlp_receiver.verify_report(
            report_path=otlp_receiver_path,
            profile_path=otlp_receiver_profile_path,
            api_base_url=otlp_receiver_api_base_url,
            receiver_endpoint=otlp_receiver_endpoint,
            api_ca_path=otlp_receiver_api_ca_path,
            receiver_ca_path=otlp_receiver_ca_path,
            client_certificate_path=otlp_receiver_client_certificate_path,
            image_digest=image_digest,
            require_qualified=False,
        )
        continuity.verify_report(
            report_path=continuity_path,
            ingress_report_path=ingress_path,
            require_clean=True,
            require_qualified=False,
        )
        processing.verify_report(
            report_path=processing_path,
            profile_path=processing_profile_path,
            api_base_url=processing_api_base_url,
            otlp_base_url=processing_otlp_base_url,
            image_digest=image_digest,
            context=context,
            namespace=namespace,
            worker_deployment=worker_deployment_name,
            receiver_deployment=receiver_deployment_name,
            require_clean=True,
            require_qualified=False,
        )
        postgresql.verify_report(
            report_path=postgresql_path,
            profile_path=postgresql_profile_path,
            api_base_url=processing_api_base_url,
            otlp_base_url=processing_otlp_base_url,
            database_host=postgresql_database_host,
            database_port=postgresql_database_port,
            image_digest=image_digest,
            context=context,
            namespace=namespace,
            require_clean=True,
            require_qualified=False,
        )
    except (
        preflight.DeploymentPreflightError,
        diagnostics.DeploymentDiagnosticError,
        ingress.IngressQualificationError,
        oidc.CustomerOidcQualificationError,
        customer_policy.CustomerPolicyQualificationError,
        customer_credential_broker.CustomerCredentialBrokerQualificationError,
        customer_otlp_receiver.CustomerOtlpReceiverQualificationError,
        continuity.CustomerContinuityQualificationError,
        processing.CustomerProcessingContinuityError,
        postgresql.CustomerPostgreSQLContinuityError,
    ):
        _fail("customer-deployment-qualification.input.verification-failed")
    for path, expected, code in (
        (preflight_path, preflight_digest, "customer-deployment-qualification.preflight.changed"),
        (diagnostic_path, diagnostic_digest, "customer-deployment-qualification.diagnostic.changed"),
        (ingress_path, ingress_digest, "customer-deployment-qualification.ingress.changed"),
        (oidc_path, oidc_digest, "customer-deployment-qualification.oidc.changed"),
        (policy_path, policy_digest, "customer-deployment-qualification.policy.changed"),
        (credential_broker_path, credential_broker_digest, "customer-deployment-qualification.credential-broker.changed"),
        (otlp_receiver_path, otlp_receiver_digest, "customer-deployment-qualification.otlp-receiver.changed"),
        (continuity_path, continuity_digest, "customer-deployment-qualification.continuity.changed"),
        (processing_path, processing_digest, "customer-deployment-qualification.processing.changed"),
        (postgresql_path, postgresql_digest, "customer-deployment-qualification.database.changed"),
    ):
        if _file_digest(path, code) != expected:
            _fail(code)
    return (
        preflight_report,
        preflight_digest,
        diagnostic_report,
        diagnostic_digest,
        ingress_report,
        ingress_digest,
        oidc_report,
        oidc_digest,
        policy_report,
        policy_digest,
        credential_broker_report,
        credential_broker_digest,
        otlp_receiver_report,
        otlp_receiver_digest,
        continuity_report,
        continuity_digest,
        processing_report,
        processing_digest,
        postgresql_report,
        postgresql_digest,
    )


def qualify(
    *,
    preflight_path: Path,
    diagnostic_path: Path,
    ingress_path: Path,
    oidc_path: Path,
    oidc_profile_path: Path,
    oidc_api_base_url: str,
    policy_path: Path,
    policy_profile_path: Path,
    policy_endpoint: str,
    credential_broker_path: Path,
    credential_broker_profile_path: Path,
    credential_broker_endpoint: str,
    credential_broker_ca_bundle_path: Path,
    otlp_receiver_path: Path,
    otlp_receiver_profile_path: Path,
    otlp_receiver_api_base_url: str,
    otlp_receiver_endpoint: str,
    otlp_receiver_api_ca_path: Path | None,
    otlp_receiver_ca_path: Path,
    otlp_receiver_client_certificate_path: Path,
    continuity_path: Path,
    processing_path: Path,
    processing_profile_path: Path,
    processing_api_base_url: str,
    processing_otlp_base_url: str,
    postgresql_path: Path,
    postgresql_profile_path: Path,
    postgresql_database_host: str,
    postgresql_database_port: int,
    values: Sequence[Path],
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    worker_deployment_name: str,
    receiver_deployment_name: str,
    image_digest: str,
    output: Path,
    helm: str = "helm",
    kubectl: str = "kubectl",
    maximum_evidence_age_seconds: int = 86400,
    maximum_clock_skew_seconds: int = 300,
    now: Callable[[], datetime] | None = None,
) -> Mapping[str, Any]:
    inputs = _validate_inputs(
        preflight_path=preflight_path,
        diagnostic_path=diagnostic_path,
        ingress_path=ingress_path,
        oidc_path=oidc_path,
        oidc_profile_path=oidc_profile_path,
        oidc_api_base_url=oidc_api_base_url,
        policy_path=policy_path,
        policy_profile_path=policy_profile_path,
        policy_endpoint=policy_endpoint,
        credential_broker_path=credential_broker_path,
        credential_broker_profile_path=credential_broker_profile_path,
        credential_broker_endpoint=credential_broker_endpoint,
        credential_broker_ca_bundle_path=credential_broker_ca_bundle_path,
        otlp_receiver_path=otlp_receiver_path,
        otlp_receiver_profile_path=otlp_receiver_profile_path,
        otlp_receiver_api_base_url=otlp_receiver_api_base_url,
        otlp_receiver_endpoint=otlp_receiver_endpoint,
        otlp_receiver_api_ca_path=otlp_receiver_api_ca_path,
        otlp_receiver_ca_path=otlp_receiver_ca_path,
        otlp_receiver_client_certificate_path=otlp_receiver_client_certificate_path,
        continuity_path=continuity_path,
        processing_path=processing_path,
        processing_profile_path=processing_profile_path,
        processing_api_base_url=processing_api_base_url,
        processing_otlp_base_url=processing_otlp_base_url,
        postgresql_path=postgresql_path,
        postgresql_profile_path=postgresql_profile_path,
        postgresql_database_host=postgresql_database_host,
        postgresql_database_port=postgresql_database_port,
        values=values,
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        worker_deployment_name=worker_deployment_name,
        receiver_deployment_name=receiver_deployment_name,
        image_digest=image_digest,
        helm=helm,
    )
    cluster_environment, cluster_check = preflight._cluster_environment(
        kubectl, context=context, namespace=namespace
    )
    if cluster_check.get("status") != "passed":
        _fail("customer-deployment-qualification.cluster.unavailable")
    report = build_report(
        preflight_report=inputs[0],
        preflight_digest=inputs[1],
        diagnostic_report=inputs[2],
        diagnostic_digest=inputs[3],
        ingress_report=inputs[4],
        ingress_digest=inputs[5],
        oidc_report=inputs[6],
        oidc_digest=inputs[7],
        policy_report=inputs[8],
        policy_digest=inputs[9],
        credential_broker_report=inputs[10],
        credential_broker_digest=inputs[11],
        otlp_receiver_report=inputs[12],
        otlp_receiver_digest=inputs[13],
        continuity_report=inputs[14],
        continuity_digest=inputs[15],
        processing_report=inputs[16],
        processing_digest=inputs[17],
        postgresql_report=inputs[18],
        postgresql_digest=inputs[19],
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        worker_deployment_name=worker_deployment_name,
        receiver_deployment_name=receiver_deployment_name,
        image_digest=image_digest,
        current_cluster_environment=cluster_environment,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
        maximum_clock_skew_seconds=maximum_clock_skew_seconds,
        now=(now or (lambda: datetime.now(timezone.utc)))(),
    )
    _write_report(output, report)
    return report


def verify_report(
    *,
    report_path: Path,
    preflight_path: Path,
    diagnostic_path: Path,
    ingress_path: Path,
    oidc_path: Path,
    oidc_profile_path: Path,
    oidc_api_base_url: str,
    policy_path: Path,
    policy_profile_path: Path,
    policy_endpoint: str,
    credential_broker_path: Path,
    credential_broker_profile_path: Path,
    credential_broker_endpoint: str,
    credential_broker_ca_bundle_path: Path,
    otlp_receiver_path: Path,
    otlp_receiver_profile_path: Path,
    otlp_receiver_api_base_url: str,
    otlp_receiver_endpoint: str,
    otlp_receiver_api_ca_path: Path | None,
    otlp_receiver_ca_path: Path,
    otlp_receiver_client_certificate_path: Path,
    continuity_path: Path,
    processing_path: Path,
    processing_profile_path: Path,
    processing_api_base_url: str,
    processing_otlp_base_url: str,
    postgresql_path: Path,
    postgresql_profile_path: Path,
    postgresql_database_host: str,
    postgresql_database_port: int,
    values: Sequence[Path],
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    worker_deployment_name: str,
    receiver_deployment_name: str,
    image_digest: str,
    helm: str = "helm",
    kubectl: str = "kubectl",
    require_current_cluster: bool = False,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    report, _ = _load_document(
        report_path, "customer-deployment-qualification.report.unreadable"
    )
    validate_report_document(report)
    inputs = _validate_inputs(
        preflight_path=preflight_path,
        diagnostic_path=diagnostic_path,
        ingress_path=ingress_path,
        oidc_path=oidc_path,
        oidc_profile_path=oidc_profile_path,
        oidc_api_base_url=oidc_api_base_url,
        policy_path=policy_path,
        policy_profile_path=policy_profile_path,
        policy_endpoint=policy_endpoint,
        credential_broker_path=credential_broker_path,
        credential_broker_profile_path=credential_broker_profile_path,
        credential_broker_endpoint=credential_broker_endpoint,
        credential_broker_ca_bundle_path=credential_broker_ca_bundle_path,
        otlp_receiver_path=otlp_receiver_path,
        otlp_receiver_profile_path=otlp_receiver_profile_path,
        otlp_receiver_api_base_url=otlp_receiver_api_base_url,
        otlp_receiver_endpoint=otlp_receiver_endpoint,
        otlp_receiver_api_ca_path=otlp_receiver_api_ca_path,
        otlp_receiver_ca_path=otlp_receiver_ca_path,
        otlp_receiver_client_certificate_path=otlp_receiver_client_certificate_path,
        continuity_path=continuity_path,
        processing_path=processing_path,
        processing_profile_path=processing_profile_path,
        processing_api_base_url=processing_api_base_url,
        processing_otlp_base_url=processing_otlp_base_url,
        postgresql_path=postgresql_path,
        postgresql_profile_path=postgresql_profile_path,
        postgresql_database_host=postgresql_database_host,
        postgresql_database_port=postgresql_database_port,
        values=values,
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        worker_deployment_name=worker_deployment_name,
        receiver_deployment_name=receiver_deployment_name,
        image_digest=image_digest,
        helm=helm,
    )
    current_cluster_environment: Mapping[str, str] | None = None
    if require_current_cluster:
        current_cluster_environment, cluster_check = preflight._cluster_environment(
            kubectl, context=context, namespace=namespace
        )
        if cluster_check.get("status") != "passed":
            _fail("customer-deployment-qualification.cluster.unavailable")
    metadata = _mapping(
        report.get("metadata"), "customer-deployment-qualification.report.invalid"
    )
    spec = _mapping(
        report.get("spec"), "customer-deployment-qualification.report.invalid"
    )
    objective = _mapping(
        spec.get("objective"), "customer-deployment-qualification.report.invalid"
    )
    reconstructed = build_report(
        preflight_report=inputs[0],
        preflight_digest=inputs[1],
        diagnostic_report=inputs[2],
        diagnostic_digest=inputs[3],
        ingress_report=inputs[4],
        ingress_digest=inputs[5],
        oidc_report=inputs[6],
        oidc_digest=inputs[7],
        policy_report=inputs[8],
        policy_digest=inputs[9],
        credential_broker_report=inputs[10],
        credential_broker_digest=inputs[11],
        otlp_receiver_report=inputs[12],
        otlp_receiver_digest=inputs[13],
        continuity_report=inputs[14],
        continuity_digest=inputs[15],
        processing_report=inputs[16],
        processing_digest=inputs[17],
        postgresql_report=inputs[18],
        postgresql_digest=inputs[19],
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        worker_deployment_name=worker_deployment_name,
        receiver_deployment_name=receiver_deployment_name,
        image_digest=image_digest,
        current_cluster_environment=current_cluster_environment,
        maximum_evidence_age_seconds=int(objective["maximumEvidenceAgeSeconds"]),
        maximum_clock_skew_seconds=int(objective["maximumClockSkewSeconds"]),
        now=_parse_timestamp(
            metadata.get("generatedAt"),
            "customer-deployment-qualification.report.time-invalid",
        ),
    )
    if report != reconstructed:
        _fail("customer-deployment-qualification.report.input-mismatch")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-deployment-qualification.report.not-qualified")
    return report


def _common_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preflight-report", type=Path, required=True)
    parser.add_argument("--diagnostic-report", type=Path, required=True)
    parser.add_argument("--ingress-report", type=Path, required=True)
    parser.add_argument("--oidc-report", type=Path, required=True)
    parser.add_argument("--oidc-profile", type=Path, required=True)
    parser.add_argument("--oidc-api-base-url", required=True)
    parser.add_argument("--policy-report", type=Path, required=True)
    parser.add_argument("--policy-profile", type=Path, required=True)
    parser.add_argument("--policy-endpoint", required=True)
    parser.add_argument("--credential-broker-report", type=Path, required=True)
    parser.add_argument("--credential-broker-profile", type=Path, required=True)
    parser.add_argument("--credential-broker-endpoint", required=True)
    parser.add_argument("--credential-broker-ca-file", type=Path, required=True)
    parser.add_argument("--otlp-receiver-qualification-report", type=Path, required=True)
    parser.add_argument("--otlp-receiver-qualification-profile", type=Path, required=True)
    parser.add_argument("--otlp-receiver-api-base-url", required=True)
    parser.add_argument("--otlp-receiver-endpoint", required=True)
    parser.add_argument("--otlp-receiver-api-ca-file", type=Path)
    parser.add_argument("--otlp-receiver-ca-file", type=Path, required=True)
    parser.add_argument("--otlp-receiver-client-certificate-file", type=Path, required=True)
    parser.add_argument("--continuity-report", type=Path, required=True)
    parser.add_argument("--processing-report", type=Path, required=True)
    parser.add_argument("--processing-profile", type=Path, required=True)
    parser.add_argument("--processing-api-base-url", required=True)
    parser.add_argument("--processing-otlp-base-url", required=True)
    parser.add_argument("--postgresql-report", type=Path, required=True)
    parser.add_argument("--postgresql-profile", type=Path, required=True)
    parser.add_argument("--postgresql-database-host", required=True)
    parser.add_argument("--postgresql-database-port", type=int, default=5432)
    parser.add_argument("--values", type=Path, action="append", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", default="iip-system")
    parser.add_argument("--release-name", default="iip")
    parser.add_argument("--deployment", default="iip-infra-intelligence")
    parser.add_argument(
        "--worker-deployment", default="iip-infra-intelligence-worker"
    )
    parser.add_argument(
        "--receiver-deployment", default="iip-infra-intelligence-otlp-receiver"
    )
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--helm", default=os.environ.get("IIP_HELM_BIN", "helm"))
    parser.add_argument("--kubectl", default=os.environ.get("IIP_KUBECTL_BIN", "kubectl"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    _common_inputs(generate)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--maximum-evidence-age-seconds", type=int, default=86400)
    generate.add_argument("--maximum-clock-skew-seconds", type=int, default=300)
    verify = commands.add_parser("verify")
    _common_inputs(verify)
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--require-current-cluster", action="store_true")
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    common = {
        "preflight_path": arguments.preflight_report,
        "diagnostic_path": arguments.diagnostic_report,
        "ingress_path": arguments.ingress_report,
        "oidc_path": arguments.oidc_report,
        "oidc_profile_path": arguments.oidc_profile,
        "oidc_api_base_url": arguments.oidc_api_base_url,
        "policy_path": arguments.policy_report,
        "policy_profile_path": arguments.policy_profile,
        "policy_endpoint": arguments.policy_endpoint,
        "credential_broker_path": arguments.credential_broker_report,
        "credential_broker_profile_path": arguments.credential_broker_profile,
        "credential_broker_endpoint": arguments.credential_broker_endpoint,
        "credential_broker_ca_bundle_path": arguments.credential_broker_ca_file,
        "otlp_receiver_path": arguments.otlp_receiver_qualification_report,
        "otlp_receiver_profile_path": arguments.otlp_receiver_qualification_profile,
        "otlp_receiver_api_base_url": arguments.otlp_receiver_api_base_url,
        "otlp_receiver_endpoint": arguments.otlp_receiver_endpoint,
        "otlp_receiver_api_ca_path": arguments.otlp_receiver_api_ca_file,
        "otlp_receiver_ca_path": arguments.otlp_receiver_ca_file,
        "otlp_receiver_client_certificate_path": arguments.otlp_receiver_client_certificate_file,
        "continuity_path": arguments.continuity_report,
        "processing_path": arguments.processing_report,
        "processing_profile_path": arguments.processing_profile,
        "processing_api_base_url": arguments.processing_api_base_url,
        "processing_otlp_base_url": arguments.processing_otlp_base_url,
        "postgresql_path": arguments.postgresql_report,
        "postgresql_profile_path": arguments.postgresql_profile,
        "postgresql_database_host": arguments.postgresql_database_host,
        "postgresql_database_port": arguments.postgresql_database_port,
        "values": arguments.values,
        "context": arguments.context,
        "namespace": arguments.namespace,
        "release_name": arguments.release_name,
        "deployment_name": arguments.deployment,
        "worker_deployment_name": arguments.worker_deployment,
        "receiver_deployment_name": arguments.receiver_deployment,
        "image_digest": arguments.image_digest,
        "helm": arguments.helm,
        "kubectl": arguments.kubectl,
    }
    try:
        if arguments.command == "generate":
            report = qualify(
                **common,
                output=arguments.output,
                maximum_evidence_age_seconds=arguments.maximum_evidence_age_seconds,
                maximum_clock_skew_seconds=arguments.maximum_clock_skew_seconds,
            )
            print(
                f"customer deployment qualification {report['spec']['status']}: "
                f"{arguments.output}"
            )
            return 0 if report["spec"]["status"] == "qualified" else 1
        report = verify_report(
            **common,
            report_path=arguments.report,
            require_current_cluster=arguments.require_current_cluster,
            require_qualified=arguments.require_qualified,
        )
        print(f"customer deployment qualification verified: {report['spec']['status']}")
        return 0
    except CustomerDeploymentQualificationError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
