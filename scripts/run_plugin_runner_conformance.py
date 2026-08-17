#!/usr/bin/env python3
"""Build and execute the example through the signed no-network Docker runner."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "sdks" / "python" / "src"))

from infra_intelligence_sdk import __version__ as sdk_version  # noqa: E402
from iip import __version__ as application_version  # noqa: E402
from iip.adapters.actions import KubernetesRestartDryRunExecutor  # noqa: E402
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore  # noqa: E402
from iip.adapters.operations import InMemoryOperationalStore  # noqa: E402
from iip.adapters.plugin_mediation import (  # noqa: E402
    StaticPluginMediationBindingRegistry,
)
from iip.adapters.plugin_runner import (  # noqa: E402
    PluginRunnerConfiguration,
    PluginTrustStore,
    SignedDockerPluginRunner,
)
from iip.application.plugin_sessions import (  # noqa: E402
    OpenPluginSessionCommand,
    PluginSessionService,
)
from iip.application.actions import GovernedActionService  # noqa: E402
from iip.application.ingest_resource import (  # noqa: E402
    IngestResourceCommand,
    ResourceIngestionService,
)
from iip.application.plugin_mediation import PluginMediationService  # noqa: E402
from iip.application.ports import ActorContext, PluginMediationBinding  # noqa: E402
from iip.bootstrap import SystemClock  # noqa: E402
import validate_schemas  # noqa: E402


IMAGE = "iip-kubernetes-observer-runner-test:local"
BRIDGE_IMAGE = "iip-plugin-mediation-bridge-test:local"
DOCKERFILE = ROOT / "plugins" / "examples" / "kubernetes-observer" / "Dockerfile"
BRIDGE_DOCKERFILE = ROOT / "deploy" / "plugin-mediation-bridge" / "Dockerfile"
EXAMPLES = ROOT / "contracts" / "examples"
PLUGIN = ROOT / "plugins" / "examples" / "kubernetes-observer"


class FixturePluginMediationGateway:
    """Conformance provider returns an untrusted fixture without network egress."""

    def fetch_plugin_json(self, actor, binding, *, path, query, deadline, max_response_bytes):
        del actor, binding, path, query, deadline, max_response_bytes
        return json.loads((PLUGIN / "fixtures" / "kubernetes-list.json").read_text())


def docker(*arguments: str, capture: bool = False) -> str:
    binary = os.environ.get("IIP_DOCKER_BIN", "docker")
    completed = subprocess.run(
        [binary, *arguments],
        cwd=ROOT,
        text=True,
        capture_output=capture,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() if capture else ""
        raise RuntimeError(f"plugin runner Docker setup failed: {detail}")
    return completed.stdout.strip() if capture else ""


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def canonical(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def sign_manifest(
    manifest: dict[str, object], private: Ed25519PrivateKey, key_id: str
) -> None:
    spec = manifest.get("spec")
    artifact = spec.get("artifact") if isinstance(spec, dict) else None
    metadata = manifest.get("metadata")
    if not isinstance(artifact, dict) or not isinstance(metadata, dict):
        raise RuntimeError("plugin manifest cannot be signed")
    artifact.pop("signature", None)
    manifest_digest = "sha256:" + hashlib.sha256(canonical(manifest)).hexdigest()
    signed = {
        "apiVersion": "iip.plugin-signature/v2",
        "pluginId": metadata["id"],
        "pluginVersion": metadata["version"],
        "protocolVersion": spec["protocolVersion"],
        "manifestDigest": manifest_digest,
        "artifact": {
            "type": artifact["type"],
            "reference": artifact["reference"],
            "digest": artifact["digest"],
        },
    }
    artifact["signature"] = {
        "profile": "iip.plugin-signature/v2",
        "algorithm": "ed25519",
        "keyId": key_id,
        "manifestDigest": manifest_digest,
        "value": b64url(private.sign(canonical(signed))),
    }


def arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "plugin-compatibility-report.json",
        help="machine-readable compatibility report output",
    )
    return parser.parse_args(tuple(argv) if argv is not None else None)


def validate_contract(schema_name: str, document: object, *, label: str) -> None:
    schema = json.loads(
        (ROOT / "contracts" / "schemas" / schema_name).read_text(encoding="utf-8")
    )
    errors = validate_schemas.instance_validation_errors(schema, document, label=label)
    if errors:
        raise RuntimeError("; ".join(errors))


def validate_result(result: Mapping[str, object], expected: object, *, profile: str) -> None:
    spec = result.get("spec")
    if not isinstance(spec, Mapping) or spec.get("status") != "succeeded":
        error = spec.get("error") if isinstance(spec, Mapping) else None
        code = error.get("code") if isinstance(error, Mapping) else "unknown"
        raise RuntimeError(f"{profile} plugin failed with stable code: {code}")
    if spec.get("output") != expected:
        raise RuntimeError(f"{profile} plugin output did not match its golden contract")
    validate_contract(
        "plugin-invocation-result.schema.json",
        result,
        label=f"{profile} isolated plugin result",
    )


def validate_action_result(result: Mapping[str, object]) -> Mapping[str, object]:
    """Validate the isolated action plugin and return its proposal-only receipt."""

    spec = result.get("spec")
    output = spec.get("output") if isinstance(spec, Mapping) else None
    if (
        not isinstance(spec, Mapping)
        or spec.get("status") != "succeeded"
        or not isinstance(output, Mapping)
        or not isinstance(output.get("spec"), Mapping)
        or output["spec"].get("status") != "proposed"
    ):
        error = spec.get("error") if isinstance(spec, Mapping) else None
        code = error.get("code") if isinstance(error, Mapping) else "unknown"
        raise RuntimeError(
            f"host-mediated-action-proposal plugin failed with stable code: {code}"
        )
    validate_contract(
        "plugin-invocation-result.schema.json",
        result,
        label="host-mediated-action-proposal isolated plugin result",
    )
    validate_contract(
        "plugin-action-mediation-response.schema.json",
        output,
        label="host-mediated-action-proposal receipt",
    )
    return output


def passed_checks(*identifiers: str) -> list[dict[str, str]]:
    return [{"id": identifier, "status": "passed"} for identifier in identifiers]


def compatibility_report(
    *,
    manifest: Mapping[str, object],
    offline_session: Mapping[str, object],
    mediated_session: Mapping[str, object],
    action_session: Mapping[str, object],
    image_id: str,
    bridge_image_id: str,
) -> dict[str, object]:
    metadata = manifest["metadata"]
    spec = manifest["spec"]
    offline_session_spec = offline_session["spec"]
    mediated_session_spec = mediated_session["spec"]
    action_session_spec = action_session["spec"]
    if not all(
        isinstance(value, Mapping)
        for value in (
            metadata,
            spec,
            offline_session_spec,
            mediated_session_spec,
            action_session_spec,
        )
    ):
        raise RuntimeError("plugin compatibility identity is invalid")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    )
    platform = docker("info", "--format", "{{.OSType}}/{{.Architecture}}", capture=True)
    runtime_version = docker(
        "version", "--format", "{{.Server.Version}}", capture=True
    )
    identity = canonical(
        {
            "revision": revision,
            "platform": platform,
            "plugin": image_id,
            "bridge": bridge_image_id,
            "offlineManifest": offline_session_spec["manifestDigest"],
            "mediatedManifest": mediated_session_spec["manifestDigest"],
            "actionManifest": action_session_spec["manifestDigest"],
        }
    )
    profiles = [
        {
            "name": "offline-fixture",
            "capability": "resource-observer",
            "method": "collect",
            "manifestDigest": offline_session_spec["manifestDigest"],
            "result": "compatible",
            "checks": passed_checks(
                "manifest-schema",
                "publisher-signature",
                "immutable-plugin-artifact",
                "no-network-sandbox",
                "bounded-sandbox",
                "input-contract",
                "output-contract",
                "golden-result",
            ),
        },
        {
            "name": "host-mediated-read",
            "capability": "resource-observer",
            "method": "collect",
            "manifestDigest": mediated_session_spec["manifestDigest"],
            "result": "compatible",
            "checks": passed_checks(
                "manifest-schema",
                "publisher-signature",
                "immutable-plugin-artifact",
                "immutable-mediation-bridge",
                "no-network-sandbox",
                "bounded-sandbox",
                "input-contract",
                "output-contract",
                "golden-result",
                "invocation-local-socket",
                "host-mediated-read",
                "credentials-host-only",
            ),
        },
        {
            "name": "host-mediated-action-proposal",
            "capability": "action-provider",
            "method": "propose-restart",
            "manifestDigest": action_session_spec["manifestDigest"],
            "result": "compatible",
            "checks": passed_checks(
                "manifest-schema",
                "publisher-signature",
                "immutable-plugin-artifact",
                "immutable-mediation-bridge",
                "no-network-sandbox",
                "bounded-sandbox",
                "input-contract",
                "output-contract",
                "invocation-local-socket",
                "host-mediated-action-proposal",
                "governed-proposal-queue",
                "approval-not-granted",
                "execution-not-granted",
            ),
        },
    ]
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "PluginCompatibilityReport",
        "metadata": {
            "id": "pcr_" + hashlib.sha256(identity).hexdigest()[:32],
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sourceRevision": revision,
            "sourceDirty": dirty,
        },
        "spec": {
            "host": {
                "platform": platform,
                "containerRuntime": "docker",
                "containerRuntimeVersion": runtime_version,
                "applicationVersion": application_version,
                "sdk": {"language": "python", "version": sdk_version},
            },
            "plugin": {
                "id": metadata["id"],
                "version": metadata["version"],
                "protocolVersion": spec["protocolVersion"],
                "capability": "resource-observer",
                "method": "collect",
                "artifactDigest": image_id,
                "mediationBridgeDigest": bridge_image_id,
            },
            "profiles": profiles,
            "summary": {
                "totalProfiles": len(profiles),
                "compatibleProfiles": len(profiles),
                "incompatibleProfiles": 0,
                "overallStatus": "compatible",
            },
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    options = arguments(argv)
    options.report.unlink(missing_ok=True)
    plugin_built = False
    bridge_built = False
    try:
        docker("build", "--file", str(DOCKERFILE), "--tag", IMAGE, ".")
        plugin_built = True
        docker("build", "--file", str(BRIDGE_DOCKERFILE), "--tag", BRIDGE_IMAGE, ".")
        bridge_built = True
        image_id = docker("image", "inspect", "--format", "{{.Id}}", IMAGE, capture=True)
        bridge_image_id = docker(
            "image", "inspect", "--format", "{{.Id}}", BRIDGE_IMAGE, capture=True
        )
        if not image_id.startswith("sha256:"):
            raise RuntimeError("plugin runner image is not digest-addressable")
        if not bridge_image_id.startswith("sha256:"):
            raise RuntimeError("plugin mediation bridge is not digest-addressable")

        manifest = json.loads((EXAMPLES / "plugin-manifest.json").read_text())
        artifact = manifest["spec"]["artifact"]
        artifact["reference"] = image_id
        artifact["digest"] = image_id

        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        key_id = "local-plugin-runner-smoke"
        sign_manifest(manifest, private, key_id)
        offline_manifest = copy.deepcopy(manifest)
        offline_manifest["spec"]["permissions"]["network"] = []
        offline_manifest["spec"]["permissions"]["secrets"] = []
        sign_manifest(offline_manifest, private, key_id)
        action_manifest = copy.deepcopy(manifest)
        action_manifest["metadata"]["displayName"] = "Kubernetes Action Proposal Conformance"
        action_manifest["metadata"]["description"] = (
            "Proposal-only action-provider profile for the isolated runner matrix."
        )
        action_manifest["spec"]["capabilities"] = ["action-provider"]
        action_manifest["spec"]["interfaces"] = [
            {
                "capability": "action-provider",
                "method": "propose-restart",
                "inputSchema": (
                    "urn:iip:contracts:plugin-action-mediation-request:v1alpha1"
                ),
                "outputSchema": (
                    "urn:iip:contracts:plugin-action-mediation-response:v1alpha1"
                ),
            }
        ]
        action_manifest["spec"]["permissions"] = {
            "network": [],
            "secrets": [],
            "resources": [],
            "actions": ["kubernetes.restart-workload"],
        }
        action_manifest["spec"]["configSchema"] = {
            "type": "object",
            "additionalProperties": False,
        }
        sign_manifest(action_manifest, private, key_id)
        validate_contract(
            "plugin-manifest.schema.json",
            manifest,
            label="signed mediated plugin manifest",
        )
        validate_contract(
            "plugin-manifest.schema.json",
            offline_manifest,
            label="signed offline plugin manifest",
        )
        validate_contract(
            "plugin-manifest.schema.json",
            action_manifest,
            label="signed action-provider plugin manifest",
        )
        trust = PluginTrustStore.from_json(
            json.dumps(
                {
                    "keys": [
                        {
                            "keyId": key_id,
                            "publisher": manifest["metadata"]["publisher"],
                            "publicKey": b64url(public),
                        }
                    ]
                }
            )
        )

        actor = ActorContext("plugin-host", "local", ("developer",))
        token = "local-plugin-capability-token-0123456789abcdef"
        offline_token = "local-plugin-offline-token-0123456789abcdef"
        action_token = "local-plugin-action-token-0123456789abcdef"
        store = InMemoryOperationalStore()
        policy = AllowTenantPolicy()
        clock = SystemClock()
        service = PluginSessionService(policy, store, clock)
        session = service.open(
            OpenPluginSessionCommand(
                actor,
                manifest,
                ("resource-observer",),
                token,
                max_requests=1,
                max_wall_time_seconds=60,
            )
        )
        offline_session = service.open(
            OpenPluginSessionCommand(
                actor,
                offline_manifest,
                ("resource-observer",),
                offline_token,
                max_requests=1,
                max_wall_time_seconds=60,
            )
        )
        action_session = service.open(
            OpenPluginSessionCommand(
                actor,
                action_manifest,
                ("action-provider",),
                action_token,
                max_requests=1,
                max_wall_time_seconds=60,
            )
        )
        now = datetime.now(timezone.utc)
        request = json.loads(
            (PLUGIN / "fixtures" / "collection-request.json").read_text()
        )
        validate_contract(
            "resource-collection-request.schema.json",
            request,
            label="plugin collection request",
        )
        resources = InMemoryResourceStore()
        target = ResourceIngestionService(resources, policy).execute(
            IngestResourceCommand(
                ActorContext("collector-local", actor.tenant_id),
                json.loads((EXAMPLES / "resource.json").read_text()),
            )
        )
        investigation_request = json.loads(
            (EXAMPLES / "investigation-request.json").read_text()
        )
        investigation_report = json.loads(
            (EXAMPLES / "investigation-report.json").read_text()
        )
        investigation_id = investigation_request["metadata"]["id"]
        investigation_request["spec"]["scope"]["resourceUids"] = [
            target.identity.uid
        ]
        investigation_report["spec"]["scope"]["resourceUids"] = [
            target.identity.uid
        ]
        investigation_report["spec"]["requestDigest"] = (
            "sha256:" + hashlib.sha256(canonical(investigation_request)).hexdigest()
        )
        store.commit_investigation(
            actor,
            investigation_id,
            investigation_request,
            investigation_report,
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "InvestigationStatus",
                "metadata": {
                    "id": investigation_id,
                    "tenantId": actor.tenant_id,
                    "updatedAt": investigation_report["spec"]["completedAt"],
                },
                "spec": {
                    "requestDigest": investigation_report["spec"]["requestDigest"],
                    "state": "completed",
                    "startedAt": investigation_report["spec"]["startedAt"],
                    "completedAt": investigation_report["spec"]["completedAt"],
                    "reportRef": (
                        f"investigation://{actor.tenant_id}/{investigation_id}/report"
                    ),
                },
            },
        )
        action_gateway = GovernedActionService(
            resources,
            policy,
            store,
            KubernetesRestartDryRunExecutor(),
            store,
            clock,
            store,
        )
        invocation_id = "pin_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        grant_id = "pmg_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        deadline = (now + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        grant = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginMediationGrant",
            "metadata": {
                "id": grant_id,
                "invocationId": invocation_id,
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
                "issuedAt": now.isoformat().replace("+00:00", "Z"),
                "expiresAt": deadline,
            },
            "spec": {
                "integrationId": "kubernetes-local",
                "provider": "kubernetes",
                "destination": "kubernetes.default.svc:443",
                "credentialName": "kubernetes.projected-service-account-token",
                "operation": "http-json-read",
                "pathTemplates": ["/api/v1/namespaces/{namespace}/pods"],
                "queryKeys": ["limit"],
                "scopes": ["kubernetes:read"],
                "limits": {"maxRequests": 1, "maxResponseBytes": 1_048_576},
            },
        }
        action_invocation_id = "pin_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        action_grant_id = "pag_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        action_request_id = "par_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        action_grant = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginActionMediationGrant",
            "metadata": {
                "id": action_grant_id,
                "invocationId": action_invocation_id,
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
                "issuedAt": now.isoformat().replace("+00:00", "Z"),
                "expiresAt": deadline,
            },
            "spec": {
                "operation": "governed-action-proposal",
                "actionTypes": ["kubernetes.restart-workload"],
                "targetResourceUids": [target.identity.uid],
                "dryRunPolicy": "required",
                "limits": {
                    "maxRequests": 1,
                    "maxProposalLifetimeSeconds": 300,
                },
            },
        }
        action_request = {
            "apiVersion": "iip.plugin-runtime/v1alpha1",
            "kind": "PluginActionMediationRequest",
            "metadata": {
                "id": action_request_id,
                "invocationId": action_invocation_id,
                "grantId": action_grant_id,
            },
            "spec": {
                "investigationId": investigation_id,
                "actionType": "kubernetes.restart-workload",
                "targetResourceUid": target.identity.uid,
                "parameters": {
                    "namespace": "default",
                    "workloadKind": "deployment",
                    "workloadName": "api",
                },
                "dryRun": True,
            },
        }
        action_invocation = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocation",
            "metadata": {
                "id": action_invocation_id,
                "sessionId": action_session["metadata"]["id"],
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
                "createdAt": now.isoformat().replace("+00:00", "Z"),
                "deadline": deadline,
            },
            "spec": {
                "manifestDigest": action_session["spec"]["manifestDigest"],
                "capability": "action-provider",
                "method": "propose-restart",
                "input": action_request,
                "actionMediationGrants": [action_grant],
            },
        }
        validate_contract(
            "plugin-action-mediation-grant.schema.json",
            action_grant,
            label="action-provider mediation grant",
        )
        validate_contract(
            "plugin-action-mediation-request.schema.json",
            action_request,
            label="action-provider mediation request",
        )
        validate_contract(
            "plugin-invocation.schema.json",
            action_invocation,
            label="action-provider invocation",
        )
        invocation = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocation",
            "metadata": {
                "id": invocation_id,
                "sessionId": session["metadata"]["id"],
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
                "createdAt": now.isoformat().replace("+00:00", "Z"),
                "deadline": deadline,
            },
            "spec": {
                "manifestDigest": session["spec"]["manifestDigest"],
                "capability": "resource-observer",
                "method": "collect",
                "input": request,
                "mediationGrants": [grant],
            },
        }
        binding = PluginMediationBinding(
            tenant_id=actor.tenant_id,
            plugin_id=manifest["metadata"]["id"],
            plugin_version=manifest["metadata"]["version"],
            grant_id=grant_id,
            integration_id="kubernetes-local",
            provider="kubernetes",
            destination="kubernetes.default.svc:443",
            credential_name="kubernetes.projected-service-account-token",
            endpoint="https://kubernetes.default.svc",
            credential_ref="credential://local/integrations/kubernetes-local/token",
            ca_bundle_path=None,
            path_templates=("/api/v1/namespaces/{namespace}/pods",),
            query_keys=("limit",),
            scopes=("kubernetes:read",),
            max_requests=1,
            max_response_bytes=1_048_576,
        )
        mediation = PluginMediationService(
            StaticPluginMediationBindingRegistry((binding,)),
            policy,
            store,
            FixturePluginMediationGateway(),
            action_gateway=action_gateway,
        )
        runner = SignedDockerPluginRunner(
            trust,
            configuration=PluginRunnerConfiguration(
                docker_binary=os.environ.get("IIP_DOCKER_BIN", "docker"),
                mediation_bridge_image_reference=bridge_image_id,
            ),
            mediation=mediation,
        )
        offline_invocation = copy.deepcopy(invocation)
        offline_invocation["metadata"]["id"] = (
            "pin_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        )
        offline_invocation["metadata"]["sessionId"] = offline_session["metadata"][
            "id"
        ]
        offline_invocation["spec"]["manifestDigest"] = offline_session["spec"][
            "manifestDigest"
        ]
        offline_invocation["spec"].pop("mediationGrants")
        offline_result = runner.run(
            actor,
            offline_manifest,
            offline_session,
            offline_invocation,
            offline_token,
        )
        mediated_result = runner.run(actor, manifest, session, invocation, token)
        action_result = runner.run(
            actor,
            action_manifest,
            action_session,
            action_invocation,
            action_token,
        )

        expected = json.loads((PLUGIN / "fixtures" / "expected-result.json").read_text())
        validate_result(offline_result, expected, profile="offline-fixture")
        validate_result(mediated_result, expected, profile="host-mediated-read")
        action_receipt = validate_action_result(action_result)
        action_receipt_spec = action_receipt["spec"]
        assert isinstance(action_receipt_spec, Mapping)
        proposal_id = action_receipt_spec["proposalId"]
        assert isinstance(proposal_id, str)
        queued = store.get_proposal(actor, proposal_id)
        queued_spec = queued.get("spec") if isinstance(queued, Mapping) else None
        queued_digest = (
            "sha256:" + hashlib.sha256(canonical(queued)).hexdigest()
            if isinstance(queued, Mapping)
            else None
        )
        if (
            not isinstance(queued, Mapping)
            or queued.get("status") != "pending-approval"
            or not isinstance(queued_spec, Mapping)
            or queued_spec.get("investigationId") != investigation_id
            or queued_spec.get("targetResourceUid") != target.identity.uid
            or action_receipt_spec.get("proposalDigest") != queued_digest
            or store.get_approval(actor, proposal_id) is not None
            or store.get_action_execution_status(actor, proposal_id) is not None
            or store.get_action_result(actor, proposal_id) is not None
        ):
            raise RuntimeError(
                "host-mediated-action-proposal did not stop at the governed proposal queue"
            )
        report = compatibility_report(
            manifest=manifest,
            offline_session=offline_session,
            mediated_session=session,
            action_session=action_session,
            image_id=image_id,
            bridge_image_id=bridge_image_id,
        )
        validate_contract(
            "plugin-compatibility-report.schema.json",
            report,
            label="plugin compatibility report",
        )
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            "plugin compatibility matrix passed: signed immutable image → no-network "
            "offline fixture + host-mediated Unix socket read + proposal-only governed action"
        )
        print(f"image: {image_id}")
        print(f"mediation bridge: {bridge_image_id}")
        print(
            "profiles: offline-fixture=compatible, host-mediated-read=compatible, "
            "host-mediated-action-proposal=compatible"
        )
        print(f"report: {options.report}")
        return 0
    finally:
        if plugin_built:
            docker("image", "rm", "--force", IMAGE)
        if bridge_built:
            docker("image", "rm", "--force", BRIDGE_IMAGE)


if __name__ == "__main__":
    raise SystemExit(main())
