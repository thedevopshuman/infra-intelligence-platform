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
        self.assertIn('git archive "$IIP_BASE_REVISION"', script)
        self.assertIn('IIP_IMAGE_REVISION=$IIP_BASE_REVISION', script)
        self.assertIn('"$IIP_DOCKER_BIN" load --input', script)
        self.assertIn("ctr -n k8s.io images tag --force", script)
        self.assertEqual(script.count('install_revision "$IIP_TARGET_CHART"'), 2)
        self.assertIn('"$IIP_HELM_BIN" rollback iip 1', script)
        self.assertGreaterEqual(script.count("assert_seed_resource"), 4)
        self.assertIn("SELECT count(*) FROM iip.schema_migrations", script)
        self.assertIn("assert len(rows) == 4", script)
        self.assertIn("application rollback -> idempotent re-upgrade", script)


if __name__ == "__main__":
    unittest.main()
