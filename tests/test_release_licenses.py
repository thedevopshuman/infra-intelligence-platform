"""Inspect real release archives for committed, independently usable notices."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from scripts.package_licensed_chart import package_chart


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts/build_release_bundle.sh"


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    ).stdout


class ReleaseLicenseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="iip-release-licenses-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.revision = git(ROOT, "rev-parse", "HEAD").decode().strip()

    def assert_notices(self, archive_path: Path, prefix: str) -> None:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            names = archive.getnames()
            self.assertEqual(len(names), len(set(names)))
            self.assertTrue(all(
                member.name.startswith(prefix)
                or (member.name == prefix.rstrip("/") and member.isdir())
                for member in archive.getmembers()
            ))
            for filename in ("LICENSE", "NOTICE"):
                member = archive.getmember(prefix + filename)
                self.assertTrue(member.isfile())
                source = archive.extractfile(member)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(
                    source.read(), git(ROOT, "show", f"{self.revision}:{filename}"),
                )

    def test_chart_contains_exact_committed_notices_and_original_structure(self) -> None:
        destination = self.directory / "chart.tgz"
        package_chart(ROOT, self.revision, destination)
        self.assert_notices(destination, "infra-intelligence/")
        with tarfile.open(destination, mode="r:gz") as archive:
            for filename in ("Chart.yaml", "values.yaml", "values.schema.json", "templates/deployment.yaml"):
                source = archive.extractfile("infra-intelligence/" + filename)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(
                    source.read(),
                    git(ROOT, "show", f"{self.revision}:deploy/helm/infra-intelligence/{filename}"),
                )
            self.assertNotIn("infra-intelligence/deploy", archive.getnames())

    @unittest.skipUnless(shutil.which("helm"), "Helm is not installed")
    def test_packaged_chart_passes_real_helm_lint(self) -> None:
        destination = self.directory / "chart.tgz"
        package_chart(ROOT, self.revision, destination)
        result = subprocess.run(
            ["helm", "lint", str(destination)], capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_contract_and_handoff_build_commands_package_committed_notices(self) -> None:
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        # Execute the actual two archive commands, without invoking Docker,
        # dependency installation, or the rest of the release pipeline.
        start = script.index("git archive --format=tar.gz")
        second = script.index("\ngit archive --format=tar.gz", start + 1)
        end = script.index("\ngit archive --format=tar.gz", second + 1)
        subprocess.run(
            ["sh", "-eu", "-c", script[start:end]], cwd=ROOT, check=True,
            capture_output=True, timeout=30,
            env={**os.environ, "IIP_RELEASE_VERSION": "0.84.0",
                 "IIP_RELEASE_REVISION": self.revision,
                 "IIP_RELEASE_BUNDLE_ABSOLUTE": str(self.directory)},
        )
        for kind, expected in (
            ("contracts", "contracts/schemas/resource.schema.json"),
            ("pilot-handoff", "SECURITY.md"),
        ):
            with self.subTest(kind=kind):
                prefix = f"infra-intelligence-{kind}-0.84.0/"
                archive_path = self.directory / f"infra-intelligence-{kind}-0.84.0.tar.gz"
                self.assert_notices(archive_path, prefix)
                with tarfile.open(archive_path, mode="r:gz") as archive:
                    self.assertTrue(archive.getmember(prefix + expected).isfile())

    def test_chart_ignores_working_tree_license_and_chart_mutations(self) -> None:
        repository = self.directory / "repository"
        chart = repository / "deploy/helm/infra-intelligence"
        chart.mkdir(parents=True)
        for filename, content in (("LICENSE", "committed license"), ("NOTICE", "committed notice")):
            (repository / filename).write_text(content, encoding="utf-8")
        (chart / "Chart.yaml").write_text("committed chart", encoding="utf-8")
        git(repository, "init", "--quiet")
        git(repository, "add", ".")
        git(repository, "-c", "core.hooksPath=/dev/null", "-c",
            "user.name=Release packaging fixture", "-c",
            "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "--quiet", "-m", "Committed packaging fixture")
        revision = git(repository, "rev-parse", "HEAD").decode().strip()
        (repository / "LICENSE").write_text("uncommitted license", encoding="utf-8")
        (repository / "NOTICE").unlink()
        (chart / "Chart.yaml").write_text("uncommitted chart", encoding="utf-8")
        (chart / "untracked.txt").write_text("excluded", encoding="utf-8")
        destination = self.directory / "committed.tgz"
        package_chart(repository, revision, destination)
        with tarfile.open(destination, mode="r:gz") as archive:
            for filename, expected in (
                ("LICENSE", b"committed license"), ("NOTICE", b"committed notice"),
                ("Chart.yaml", b"committed chart"),
            ):
                source = archive.extractfile("infra-intelligence/" + filename)
                self.assertIsNotNone(source)
                assert source is not None
                self.assertEqual(source.read(), expected)
            self.assertNotIn("infra-intelligence/untracked.txt", archive.getnames())

    def test_chart_packaging_is_reproducible_and_refuses_overwrite(self) -> None:
        first, second = self.directory / "first.tgz", self.directory / "second.tgz"
        package_chart(ROOT, self.revision, first)
        package_chart(ROOT, self.revision, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.assertRaises(FileExistsError):
            package_chart(ROOT, self.revision, first)

    def test_builder_still_rejects_dirty_source_before_packaging(self) -> None:
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("git status --porcelain --untracked-files=normal", script)
        self.assertLess(
            script.index("Release bundles must be built from a clean committed worktree"),
            script.index("scripts/package_licensed_chart.py"),
        )


if __name__ == "__main__":
    unittest.main()
