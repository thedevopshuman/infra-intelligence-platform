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
            "apiTermination.endpointDrainSeconds must be less than",
            "otlpIngest.endpointDrainSeconds must be less than",
            "worker.enabled must be true when worker.podDisruptionBudget.enabled=true",
            "an OTLP receiver must be enabled when otlpIngest.podDisruptionBudget.enabled=true",
            "podDisruptionBudget.minAvailable must be less than replicaCount",
            "worker.podDisruptionBudget.minAvailable must be less than worker.replicaCount",
            "otlpIngest.podDisruptionBudget.minAvailable must be less than otlpIngest.replicaCount",
            "eventPublisher.retryBaseSeconds must not exceed",
            "eventDeliverySlo.maximumDeliveryLatencySeconds must be less than",
            "investigationCompletionSlo.maximumCompletionSeconds must be less than",
            "evidenceRetention.ephemeralSeconds must not exceed",
            "evidenceRetention.standardSeconds must not exceed",
            "worker.enabled must be true when evidenceRetention.enabled=true",
            "worker.enabled must be true when aiAttribution.enabled=true",
            "aiAttribution.policiesExistingSecret is required",
            "aiAttribution.enabled must be true when attribution test fixtures are allowed",
            "worker.enabled must be true when aiCostEngine.enabled=true",
            "aiCostEngine.catalogsExistingSecret is required",
            "aiCostEngine.enabled must be true when test fixtures are allowed",
            "worker.enabled must be true when aiSavingsEngine.enabled=true",
            "aiCostEngine.enabled must be true when aiSavingsEngine.enabled=true",
            "aiSavingsEngine.profilesExistingSecret is required",
            "worker.enabled must be true when aiAllocationReporting.enabled=true",
            "aiAttribution.enabled must be true when aiAllocationReporting.enabled=true",
            "aiCostEngine.enabled must be true when aiAllocationReporting.enabled=true",
            "telemetry.traceMaxExportBatchSize must not exceed",
            "telemetry.otlpEndpoint is required",
            "auth.existingSecret is required",
            "policy.externalHttp.configJson is required",
            "credentialBroker.externalHttp.configJson is required",
            "otlpReceiver.channelsExistingSecret is required",
            "otlpLogsReceiver.channelsExistingSecret is required",
            "aiUsageReceiver.channelsExistingSecret is required",
            "otlpIngest.tls.serverExistingSecret is required",
            "otlpIngest.tls.clientCaExistingSecret is required",
            "otlpIngest.tls.identitiesExistingSecret is required",
            "backup.destination.existingClaim is required",
            "networkPolicy.databaseEgress.enabled must be true when backup is enabled",
            "ingress.tls.existingSecret is required",
            "ingress.tlsRedirectAnnotation is required",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guards)

        retention = self.schema["properties"]["evidenceRetention"]
        self.assertFalse(retention["additionalProperties"])
        self.assertEqual(
            retention["properties"]["batchSize"]["maximum"],
            1000,
        )

        ai_cost = self.schema["properties"]["aiCostEngine"]
        self.assertFalse(ai_cost["additionalProperties"])
        self.assertEqual(ai_cost["properties"]["batchSize"]["maximum"], 1000)
        ai_attribution = self.schema["properties"]["aiAttribution"]
        self.assertFalse(ai_attribution["additionalProperties"])
        self.assertEqual(
            ai_attribution["properties"]["batchSize"]["maximum"],
            1000,
        )
        ai_savings = self.schema["properties"]["aiSavingsEngine"]
        self.assertFalse(ai_savings["additionalProperties"])
        self.assertEqual(
            ai_savings["properties"]["intervalSeconds"]["maximum"], 3600
        )
        allocation = self.schema["properties"]["aiAllocationReporting"]
        self.assertFalse(allocation["additionalProperties"])
        self.assertEqual(
            allocation["properties"]["sourceRecordLimit"]["maximum"],
            10000,
        )
        self.assertEqual(
            allocation["properties"]["windowSeconds"]["maximum"],
            2678400,
        )
        telemetry = self.schema["properties"]["telemetry"]
        self.assertEqual(
            telemetry["properties"]["aiEconomicsAttributeMode"]["enum"],
            ["scope", "tenant-scope"],
        )
        self.assertIn("aiEconomicsAttributeMode: tenant-scope", self.values)

        termination = self.schema["properties"]["apiTermination"]
        self.assertFalse(termination["additionalProperties"])
        self.assertEqual(
            set(termination["required"]),
            {"gracePeriodSeconds", "endpointDrainSeconds"},
        )
        self.assertEqual(
            termination["properties"]["gracePeriodSeconds"]["maximum"],
            600,
        )
        properties = self.schema["properties"]
        self.assertEqual(
            properties["worker"]["properties"]["terminationGracePeriodSeconds"][
                "maximum"
            ],
            900,
        )
        self.assertEqual(
            properties["otlpIngest"]["properties"][
                "terminationGracePeriodSeconds"
            ]["maximum"],
            600,
        )
        topology = properties["availability"]["properties"]["topologySpread"]
        self.assertFalse(properties["availability"]["additionalProperties"])
        self.assertEqual(
            topology["properties"]["whenUnsatisfiable"]["enum"],
            ["DoNotSchedule", "ScheduleAnyway"],
        )
        self.assertEqual(topology["properties"]["minDomains"]["minimum"], 2)
        self.assertEqual(
            termination["properties"]["endpointDrainSeconds"]["maximum"],
            60,
        )


if __name__ == "__main__":
    unittest.main()
