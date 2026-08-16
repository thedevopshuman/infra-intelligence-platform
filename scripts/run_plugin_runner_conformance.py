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
from iip.adapters.plugin_runner import (  # noqa: E402
    PluginRunnerConfiguration,
    PluginTrustStore,
    SignedDockerPluginRunner,
)
from iip.application.plugin_sessions import (  # noqa: E402
    OpenPluginSessionCommand,
    PluginSessionService,
)
from iip.application.ports import ActorContext  # noqa: E402
from iip.bootstrap import SystemClock  # noqa: E402
import validate_schemas  # noqa: E402


IMAGE = "iip-kubernetes-observer-runner-test:local"
DOCKERFILE = ROOT / "plugins" / "examples" / "kubernetes-observer" / "Dockerfile"
EXAMPLES = ROOT / "contracts" / "examples"
PLUGIN = ROOT / "plugins" / "examples" / "kubernetes-observer"


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
    docker("build", "--file", str(DOCKERFILE), "--tag", IMAGE, ".")
    try:
        image_id = docker("image", "inspect", "--format", "{{.Id}}", IMAGE, capture=True)
        if not image_id.startswith("sha256:"):
            raise RuntimeError("plugin runner image is not digest-addressable")

        manifest = json.loads((EXAMPLES / "plugin-manifest.json").read_text())
        manifest["spec"]["permissions"]["network"] = []
        manifest["spec"]["permissions"]["secrets"] = []
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
        service = PluginSessionService(
            AllowTenantPolicy(), InMemoryOperationalStore(), SystemClock()
        )
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
        invocation = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocation",
            "metadata": {
                "id": "pin_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32],
                "sessionId": session["metadata"]["id"],
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
                "createdAt": now.isoformat().replace("+00:00", "Z"),
                "deadline": (now + timedelta(seconds=30))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            "spec": {
                "manifestDigest": session["spec"]["manifestDigest"],
                "capability": "resource-observer",
                "method": "collect",
                "input": request,
            },
        }
        runner = SignedDockerPluginRunner(
            trust,
            configuration=PluginRunnerConfiguration(
                docker_binary=os.environ.get("IIP_DOCKER_BIN", "docker")
            ),
        )
        result = runner.run(actor, manifest, session, invocation, token)

        expected = json.loads((PLUGIN / "fixtures" / "expected-result.json").read_text())
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
            "no-network sandbox → canonical resource result"
        )
        print(f"image: {image_id}")
        print(f"output bytes: {result['spec']['usage']['outputBytes']}")
        return 0
    finally:
        docker("image", "rm", IMAGE)


if __name__ == "__main__":
    raise SystemExit(main())
