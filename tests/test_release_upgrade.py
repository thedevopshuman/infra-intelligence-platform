"""Static safety contract for the packaged cross-version upgrade gate."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseUpgradeGateTests(unittest.TestCase):
    def test_gate_is_kind_only_source_bound_and_data_preserving(self) -> None:
        script_path = ROOT / "scripts" / "test_release_upgrade.sh"
        script = script_path.read_text(encoding="utf-8")

        self.assertTrue(script_path.stat().st_mode & 0o111)
        self.assertIn('case "$IIP_KUBE_CONTEXT"', script)
        self.assertIn("Refusing release upgrade test outside", script)
        self.assertIn('scripts/release_bundle.py verify "$IIP_RELEASE_BUNDLE"', script)
        self.assertIn("git merge-base --is-ancestor", script)
        self.assertIn("Release bundle revision does not match", script)
        self.assertIn("must not precede the source migration", script)
        self.assertNotIn("requires a target with a newer schema migration", script)
        self.assertIn('git archive "$IIP_BASE_REVISION"', script)
        self.assertIn('IIP_IMAGE_REVISION=$IIP_BASE_REVISION', script)
        self.assertIn('"$IIP_DOCKER_BIN" load --input', script)
        self.assertIn("ctr -n k8s.io images tag --force", script)
        self.assertIn('"$IIP_HELM_BIN" show values "$chart"', script)
        self.assertIn(
            "--set database.transportSecurity.mode=insecure-local",
            script,
        )
        self.assertEqual(script.count('install_revision "$IIP_TARGET_CHART"'), 2)
        self.assertIn('"$IIP_HELM_BIN" rollback iip 1', script)
        self.assertGreaterEqual(script.count("assert_seed_resource"), 4)
        self.assertIn("SELECT count(*) FROM iip.schema_migrations", script)
        self.assertIn("assert len(rows) == 4", script)
        self.assertIn("application rollback -> idempotent re-upgrade", script)

    def test_gate_sends_sustained_authenticated_data_reads(self) -> None:
        script = (ROOT / "scripts" / "test_release_upgrade.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("automountServiceAccountToken: false", script)
        self.assertIn("readOnlyRootFilesystem: true", script)
        self.assertIn('Path("/var/run/iip-probe/bearer-token")', script)
        self.assertIn('fetch("/v1/system/version")', script)
        self.assertIn('fetch("/v1/resources")', script)
        self.assertIn('"http://iip-infra-intelligence" + path', script)
        self.assertNotIn('"http://iip-infra-intelligence:8080" + path', script)
        self.assertIn("unexpected-release-identity", script)
        self.assertIn("tenant-resource-unavailable", script)
        self.assertIn('state["failureCount"] == 0', script)
        self.assertIn('state["requestCount"] >= 40', script)
        self.assertIn("stop_and_assert_availability_probe", script)

    def test_gate_proves_a_terminating_api_finishes_an_active_read(self) -> None:
        script = (ROOT / "scripts" / "test_release_upgrade.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("LOCK TABLE iip.resource_projections", script)
        self.assertIn("pg_sleep(30)", script)
        self.assertIn("wait_event_type = 'Lock'", script)
        self.assertIn("kind: Job", script)
        self.assertIn("name: iip-drain-client", script)
        self.assertIn("automountServiceAccountToken: false", script)
        self.assertIn("secretName: iip-upgrade-probe", script)
        self.assertIn('delete pod "$IIP_DRAIN_POD"', script)
        self.assertIn("--wait=false", script)
        self.assertIn("drained-request-complete", script)
        self.assertIn("In-flight API request was terminated", script)
        self.assertLess(
            script.index("stop_and_assert_availability_probe\n"),
            script.rindex("prove_in_flight_request_drain\n"),
        )


if __name__ == "__main__":
    unittest.main()
