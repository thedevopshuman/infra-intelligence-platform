from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "infra-intelligence"
EXAMPLE = CHART / "examples" / "iip-database.externalsecret.yaml"
RUNBOOK = ROOT / "docs" / "operations" / "external-secrets.md"
SCRIPT = ROOT / "scripts" / "test_external_secrets.sh"


class ExternalSecretHandoffTests(unittest.TestCase):
    def test_production_example_contains_no_secret_value_or_provider_identity(self) -> None:
        manifest = EXAMPLE.read_text(encoding="utf-8")

        self.assertIn("apiVersion: external-secrets.io/v1", manifest)
        self.assertIn("name: iip-database", manifest)
        self.assertIn("name: iip-database-ca", manifest)
        self.assertIn("secretKey: database-url", manifest)
        self.assertIn("secretKey: ca.crt", manifest)
        self.assertIn("property: ca", manifest)
        self.assertIn("name: iip-production-secret-store", manifest)
        self.assertNotIn("stringData:", manifest)
        self.assertNotIn("data:\n  database-url:", manifest)
        self.assertNotIn("postgresql://", manifest)
        self.assertNotIn("BEGIN CERTIFICATE", manifest)
        self.assertNotIn("accessKey", manifest)
        self.assertNotIn("clientSecret", manifest)

    def test_compatibility_profile_is_version_and_digest_bound(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("IIP_ESO_CHART_VERSION=2.10.0", script)
        self.assertIn(
            "IIP_ESO_CHART_SHA256=b96e948fff3674638b5d3f9e43886f3796e04739c4b4127929aed2ddac7d1418",
            script,
        )
        self.assertIn(
            "IIP_ESO_IMAGE_DIGEST=sha256:814117b0fd6d121b03e8ba3b6db1cecbe7449a354fc0fc9c4faf73a37aa221b1",
            script,
        )
        self.assertIn("--set-string image.tag=", script)
        self.assertIn("--set-string webhook.image.tag=", script)
        self.assertIn("--set-string certController.image.tag=", script)

    def test_runbook_inventories_every_chart_secret_name_boundary(self) -> None:
        values = (CHART / "values.yaml").read_text(encoding="utf-8")
        runbook = RUNBOOK.read_text(encoding="utf-8")
        stack: list[tuple[int, str]] = []
        boundaries: set[str] = set()

        for line in values.splitlines():
            match = re.match(r"^(\s*)([A-Za-z][A-Za-z0-9]*):(?:\s*(.*))?$", line)
            if match is None:
                continue
            indent = len(match.group(1))
            key = match.group(2)
            value = match.group(3) or ""
            while stack and stack[-1][0] >= indent:
                stack.pop()
            path = ".".join([item[1] for item in stack] + [key])
            if key == "imagePullSecrets" or key.endswith("ExistingSecret") or key == "existingSecret":
                boundaries.add(path)
            if not value or value.lstrip().startswith("#"):
                stack.append((indent, key))

        self.assertEqual(35, len(boundaries))
        for boundary in sorted(boundaries):
            with self.subTest(boundary=boundary):
                self.assertIn(f"`{boundary}`", runbook)

    def test_compatibility_profile_proves_rotation_and_named_secret_authority(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('resourceNames: ["iip-upstream-core"]', script)
        self.assertIn('verbs: ["get"]', script)
        self.assertIn("auth can-i", script)
        self.assertIn("list secrets", script)
        self.assertIn("get secret/not-authorized", script)
        self.assertIn("force-sync=", script)
        self.assertIn("IIP_ROTATED_TARGET_AUTH", script)
        self.assertIn("IIP_ROTATED_TARGET_DATABASE_CA", script)
        self.assertIn("--timeout 600s", script)
        self.assertIn("secretKey: ca.crt", script)
        self.assertIn("property: ca", script)
        self.assertIn(
            "--set database.transportSecurity.caExistingSecret=iip-database-ca",
            script,
        )
        self.assertIn("secretKey: identities-json", script)
        self.assertIn("property: identities-json", script)
        self.assertIn('--context "$IIP_KUBE_CONTEXT" cluster-info', script)
        self.assertNotIn("current-context", script)
        self.assertLess(
            script.index("delete externalsecret"),
            script.index("uninstall external-secrets"),
        )
        self.assertNotIn("echo \"$IIP_DATABASE_URL\"", script)
        self.assertNotIn("echo \"$IIP_AUTH_TOKEN\"", script)


if __name__ == "__main__":
    unittest.main()
