#!/usr/bin/env python3
"""Build and execute the example through the signed no-network Docker runner."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from iip.adapters.memory import AllowTenantPolicy  # noqa: E402
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


def main() -> int:
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
        signed = {
            "apiVersion": "iip.plugin-signature/v1",
            "pluginId": manifest["metadata"]["id"],
            "pluginVersion": manifest["metadata"]["version"],
            "protocolVersion": manifest["spec"]["protocolVersion"],
            "artifact": {
                "type": artifact["type"],
                "reference": image_id,
                "digest": image_id,
            },
        }
        artifact["signature"] = {
            "algorithm": "ed25519",
            "keyId": key_id,
            "value": b64url(private.sign(canonical(signed))),
        }
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
        store = InMemoryOperationalStore()
        service = PluginSessionService(AllowTenantPolicy(), store, SystemClock())
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
        now = datetime.now(timezone.utc)
        request = json.loads(
            (PLUGIN / "fixtures" / "collection-request.json").read_text()
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
            AllowTenantPolicy(),
            store,
            FixturePluginMediationGateway(),
        )
        runner = SignedDockerPluginRunner(
            trust,
            configuration=PluginRunnerConfiguration(
                docker_binary=os.environ.get("IIP_DOCKER_BIN", "docker"),
                mediation_bridge_image_reference=bridge_image_id,
            ),
            mediation=mediation,
        )
        result = runner.run(actor, manifest, session, invocation, token)

        expected = json.loads((PLUGIN / "fixtures" / "expected-result.json").read_text())
        if result.get("spec", {}).get("status") != "succeeded":
            code = result.get("spec", {}).get("error", {}).get("code", "unknown")
            raise RuntimeError(f"isolated plugin failed with stable code: {code}")
        if result["spec"]["output"] != expected:
            raise RuntimeError("isolated plugin output did not match its golden contract")
        schema = json.loads(
            (ROOT / "contracts" / "schemas" / "plugin-invocation-result.schema.json").read_text()
        )
        errors = validate_schemas.instance_validation_errors(
            schema, result, label="isolated plugin result"
        )
        if errors:
            raise RuntimeError("; ".join(errors))
        print(
            "signed plugin runner passed: Ed25519 trust → immutable image digest → "
            "no-network sandbox → host-mediated Unix socket read → canonical resource result"
        )
        print(f"image: {image_id}")
        print(f"mediation bridge: {bridge_image_id}")
        print(f"output bytes: {result['spec']['usage']['outputBytes']}")
        return 0
    finally:
        if plugin_built:
            docker("image", "rm", "--force", IMAGE)
        if bridge_built:
            docker("image", "rm", "--force", BRIDGE_IMAGE)


if __name__ == "__main__":
    raise SystemExit(main())
