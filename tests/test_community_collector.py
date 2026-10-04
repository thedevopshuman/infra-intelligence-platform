"""Protected Collector admission is generated without telemetry-derived trust."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.community_collector import (
    MARKER, SCOPE, admission_filters, render_collector, write_collector,
)


def channel() -> dict:
    return {"channels": [{
        "provider": "aws.bedrock", "instrumentationScopes": [SCOPE],
        "models": ["reviewed-model-a", "reviewed-model-b"],
        "operations": ["chat"], "regions": ["us-east-1"],
        "services": [{"otlpName": "reviewed-service", "serviceNamespace": "reviewed-namespace", "deploymentEnvironment": "production"}],
    }]}


class CommunityCollectorTests(unittest.TestCase):
    def test_exact_protected_identity_admission_precedes_queue(self) -> None:
        rendered = render_collector(channel())
        self.assertNotIn(MARKER, rendered)
        conditions = admission_filters(channel())
        self.assertIn('resource.attributes["service.name"] == "reviewed-service"', conditions[0])
        self.assertIn('resource.attributes["service.namespace"] == "reviewed-namespace"', conditions[0])
        self.assertIn('resource.attributes["deployment.environment.name"] == "production"', conditions[0])
        self.assertIn('attributes["gen_ai.provider.name"] != nil and (attributes["gen_ai.provider.name"] != "aws.bedrock")', conditions)
        self.assertIn('attributes["gen_ai.request.model"] == nil or (attributes["gen_ai.request.model"] != "reviewed-model-a" and attributes["gen_ai.request.model"] != "reviewed-model-b")', conditions)
        self.assertIn('attributes["cloud.region"] != nil and resource.attributes["cloud.region"] != nil and attributes["cloud.region"] != resource.attributes["cloud.region"]', conditions)
        self.assertIn('attributes["aws.retry_count"] != nil and (attributes["aws.retry_count"] < 0 or attributes["aws.retry_count"] > 100)', conditions)
        self.assertIn('SHA256(attributes["aws.request_id"])', rendered)
        kept_span_attributes = next(line for line in rendered.splitlines() if "keep_keys(attributes," in line)
        self.assertNotIn("error.type", kept_span_attributes)
        self.assertIn("groupbyattrs/metadata_resource:\n    keys: []", rendered)
        self.assertIn("transform/metadata_only, transform/scope_cleanup, groupbyattrs/metadata_resource, batch", rendered)
        self.assertIn('set(attributes["cloud.region"], instrumentation_scope.attributes["cloud.region"])', rendered)
        self.assertIn('delete_matching_keys(attributes, ".*")', rendered)

    def test_absent_optional_identity_does_not_admit_unreviewed_values(self) -> None:
        document = channel()
        document["channels"][0]["services"] = [{"otlpName": "reviewed-service"}]
        service_condition = admission_filters(document)[0]
        self.assertIn('resource.attributes["service.namespace"] == nil', service_condition)
        self.assertIn('resource.attributes["deployment.environment.name"] == nil', service_condition)
        self.assertNotIn(" or ", service_condition)

    def test_quotes_backslashes_and_ottl_like_values_are_data(self) -> None:
        document = channel()
        suspicious = 'reviewed\\service" or true or name == "other'
        document["channels"][0]["services"][0]["otlpName"] = suspicious
        conditions = admission_filters(document)
        self.assertIn(json.dumps(suspicious), conditions[0])
        rendered = render_collector(document)
        # JSON-quoted conditions are also valid YAML scalar strings. Decode the
        # exact generated line and ensure no interpolation/code escaped it.
        generated_line = next(line.strip()[2:] for line in rendered.splitlines() if line.startswith('        - "not ('))
        self.assertEqual(json.loads(generated_line), conditions[0])

    def test_environment_interpolation_and_control_values_are_rejected(self) -> None:
        for value in ("${env:SECRET}", "unexpected\nnewline", "", "a" * 257):
            with self.subTest(value=value):
                document = channel()
                document["channels"][0]["models"] = [value]
                with self.assertRaisesRegex(ValueError, "community.collector.configuration.invalid"):
                    render_collector(document)

    def test_invalid_scope_multiple_channels_and_empty_allowlists_fail_closed(self) -> None:
        for mutation in (
            lambda d: d["channels"].append(copy.deepcopy(d["channels"][0])),
            lambda d: d["channels"][0].update(instrumentationScopes=["unreviewed"]),
            lambda d: d["channels"][0].update(models=[]),
            lambda d: d["channels"][0].update(services=[]),
            lambda d: d["channels"][0].update(regions=["us-east-1", "us-east-1"]),
        ):
            document = channel()
            mutation(document)
            with self.assertRaisesRegex(ValueError, "community.collector.configuration.invalid"):
                render_collector(document)

    def test_output_is_deterministic_private_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = write_collector(directory, channel())
            self.assertEqual(path.read_text(), render_collector(channel()))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                write_collector(directory, channel())
            public = directory / "public"
            public.mkdir(mode=0o755)
            os.chmod(public, 0o755)
            with self.assertRaisesRegex(ValueError, "community.collector.directory.invalid"):
                write_collector(public, channel())


if __name__ == "__main__":
    unittest.main()
