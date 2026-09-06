from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import qualify_local_release  # noqa: E402


REVISION = "a" * 40
PREVIOUS = "b" * 40


class LocalReleaseQualificationTests(unittest.TestCase):
    def test_closed_stages_cover_every_retained_readiness_input(self) -> None:
        targets = [target for _, stage in qualify_local_release.STAGES for target in stage]

        for required in (
            "verify",
            "test-capacity",
            "test-credential-broker",
            "test-oidc",
            "test-policy-engine",
            "test-external-secrets",
            "test-otlp-receiver",
            "test-bedrock-instrumentation",
            "test-openai-instrumentation",
            "test-ai-finops",
            "verify-ai-finops-runtime-report",
            "test-backup-restore",
            "test-postgres-continuity",
            "test-deployment-preflight",
            "qualify-github-context",
            "test-plugin-compatibility",
            "qualify-kubernetes-availability",
            "release-bundle",
            "qualify-release",
            "qualify-release-vulnerabilities",
            "assess-release-readiness",
            "verify-release-readiness-report",
        ):
            self.assertIn(required, targets)
        self.assertLess(targets.index("release-bundle"), targets.index("qualify-release"))
        self.assertLess(
            targets.index("qualify-release-vulnerabilities"),
            targets.index("assess-release-readiness"),
        )

    def test_paths_and_environment_are_exact_revision_bound(self) -> None:
        paths = qualify_local_release.qualification_paths(REVISION, "0.84.0")
        with patch.dict(
            qualify_local_release.os.environ,
            {
                "IIP_INGRESS_TOKEN_FILE": "/private/token",
                "AWS_SECRET_ACCESS_KEY": "private",
                "OPENAI_API_KEY": "private",
                "GITHUB_TOKEN": "private",
            },
        ):
            environment = qualify_local_release.qualification_environment(
                paths=paths,
                previous_revision=PREVIOUS,
                toolchain=qualify_local_release.Toolchain(python="safe-python"),
            )

        self.assertEqual(paths.bundle.name, "iip-0.84.0-aaaaaaaaaaaa")
        self.assertEqual(
            paths.readiness.name, "release-readiness-aaaaaaaaaaaa.json"
        )
        self.assertEqual(environment["IIP_UPGRADE_FROM_REVISION"], PREVIOUS)
        self.assertEqual(environment["IIP_RELEASE_BUNDLE"], str(paths.bundle))
        self.assertEqual(environment["PYTHON"], "safe-python")
        self.assertNotIn("IIP_INGRESS_TOKEN_FILE", environment)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("GITHUB_TOKEN", environment)

    def test_make_command_uses_argument_boundaries_without_a_shell(self) -> None:
        command = qualify_local_release.make_command(
            qualify_local_release.Toolchain(
                make="safe-make", python="safe python", docker="safe-docker"
            ),
            ("verify", "release-bundle"),
        )

        self.assertEqual(command[0], "safe-make")
        self.assertIn("PYTHON=safe python", command)
        self.assertEqual(command[-2:], ("verify", "release-bundle"))
        self.assertNotIn("sh", command)

    def test_dirty_source_and_invalid_ancestor_fail_before_work(self) -> None:
        calls = []

        def output(command: tuple[str, ...]) -> str:
            calls.append(command)
            if command[1:3] == ("rev-parse", "HEAD"):
                return REVISION
            if command[1:3] == ("status", "--porcelain"):
                return " M README.md"
            self.fail(command)

        with patch.object(qualify_local_release, "_output", side_effect=output):
            with self.assertRaisesRegex(
                qualify_local_release.LocalReleaseQualificationError,
                "local-release.source.dirty",
            ):
                qualify_local_release._repository_identity("b" * 7)
        self.assertEqual(len(calls), 2)

        with self.assertRaisesRegex(
            qualify_local_release.LocalReleaseQualificationError,
            "local-release.upgrade-revision.invalid",
        ):
            qualify_local_release._repository_identity("main; unsafe")

    def test_output_validation_rejects_incomplete_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = qualify_local_release.QualificationPaths(
                bundle=root / "bundle",
                release_qualification=root / "release.json",
                vulnerability_qualification=root / "vulnerability.json",
                readiness=root / "readiness.json",
                evidence_dir=root,
            )
            for path, kind, status in (
                (
                    paths.release_qualification,
                    "ReleaseQualificationReport",
                    "qualified",
                ),
                (
                    paths.vulnerability_qualification,
                    "ReleaseVulnerabilityQualificationReport",
                    "qualified",
                ),
                (paths.readiness, "ReleaseReadinessReport", "locally-qualified"),
            ):
                path.write_text(
                    json.dumps(
                        {
                            "kind": kind,
                            "metadata": {
                                "sourceRevision": REVISION,
                                "sourceDirty": False,
                            },
                            "spec": {"status": status, "summary": {}},
                        }
                    ),
                    encoding="utf-8",
                )
            with patch.object(
                qualify_local_release,
                "verify_bundle",
                return_value={
                    "metadata": {"revision": REVISION, "version": "0.84.0"}
                },
            ):
                with self.assertRaisesRegex(
                    qualify_local_release.LocalReleaseQualificationError,
                    "local-release.output.readiness-incomplete",
                ):
                    qualify_local_release.validate_outputs(
                        paths, revision=REVISION, version="0.84.0"
                    )

    def test_orchestrator_runs_closed_stages_and_stops_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = qualify_local_release.QualificationPaths(
                bundle=root / "bundle",
                release_qualification=root / "release.json",
                vulnerability_qualification=root / "vulnerability.json",
                readiness=root / "readiness.json",
                evidence_dir=root,
            )
            calls: list[tuple[str, ...]] = []

            def successful(
                command: tuple[str, ...], environment: dict[str, str], cwd: Path
            ) -> int:
                self.assertEqual(cwd, qualify_local_release.ROOT)
                self.assertEqual(environment["IIP_UPGRADE_FROM_REVISION"], PREVIOUS)
                calls.append(command)
                return 0

            with patch.object(
                qualify_local_release,
                "_repository_identity",
                return_value=(REVISION, PREVIOUS, "0.84.0"),
            ), patch.object(
                qualify_local_release, "qualification_paths", return_value=paths
            ), patch.object(
                qualify_local_release, "validate_outputs"
            ) as validate, patch.object(
                qualify_local_release, "_output", return_value=""
            ):
                result = qualify_local_release.qualify_local_release(
                    upgrade_from_revision=PREVIOUS,
                    toolchain=qualify_local_release.Toolchain(),
                    runner=successful,
                )

            self.assertEqual(result, paths)
            self.assertEqual(len(calls), len(qualify_local_release.STAGES))
            validate.assert_called_once_with(
                paths, revision=REVISION, version="0.84.0"
            )

            failed_calls = 0

            def failed(
                command: tuple[str, ...], environment: dict[str, str], cwd: Path
            ) -> int:
                nonlocal failed_calls
                failed_calls += 1
                return 9

            with patch.object(
                qualify_local_release,
                "_repository_identity",
                return_value=(REVISION, PREVIOUS, "0.84.0"),
            ), patch.object(
                qualify_local_release, "qualification_paths", return_value=paths
            ):
                with self.assertRaisesRegex(
                    qualify_local_release.LocalReleaseQualificationError,
                    "local-release.stage.source-quality.failed",
                ):
                    qualify_local_release.qualify_local_release(
                        upgrade_from_revision=PREVIOUS,
                        toolchain=qualify_local_release.Toolchain(),
                        runner=failed,
                    )
            self.assertEqual(failed_calls, 1)


if __name__ == "__main__":
    unittest.main()
