from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from iip.adapters.otlp_ai_usage_receiver import ConfiguredAiUsageReceiver
from iip.application.attribute_ai_usage import validate_ai_attribution_policy
from iip.application.calculate_ai_cost import validate_ai_price_catalog
from iip.application.evaluate_ai_savings import validate_ai_savings_profile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ai_finops_fixture  # noqa: E402
import ai_finops_stack  # noqa: E402


class AiFinOpsTopologyTests(unittest.TestCase):
    def test_time_relative_fixture_uses_valid_protected_configuration(self) -> None:
        anchor = datetime(2026, 9, 5, 7, 30, tzinfo=timezone.utc)
        channel = ai_finops_fixture.channel_configuration()
        receiver = ConfiguredAiUsageReceiver.from_json(json.dumps(channel))

        authenticated = receiver.authenticate_bearer(
            ai_finops_fixture.CHANNEL_TOKEN
        )
        self.assertEqual(authenticated.actor.tenant_id, "local")
        self.assertEqual(
            authenticated.model_ids,
            (
                ai_finops_fixture.CANDIDATE_MODEL,
                ai_finops_fixture.KNOWN_MODEL,
                ai_finops_fixture.UNKNOWN_MODEL,
            ),
        )
        openai = receiver.authenticate_bearer(
            ai_finops_fixture.OPENAI_CHANNEL_TOKEN
        )
        self.assertEqual(openai.actor.tenant_id, "local")
        self.assertEqual(openai.provider, "openai")
        self.assertEqual(openai.model_ids, (ai_finops_fixture.OPENAI_MODEL,))

        policies = ai_finops_fixture.attribution_policy_configuration(anchor)
        attribution = validate_ai_attribution_policy(policies["policies"][0])
        self.assertEqual(attribution.tenant_id, "local")
        self.assertEqual(len(attribution.rules), 2)
        self.assertEqual(
            attribution.rules[0].deployment_environment,
            "ai-finops-demo",
        )
        self.assertEqual(
            {rule.service_name for rule in attribution.rules},
            {"support-assistant", "order-copilot"},
        )

        catalogs = ai_finops_fixture.price_catalog_configuration(anchor)
        catalog = validate_ai_price_catalog(catalogs["catalogs"][0])
        self.assertEqual(catalog.tenant_id, "local")
        self.assertEqual(
            {entry.provider for entry in catalog.entries},
            {"aws.bedrock", "openai"},
        )
        profiles = ai_finops_fixture.savings_profile_configuration(anchor)
        validated = tuple(
            validate_ai_savings_profile(item, allow_test_fixtures=True)
            for item in profiles["profiles"]
        )
        self.assertEqual(len(validated), 5)
        self.assertEqual(
            {item.profile_id for item in validated},
            {
                "support-assistant-context",
                "research-assistant-coverage",
                "order-copilot-coverage",
                "support-assistant-retries",
                "support-assistant-model-cost",
            },
        )

    def test_dashboard_answers_v0_questions_and_exposes_coverage(self) -> None:
        dashboard = json.loads(
            (
                ROOT
                / "deploy"
                / "grafana"
                / "dashboards"
                / "iip-ai-finops.json"
            ).read_text(encoding="utf-8")
        )
        panels = {panel["title"]: panel for panel in dashboard["panels"]}

        for title in (
            "How much usage?",
            "How much cost?",
            "Where is spend happening?",
            "What changed?",
            "One potential saving",
            "Can I trust the coverage?",
            "Are retries increasing?",
            "Is a qualified lower-cost model available?",
            "Cost by protected application",
            "Cost by protected team",
        ):
            self.assertIn(title, panels)
        expressions = tuple(
            target["expr"]
            for panel in panels.values()
            for target in panel["targets"]
        )
        for metric in (
            "iip_ai_usage_requests",
            "iip_ai_cost_amount",
            "iip_ai_context_growth_change",
            "iip_ai_savings_potential_amount",
            "iip_ai_retry_operation_rate_increase",
            "iip_ai_model_cost_increase",
            "iip_ai_cost_requests",
            "iip_ai_allocation_cost_amount",
        ):
            self.assertTrue(any(metric in expression for expression in expressions))
        self.assertTrue(
            any("gen_ai_provider_name" in expression for expression in expressions)
        )
        serialized = json.dumps(dashboard).lower()
        for prohibited in ("trace_id", "span_id", "request_id", "gen_ai.prompt"):
            self.assertNotIn(prohibited, serialized)

    def test_compose_keeps_engines_in_worker_and_receiver_is_isolated(self) -> None:
        compose = (
            ROOT / "deploy" / "docker-compose.ai-finops.yml"
        ).read_text(encoding="utf-8")
        api, remainder = compose.split("  ai-usage-receiver:", maxsplit=1)
        receiver, worker_and_backends = remainder.split(
            "  workflow-worker:", maxsplit=1
        )

        self.assertIn("IIP_AI_PRICE_CATALOGS_JSON", api)
        self.assertNotIn("IIP_AI_PRICE_CATALOGS_JSON", receiver)
        self.assertIn("IIP_AI_PRICE_CATALOGS_JSON", worker_and_backends)
        self.assertIn("IIP_AI_ATTRIBUTION_POLICIES_JSON", api)
        self.assertNotIn("IIP_AI_ATTRIBUTION_POLICIES_JSON", receiver)
        self.assertIn("IIP_AI_ATTRIBUTION_POLICIES_JSON", worker_and_backends)
        self.assertIn('IIP_AI_ALLOCATION_REPORTING_ENABLED: "true"', api)
        self.assertIn(
            'IIP_AI_ALLOCATION_REPORTING_ENABLED: "true"',
            worker_and_backends,
        )
        self.assertNotIn("IIP_AI_COST_ENGINE_ENABLED", api)
        self.assertNotIn("IIP_AI_ATTRIBUTION_ENABLED", api)
        self.assertIn("IIP_AI_USAGE_RECEIVER_ENABLED: \"true\"", receiver)
        self.assertIn('IIP_OTLP_RECEIVER_ENABLED: "false"', receiver)
        self.assertIn('IIP_OTLP_LOGS_RECEIVER_ENABLED: "false"', receiver)
        self.assertIn("127.0.0.1:", compose)
        self.assertIn('cap_drop: ["ALL"]', compose)
        self.assertIn("no-new-privileges:true", compose)

    def test_collector_uses_env_credential_and_separate_signal_routes(self) -> None:
        collector = (
            ROOT / "deploy" / "otel" / "ai-finops-collector.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("${env:IIP_AI_USAGE_CHANNEL_TOKEN}", collector)
        self.assertIn("${env:IIP_OPENAI_USAGE_CHANNEL_TOKEN}", collector)
        self.assertNotIn(ai_finops_fixture.CHANNEL_TOKEN, collector)
        self.assertNotIn(ai_finops_fixture.OPENAI_CHANNEL_TOKEN, collector)
        self.assertIn("exporters: [otlp_http/iip]", collector)
        self.assertIn("exporters: [otlp_http/iip_openai]", collector)
        self.assertIn("receivers: [otlp/openai]", collector)
        self.assertIn("exporters: [prometheus]", collector)
        self.assertIn("exporters: [otlp_http/loki]", collector)

    def test_visible_stack_configuration_is_protected_and_time_bound(self) -> None:
        anchor = datetime(2026, 9, 5, 7, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / ".iip"
            env_path = state / "ai-finops.env"
            with patch.multiple(
                ai_finops_stack,
                STATE_DIR=state,
                ENV_PATH=env_path,
            ):
                created = ai_finops_stack.create_configuration(anchor)

            self.assertEqual(created, env_path)
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
            values = dict(
                line.split("=", maxsplit=1)
                for line in env_path.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(
                values["IIP_AI_FINOPS_ANCHOR"],
                "2026-09-05T07:30:00Z",
            )
            self.assertEqual(
                values["IIP_AI_USAGE_CHANNEL_TOKEN"],
                ai_finops_fixture.CHANNEL_TOKEN,
            )
            self.assertEqual(
                values["IIP_OPENAI_USAGE_CHANNEL_TOKEN"],
                ai_finops_fixture.OPENAI_CHANNEL_TOKEN,
            )
            profiles = json.loads(values["IIP_AI_SAVINGS_PROFILES_JSON"])
            self.assertEqual(len(profiles["profiles"]), 5)
            policies = json.loads(values["IIP_AI_ATTRIBUTION_POLICIES_JSON"])
            self.assertEqual(len(policies["policies"]), 1)

    def test_visible_stack_compose_does_not_use_a_shell(self) -> None:
        with patch.object(ai_finops_stack, "ENV_PATH", Path("/tmp/iip.env")):
            with patch("ai_finops_stack.subprocess.run") as run:
                run.return_value.stdout = "container-id\n"
                output = ai_finops_stack.compose(
                    ("ps", "--quiet"),
                    capture_output=True,
                )

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["docker", "compose", "--project-name"])
        self.assertEqual(command[3], "iip-ai-finops")
        self.assertIn("--env-file", command)
        self.assertEqual(command[-2:], ["ps", "--quiet"])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(output, "container-id\n")

    def test_disposable_gate_does_not_take_over_visible_stack_ports(self) -> None:
        runner = (ROOT / "scripts" / "test_ai_finops.sh").read_text(
            encoding="utf-8"
        )

        for port in (
            "25435",
            "28082",
            "24320",
            "24319",
            "24321",
            "23134",
            "29091",
            "23101",
            "23000",
        ):
            self.assertIn(port, runner)
        self.assertIn("IIP_AI_FINOPS_TEST_COLLECTOR_PORT", runner)
        self.assertIn("IIP_AI_FINOPS_TEST_OPENAI_COLLECTOR_PORT", runner)

    def test_visible_stack_reports_the_console_and_fixture_credential(self) -> None:
        lifecycle = (ROOT / "scripts" / "ai_finops_stack.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("http://127.0.0.1:18082/console", lifecycle)
        self.assertIn("ai_finops_fixture.CONTROL_TOKEN", lifecycle)


if __name__ == "__main__":
    unittest.main()
