from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SDK = ROOT / "sdks" / "python" / "src"
for entry in (str(SCRIPTS), str(SDK)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import qualify_customer_ai_finops_flow as flow  # noqa: E402
from infra_intelligence_sdk import (  # noqa: E402
    CustomerAiFinopsFlowQualificationProfile,
    CustomerAiFinopsFlowQualificationReport,
)


REVISION = "a" * 40
NOW = datetime(2026, 9, 8, 10, 12, tzinfo=timezone.utc)
TRACE_ID = "0123456789abcdef0123456789abcdef"
SPAN_ID = "0123456789abcdef"


def _example(name: str) -> dict[str, object]:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


class CustomerAiFinopsFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _example("customer-ai-finops-flow-qualification-profile.json")
        self.prerequisite_profile = _example(
            "customer-ai-finops-prerequisite-profile.json"
        )
        self.prerequisite_report = _example(
            "customer-ai-finops-prerequisite-report.json"
        )
        prerequisite_profile_digest = flow._digest(self.prerequisite_profile)
        prerequisite_report_digest = flow._digest(self.prerequisite_report)
        self.prerequisite_profile_document = flow.FileDocument(
            self.prerequisite_profile, prerequisite_profile_digest
        )
        self.prerequisite_report_document = flow.FileDocument(
            self.prerequisite_report, prerequisite_report_digest
        )
        self.profile["spec"]["prerequisites"].update(
            profileDigest=prerequisite_profile_digest,
            reportDigest=prerequisite_report_digest,
        )
        self.bedrock_profile = _example(
            "customer-bedrock-qualification-profile.json"
        )
        self.profile["spec"]["bedrock"]["profileDigest"] = flow._digest(
            self.bedrock_profile
        )
        self.runtime = self._runtime()

    def _runtime(self) -> flow.RuntimeDocuments:
        live_report = _example(
            "bedrock-converse-stream-instrumentation-compatibility-report.json"
        )
        live_report["metadata"].update(
            generatedAt="2026-09-08T10:11:04Z",
            sourceRevision=REVISION,
            sourceDirty=False,
        )
        live_report["spec"]["qualificationLevel"] = "live-provider-interoperability"
        live_report["spec"]["profile"].update(
            modelId=self.bedrock_profile["spec"]["target"]["modelId"],
            region=self.bedrock_profile["spec"]["target"]["region"],
            invocationTarget="aws-bedrock",
        )
        live_report["spec"]["result"].update(
            liveProviderVerified=True,
            providerCallLatencyMilliseconds=5000,
        )
        live_report["metadata"]["id"] = flow.bedrock._expected_compatibility_id(
            live_report
        )

        observation = _example("ai-economics-invocation-observation.json")
        observation["metadata"].update(
            tenantId=self.prerequisite_profile["spec"]["pricing"]["tenantId"],
            generatedAt="2026-09-08T10:11:05Z",
        )
        correlation_digest = flow._digest(
            {
                "tenantId": observation["metadata"]["tenantId"],
                "traceId": TRACE_ID,
                "spanId": SPAN_ID,
            }
        )
        observation["spec"]["correlationDigest"] = correlation_digest
        pricing = observation["spec"]["sources"]["pricing"]
        selected_pricing = self.prerequisite_profile["spec"]["pricing"]
        pricing.update(
            id=selected_pricing["catalogId"],
            version=selected_pricing["catalogVersion"],
            documentDigest=selected_pricing["catalogDocumentDigest"],
        )
        attribution_source_digest = observation["spec"]["sources"]["attribution"][
            "documentDigest"
        ]
        pricing_source_digest = pricing["documentDigest"]
        usage = observation["spec"]["usage"]
        attribution = observation["spec"]["attribution"]
        cost = observation["spec"]["cost"]
        priced = cost["pricedCost"]
        run_evidence = {
            "apiVersion": flow.API_VERSION,
            "kind": flow.RUN_EVIDENCE_KIND,
            "metadata": {
                "generatedAt": "2026-09-08T10:11:05Z",
                "sourceRevision": REVISION,
                "sourceDirty": False,
            },
            "spec": {
                "traceId": TRACE_ID,
                "spanId": SPAN_ID,
                "correlationDigest": correlation_digest,
                "deliveryEndpointDigest": flow._raw_digest(
                    self.profile["spec"]["targets"]["otlpTracesEndpoint"]
                ),
                "liveCompatibilityReportDigest": flow._digest(live_report),
                "invocationObservationDigest": flow._digest(observation),
                "usage": {
                    "recordId": usage["recordId"],
                    "recordDigest": usage["recordDigest"],
                },
                "attribution": {
                    "recordId": attribution["recordId"],
                    "recordDigest": attribution["recordDigest"],
                    "sourceDocumentDigest": attribution_source_digest,
                    "applicationId": attribution["applicationId"],
                    "teamId": attribution["teamId"],
                },
                "cost": {
                    "recordId": cost["recordId"],
                    "recordDigest": cost["recordDigest"],
                    "sourceDocumentDigest": pricing_source_digest,
                    "currency": priced["currency"],
                    "currencyScale": priced["currencyScale"],
                    "totalSubunits": priced["totalSubunits"],
                    "costBasis": priced["costBasis"],
                },
                "telemetry": {
                    "queryDigest": "sha256:" + "8" * 64,
                    "requestCountBefore": 41,
                    "requestCountAfter": 42,
                    "dashboardDocumentDigest": "sha256:" + "9" * 64,
                    "dashboardPanelCount": 9,
                },
                "timing": {
                    "startedAt": "2026-09-08T10:11:00Z",
                    "completedAt": "2026-09-08T10:11:05Z",
                    "endToEndLatencyMilliseconds": 5000,
                    "observationPolls": 2,
                },
                "privacy": {
                    "contentCaptured": False,
                    "rawPayloadPersisted": False,
                    "transportable": False,
                },
            },
        }
        return flow.RuntimeDocuments(run_evidence, live_report, observation)

    def _build(self) -> dict[str, object]:
        return dict(
            flow.build_report(
                flow_profile=self.profile,
                prerequisite_profile=self.prerequisite_profile_document,
                prerequisite_report=self.prerequisite_report_document,
                bedrock_profile=self.bedrock_profile,
                runtime=self.runtime,
                protected_inputs=True,
            )
        )

    def test_builds_minimized_source_bound_report(self) -> None:
        report = self._build()
        flow.validate_report_document(report)
        self.assertEqual("qualified", report["spec"]["status"])
        self.assertEqual(15, report["spec"]["summary"]["passedChecks"])
        bindings = report["spec"]["bindings"]
        self.assertEqual(
            self.runtime.run_evidence["spec"]["attribution"][
                "sourceDocumentDigest"
            ],
            bindings["activeAttributionPolicyDocumentDigest"],
        )
        self.assertEqual(
            self.prerequisite_profile["spec"]["pricing"][
                "catalogDocumentDigest"
            ],
            bindings["activePriceCatalogDocumentDigest"],
        )
        serialized = json.dumps(report, sort_keys=True)
        for sensitive in (
            TRACE_ID,
            SPAN_ID,
            self.profile["metadata"]["environmentId"],
            self.profile["spec"]["attribution"]["applicationId"],
            self.profile["spec"]["attribution"]["teamId"],
            self.profile["spec"]["targets"]["controlPlaneBaseUrl"],
            self.bedrock_profile["spec"]["target"]["modelId"],
        ):
            self.assertNotIn(sensitive, serialized)

    def test_qualify_requires_explicit_call_and_clean_source(self) -> None:
        with self.assertRaisesRegex(
            flow.CustomerAiFinopsFlowError,
            "customer-ai-finops-flow.enable.required",
        ):
            flow.qualify(
                flow_profile=self.profile,
                prerequisite_profile=self.prerequisite_profile_document,
                prerequisite_report=self.prerequisite_report_document,
                bedrock_profile=self.bedrock_profile,
                allow_provider_call=False,
                runner=lambda *_: self.runtime,
                now=NOW,
            )
        with patch.object(flow, "_source_identity", return_value=(REVISION, False)):
            report, runtime = flow.qualify(
                flow_profile=self.profile,
                prerequisite_profile=self.prerequisite_profile_document,
                prerequisite_report=self.prerequisite_report_document,
                bedrock_profile=self.bedrock_profile,
                allow_provider_call=True,
                runner=lambda *_: self.runtime,
                now=NOW,
            )
        self.assertEqual("qualified", report["spec"]["status"])
        self.assertIs(runtime, self.runtime)

    def test_rejects_cross_tenant_or_wrong_active_catalog(self) -> None:
        crossed = copy.deepcopy(self.runtime)
        crossed.observation["metadata"]["tenantId"] = "tenant-other"
        crossed.run_evidence["spec"]["invocationObservationDigest"] = flow._digest(
            crossed.observation
        )
        with self.assertRaisesRegex(
            flow.CustomerAiFinopsFlowError,
            "customer-ai-finops-flow.run-evidence.invalid",
        ):
            flow.build_report(
                flow_profile=self.profile,
                prerequisite_profile=self.prerequisite_profile_document,
                prerequisite_report=self.prerequisite_report_document,
                bedrock_profile=self.bedrock_profile,
                runtime=crossed,
                protected_inputs=True,
            )
        wrong_catalog = copy.deepcopy(self.runtime)
        wrong_catalog.observation["spec"]["sources"]["pricing"][
            "documentDigest"
        ] = "sha256:" + "f" * 64
        wrong_catalog.run_evidence["spec"]["cost"][
            "sourceDocumentDigest"
        ] = "sha256:" + "f" * 64
        wrong_catalog.run_evidence["spec"]["invocationObservationDigest"] = (
            flow._digest(wrong_catalog.observation)
        )
        with self.assertRaisesRegex(
            flow.CustomerAiFinopsFlowError,
            "customer-ai-finops-flow.run-evidence.invalid",
        ):
            flow.build_report(
                flow_profile=self.profile,
                prerequisite_profile=self.prerequisite_profile_document,
                prerequisite_report=self.prerequisite_report_document,
                bedrock_profile=self.bedrock_profile,
                runtime=wrong_catalog,
                protected_inputs=True,
            )

    def test_rejects_crossed_prerequisite_profile(self) -> None:
        crossed = copy.deepcopy(self.prerequisite_report)
        crossed["spec"]["bindings"]["profileDigest"] = "sha256:" + "f" * 64
        document = flow.FileDocument(crossed, flow._digest(crossed))
        self.profile["spec"]["prerequisites"]["reportDigest"] = document.file_digest
        with self.assertRaisesRegex(
            flow.CustomerAiFinopsFlowError,
            "customer-ai-finops-flow.prerequisite.invalid",
        ):
            flow.build_report(
                flow_profile=self.profile,
                prerequisite_profile=self.prerequisite_profile_document,
                prerequisite_report=document,
                bedrock_profile=self.bedrock_profile,
                runtime=self.runtime,
                protected_inputs=True,
            )

    def test_verify_rebinds_evidence_and_current_freshness(self) -> None:
        report = self._build()
        with patch.object(flow, "_source_identity", return_value=(REVISION, False)):
            verified = flow.verify(
                report=report,
                flow_profile=self.profile,
                prerequisite_profile=self.prerequisite_profile_document,
                prerequisite_report=self.prerequisite_report_document,
                bedrock_profile=self.bedrock_profile,
                runtime=self.runtime,
                require_qualified=True,
                now=NOW,
            )
        self.assertEqual(report, verified)
        tampered = copy.deepcopy(report)
        tampered["spec"]["measurements"]["observationPolls"] = 3
        with patch.object(flow, "_source_identity", return_value=(REVISION, False)):
            with self.assertRaises(flow.CustomerAiFinopsFlowError):
                flow.verify(
                    report=tampered,
                    flow_profile=self.profile,
                    prerequisite_profile=self.prerequisite_profile_document,
                    prerequisite_report=self.prerequisite_report_document,
                    bedrock_profile=self.bedrock_profile,
                    runtime=self.runtime,
                    require_qualified=True,
                    now=NOW,
                )
        with patch.object(flow, "_source_identity", return_value=(REVISION, False)):
            with self.assertRaisesRegex(
                flow.CustomerAiFinopsFlowError,
                "customer-ai-finops-flow.prerequisite.invalid",
            ):
                flow.verify(
                    report=report,
                    flow_profile=self.profile,
                    prerequisite_profile=self.prerequisite_profile_document,
                    prerequisite_report=self.prerequisite_report_document,
                    bedrock_profile=self.bedrock_profile,
                    runtime=self.runtime,
                    require_qualified=True,
                    now=datetime(2026, 9, 10, tzinfo=timezone.utc),
                )

    def test_profile_requires_closed_https_targets(self) -> None:
        for target in self.profile["spec"]["targets"]:
            invalid = copy.deepcopy(self.profile)
            invalid["spec"]["targets"][target] = "http://example.test"
            with self.assertRaisesRegex(
                flow.CustomerAiFinopsFlowError,
                "customer-ai-finops-flow.profile.endpoint-invalid",
            ):
                flow.validate_profile(invalid)
        invalid = copy.deepcopy(self.profile)
        invalid["spec"]["targets"]["controlPlaneBaseUrl"] = (
            "https://example.test/base/../admin"
        )
        with self.assertRaises(flow.CustomerAiFinopsFlowError):
            flow.validate_profile(invalid)
        valid = copy.deepcopy(self.profile)
        valid["spec"]["targets"]["controlPlaneBaseUrl"] = (
            "https://example.test/iip"
        )
        flow.validate_profile(valid)

    def test_protected_headers_and_distinct_paths_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "headers.json"
            path.write_text('{"Authorization":"Bearer example"}\n')
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(
                flow.CustomerAiFinopsFlowError,
                "customer-ai-finops-flow.headers.invalid",
            ):
                flow._headers(path)
            os.chmod(path, 0o600)
            self.assertEqual(
                {"Authorization": "Bearer example"}, flow._headers(path)
            )
            with self.assertRaisesRegex(
                flow.CustomerAiFinopsFlowError,
                "customer-ai-finops-flow.output-paths.invalid",
            ):
                flow._require_distinct_paths((path, path))
            output = Path(temporary) / "run-evidence.json"
            flow._write(output, self.runtime.run_evidence, mode=0o600)
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            with self.assertRaisesRegex(
                flow.CustomerAiFinopsFlowError,
                "customer-ai-finops-flow.output-paths.invalid",
            ):
                flow._require_disjoint_outputs((path,), (path,))

    def test_runtime_composes_provider_observation_and_dashboard_boundaries(
        self,
    ) -> None:
        live_report = copy.deepcopy(self.runtime.live_report)
        observation = copy.deepcopy(self.runtime.observation)
        correlation = _example("bedrock-invocation-correlation.json")
        correlation["metadata"].update(
            sourceRevision=REVISION,
            sourceDirty=False,
        )
        correlation["spec"].update(
            traceId=TRACE_ID,
            spanId=SPAN_ID,
            deliveryEndpointDigest=flow._raw_digest(
                self.profile["spec"]["targets"]["otlpTracesEndpoint"]
            ),
            contentCaptured=False,
        )

        class CompletedProviderCall:
            def __init__(inner_self, command, *, cwd, env, **kwargs) -> None:
                self.assertEqual(
                    [str(ROOT / "scripts" / "test_bedrock_instrumentation.sh")],
                    command,
                )
                self.assertEqual(ROOT, cwd)
                self.assertNotIn("AWS_ACCESS_KEY_ID", env)
                self.assertNotIn("IIP_BEDROCK_OTLP_ALLOW_INSECURE", env)
                self.assertEqual("us-east-1", env["AWS_REGION"])
                output = Path(env["IIP_BEDROCK_OUTPUT_DIR"])
                live_path = output / env["IIP_BEDROCK_REPORT_BASENAME"]
                correlation_path = output / env["IIP_BEDROCK_CORRELATION_BASENAME"]
                live_path.write_text(json.dumps(live_report))
                correlation_path.write_text(json.dumps(correlation))
                os.chmod(correlation_path, 0o600)

            @staticmethod
            def wait(timeout=None) -> int:
                return 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            credentials = root / "aws-credentials"
            credentials.write_text("protected")
            os.chmod(credentials, 0o600)
            header_paths = []
            for name in ("control", "otlp", "prometheus", "grafana"):
                path = root / f"{name}-headers.json"
                path.write_text('{"Authorization":"Bearer protected"}\n')
                os.chmod(path, 0o600)
                header_paths.append(path)
            with (
                patch.dict(
                    os.environ,
                    {
                        "AWS_ACCESS_KEY_ID": "ambient",
                        "IIP_BEDROCK_OTLP_ALLOW_INSECURE": "true",
                    },
                ),
                patch.object(
                    flow.bedrock,
                    "validate_credentials_file",
                    return_value=credentials,
                ),
                patch.object(
                    flow,
                    "_prometheus_count",
                    side_effect=((41, "query"), (42, "query")),
                ),
                patch.object(
                    flow,
                    "_dashboard",
                    return_value=({"uid": "iip-ai-finops", "panels": []}, 9),
                ),
                patch.object(flow, "_http_json", return_value=observation),
                patch.object(flow.subprocess, "Popen", CompletedProviderCall),
            ):
                runtime = flow._execute_runtime(
                    self.profile,
                    self.bedrock_profile,
                    aws_credentials=credentials,
                    control_headers_path=header_paths[0],
                    otlp_headers_path=header_paths[1],
                    prometheus_headers_path=header_paths[2],
                    grafana_headers_path=header_paths[3],
                    control_ca=None,
                    otlp_ca=None,
                    prometheus_ca=None,
                    grafana_ca=None,
                    otlp_client_cert=None,
                    otlp_client_key=None,
                    docker_bin="docker",
                )
        self.assertEqual(observation, runtime.observation)
        self.assertEqual(
            42,
            runtime.run_evidence["spec"]["telemetry"]["requestCountAfter"],
        )
        self.assertEqual(
            observation["spec"]["sources"]["pricing"]["documentDigest"],
            runtime.run_evidence["spec"]["cost"]["sourceDocumentDigest"],
        )

    def test_sdk_exposes_only_profile_and_transportable_report(self) -> None:
        report = self._build()
        typed_profile = CustomerAiFinopsFlowQualificationProfile.from_dict(
            self.profile
        )
        typed_report = CustomerAiFinopsFlowQualificationReport.from_dict(report)
        self.assertEqual(self.profile, typed_profile.to_dict())
        self.assertEqual("qualified", typed_report.status)
        self.assertEqual(report, typed_report.to_dict())

    def test_rejects_naive_verification_time(self) -> None:
        report = self._build()
        with patch.object(flow, "_source_identity", return_value=(REVISION, False)):
            with self.assertRaisesRegex(
                flow.CustomerAiFinopsFlowError,
                "customer-ai-finops-flow.time.invalid",
            ):
                flow.verify(
                    report=report,
                    flow_profile=self.profile,
                    prerequisite_profile=self.prerequisite_profile_document,
                    prerequisite_report=self.prerequisite_report_document,
                    bedrock_profile=self.bedrock_profile,
                    runtime=self.runtime,
                    require_qualified=True,
                    now=datetime(2026, 9, 8, 10, 12),
                )


if __name__ == "__main__":
    unittest.main()
