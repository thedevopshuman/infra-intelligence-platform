"""Community AI economics dashboard projection tests."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_dashboard  # noqa: E402


class CommunityDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name) / "community"
        self.state.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _panels(document: dict) -> dict[int, dict]:
        return {panel["id"]: panel for panel in document["panels"]}

    @staticmethod
    def _expressions(panel: dict) -> tuple[str, ...]:
        return tuple(target["expr"] for target in panel.get("targets", []))

    def _render(self, currency: str = "USD", scale: int = 9) -> dict:
        path = community_dashboard.write_dashboard(self.state, currency, scale)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_checked_in_template_is_valid_json_with_stable_identity(self) -> None:
        template = json.loads(
            community_dashboard.TEMPLATE.read_text(encoding="utf-8")
        )

        self.assertEqual(template["uid"], "iip-community-ai-finops")
        self.assertEqual(template["time"], {"from": "now-24h", "to": "now"})
        self.assertEqual(len(template["panels"]), 12)
        self.assertIn(
            "__IIP_COMMUNITY_ROLLING_COST_TOTAL__", json.dumps(template)
        )

    def test_primary_panels_use_the_rolling_allocation_projection(self) -> None:
        document = self._render()
        panels = self._panels(document)
        primary = tuple(
            expression
            for panel_id in range(1, 10)
            for expression in self._expressions(panels[panel_id])
        )

        self.assertTrue(primary)
        for expression in primary:
            self.assertIn("iip_ai_allocation_", expression)
            self.assertIn('job="iip-ai-economics"', expression)
            for fixed_window_metric in (
                "iip_ai_usage_requests",
                "iip_ai_cost_amount",
                "iip_ai_context_growth_change",
                "iip_ai_savings_potential_amount",
            ):
                self.assertNotIn(fixed_window_metric, expression)

        for panel_id in range(1, 8):
            expression = self._expressions(panels[panel_id])[0]
            self.assertIn('iip_ai_allocation_dimension="application"', expression)
            self.assertNotIn('iip_ai_allocation_dimension="team"', expression)
        self.assertIn(
            'iip_ai_allocation_dimension="application"',
            self._expressions(panels[8])[0],
        )
        self.assertIn(
            'iip_ai_allocation_dimension="team"',
            self._expressions(panels[9])[0],
        )

    def test_totals_include_every_allocation_and_cost_status_once(self) -> None:
        panels = self._panels(self._render())
        request_total = self._expressions(panels[1])[0]
        cost_total = self._expressions(panels[4])[0]
        price_coverage = self._expressions(panels[5])[0]
        ownership_coverage = self._expressions(panels[7])[0]
        application_cost = self._expressions(panels[8])[0]
        team_cost = self._expressions(panels[9])[0]

        self.assertNotIn("iip_ai_allocation_status=", request_total)
        self.assertNotIn("iip_ai_allocation_status=", cost_total)
        self.assertIn("sum by (iip_ai_cost_status)", price_coverage)
        self.assertIn("sum by (iip_ai_allocation_status)", ownership_coverage)
        self.assertIn(
            "sum by (iip_ai_application_id,iip_ai_allocation_status)",
            application_cost,
        )
        self.assertIn(
            "sum by (iip_ai_team_id,iip_ai_allocation_status)", team_cost
        )
        self.assertNotIn("iip_ai_allocation_status=", application_cost)
        self.assertNotIn("iip_ai_allocation_status=", team_cost)

    def test_catalog_currency_and_each_supported_scale_are_rendered_exactly(self) -> None:
        for index, (currency, scale) in enumerate(
            (("EUR", 6), ("USD", 9), ("JPY", 12))
        ):
            state = Path(self.temporary.name) / f"community-{index}"
            state.mkdir(mode=0o700)
            path = community_dashboard.write_dashboard(state, currency, scale)
            payload = path.read_text(encoding="utf-8")
            document = json.loads(payload)
            panels = self._panels(document)

            self.assertNotIn("__IIP_", payload)
            self.assertIn(
                f"Currency: {currency}; scale: {scale}.", document["description"]
            )
            for panel_id in (4, 8, 9, 12):
                expression = self._expressions(panels[panel_id])[0]
                self.assertIn(f'iip_ai_currency="{currency}"', expression)
                self.assertIn(f'iip_ai_currency_scale="{scale}"', expression)
                self.assertIn(
                    'iip_ai_cost_basis="calculated-estimate"', expression
                )
                self.assertIn(f" / {10**scale}", expression)
                self.assertEqual(
                    panels[panel_id]["fieldConfig"]["defaults"]["unit"],
                    f"currency{currency}",
                )

    def test_optional_change_and_saving_are_explicit_fixed_window_series(
        self,
    ) -> None:
        panels = self._panels(self._render())
        change = self._expressions(panels[11])[0]
        saving = self._expressions(panels[12])[0]
        explanation = panels[10]["options"]["content"]

        self.assertIn("iip_ai_context_growth_change", change)
        self.assertIn("iip_ai_savings_potential_amount", saving)
        self.assertNotIn("sum(", saving)
        self.assertNotIn("vector(0)", change + saving)
        self.assertIn("No data", explanation)
        self.assertIn("not mean zero", explanation)
        self.assertIn("fixed window", panels[11]["title"])
        self.assertIn("fixed window", panels[12]["title"])

    def test_generated_dashboard_is_owner_only_and_never_overwritten(self) -> None:
        path = community_dashboard.write_dashboard(self.state, "USD", 9)
        original = path.read_bytes()

        self.assertEqual(path, self.state / "dashboard.json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with self.assertRaisesRegex(
            community_dashboard.CommunityDashboardError,
            "community.dashboard.exists",
        ):
            community_dashboard.write_dashboard(self.state, "USD", 9)
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_catalog_values_and_unprotected_state_fail_closed(self) -> None:
        for currency, scale in (
            ("usd", 9),
            ("USDD", 9),
            ("U$D", 9),
            ("USD", 0),
            ("USD", 9.0),
            ("USD", True),
        ):
            with self.subTest(currency=currency, scale=scale):
                with self.assertRaisesRegex(
                    community_dashboard.CommunityDashboardError,
                    "community.dashboard.configuration.invalid",
                ):
                    community_dashboard.write_dashboard(
                        self.state, currency, scale  # type: ignore[arg-type]
                    )

        os.chmod(self.state, 0o755)
        with self.assertRaisesRegex(
            community_dashboard.CommunityDashboardError,
            "community.dashboard.state.invalid",
        ):
            community_dashboard.write_dashboard(self.state, "USD", 9)

    def test_state_symlink_is_rejected(self) -> None:
        target = Path(self.temporary.name) / "target"
        target.mkdir(mode=0o700)
        link = Path(self.temporary.name) / "state-link"
        link.symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(
            community_dashboard.CommunityDashboardError,
            "community.dashboard.state.invalid",
        ):
            community_dashboard.write_dashboard(link, "USD", 9)


if __name__ == "__main__":
    unittest.main()
