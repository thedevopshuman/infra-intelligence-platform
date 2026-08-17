from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "infra-intelligence"


class HelmValuesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(
            (CHART / "values.schema.json").read_text(encoding="utf-8")
        )
        cls.values = (CHART / "values.yaml").read_text(encoding="utf-8")

    def test_schema_is_valid_and_covers_every_top_level_value(self) -> None:
        Draft7Validator.check_schema(self.schema)
        value_keys = set(re.findall(r"^([A-Za-z][A-Za-z0-9]*):", self.values, re.M))

        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["properties"]), value_keys)

    def test_security_defaults_cannot_be_relaxed_by_values(self) -> None:
        properties = self.schema["properties"]

        self.assertFalse(
            properties["serviceAccount"]["properties"]["automount"]["const"]
        )
        self.assertTrue(
            properties["podSecurityContext"]["properties"]["runAsNonRoot"]["const"]
        )
        self.assertTrue(
            properties["securityContext"]["properties"][
                "readOnlyRootFilesystem"
            ]["const"]
        )
        self.assertEqual(
            properties["service"]["properties"]["type"]["const"], "ClusterIP"
        )
        self.assertEqual(
            properties["image"]["properties"]["digest"]["pattern"],
            "^$|^sha256:[a-f0-9]{64}$",
        )
        self.assertEqual(
            properties["backup"]["properties"]["destination"]["properties"][
                "mountPath"
            ]["const"],
            "/var/lib/iip-backups",
        )
        self.assertRegex(
            properties["backup"]["properties"]["image"]["properties"]["digest"][
                "pattern"
            ],
            "sha256",
        )
        self.assertIn("existingSecret: iip-auth", self.values)
        self.assertIn('digest: ""', self.values)
        worker = properties["worker"]["properties"]
        self.assertEqual(worker["investigationConcurrency"]["maximum"], 64)
        self.assertEqual(
            worker["maxTenantInvestigationConcurrency"]["maximum"], 64
        )
        self.assertEqual(
            properties["investigationQueue"]["properties"][
                "maxOutstandingJobsPerTenant"
            ]["maximum"],
            100000,
        )

    def test_cross_field_guards_fail_before_a_workload_is_rendered(self) -> None:
        guards = (CHART / "templates" / "validation.yaml").read_text(
            encoding="utf-8"
        )

        for expected in (
            "worker.heartbeatSeconds must be less than worker.leaseSeconds",
            "eventPublisher.retryBaseSeconds must not exceed",
            "eventDeliverySlo.maximumDeliveryLatencySeconds must be less than",
            "investigationCompletionSlo.maximumCompletionSeconds must be less than",
            "telemetry.traceMaxExportBatchSize must not exceed",
            "telemetry.otlpEndpoint is required",
            "auth.existingSecret is required",
            "policy.externalHttp.configJson is required",
            "credentialBroker.externalHttp.configJson is required",
            "otlpReceiver.channelsExistingSecret is required",
            "otlpLogsReceiver.channelsExistingSecret is required",
            "backup.destination.existingClaim is required",
            "networkPolicy.databaseEgress.enabled must be true when backup is enabled",
            "ingress.tls.existingSecret is required",
            "ingress.tlsRedirectAnnotation is required",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guards)


if __name__ == "__main__":
    unittest.main()
