from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_helm_release as release


ROOT = Path(__file__).resolve().parents[1]
TAG = "helm-v0.7.0"
PIN = "sha256:" + "a" * 64


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True,
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()


class HelmReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="iip-helm-release-test-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.fixture_count = 0
        self.helm_calls: list[tuple[str, ...]] = []
        self.real_run = release._run

    def fixture(self, *, repository: str = release.IMAGE_REPOSITORY, digest: str = PIN) -> tuple[Path, str]:
        self.fixture_count += 1
        root = self.directory / f"source-{self.fixture_count}"
        chart = root / release.CHART_SOURCE
        shutil.copytree(ROOT / release.CHART_SOURCE, chart)
        metadata = (chart / "Chart.yaml").read_text()
        metadata = re.sub(r"(?m)^version:.*$", "version: 0.7.0", metadata)
        metadata = re.sub(r"(?m)^appVersion:.*$", 'appVersion: "0.3.0"', metadata)
        (chart / "Chart.yaml").write_text(metadata)
        values = (chart / "values.yaml").read_text()
        values = re.sub(r"(?m)^  repository:.*$", f"  repository: {repository}", values, count=1)
        values = re.sub(r"(?m)^  digest:.*$", f'  digest: "{digest}"', values, count=1)
        (chart / "values.yaml").write_text(values)
        (root / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
        (root / "NOTICE").write_bytes((ROOT / "NOTICE").read_bytes())
        (root / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n')
        (root / ".gitignore").write_text("dist/\n")
        git(root, "init", "-q")
        git(root, "add", ".")
        git(root, "-c", "user.name=Chart fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "commit", "-qm", "Committed chart fixture")
        revision = git(root, "rev-parse", "HEAD")
        git(root, "tag", TAG)
        git(root, "update-ref", "refs/remotes/origin/main", revision)
        return root, revision

    def fake_run(self, command, **kwargs):
        if command[0] == "fixture-helm":
            self.helm_calls.append(tuple(command))
            if command[1] == "template":
                return (f'          image: "{release.IMAGE_REPOSITORY}@{PIN}"\n' * 3).encode()
            return b"lint complete\n"
        return self.real_run(command, **kwargs)

    def prepare(self, root: Path, revision: str, **overrides) -> Path:
        arguments = dict(root=root, revision=revision, tag=TAG,
                         github_repository=release.GITHUB_REPOSITORY,
                         output=self.directory / "artifacts", helm="fixture-helm")
        arguments.update(overrides)
        with patch.object(release, "_run", side_effect=self.fake_run):
            return release.prepare(**arguments)

    def test_independent_versions_package_committed_notices_and_exact_checksums(self) -> None:
        root, revision = self.fixture()
        archive = self.prepare(root, revision)
        self.assertEqual(archive.name, "infra-intelligence-0.7.0.tgz")
        self.assertEqual(sorted(item.name for item in archive.parent.iterdir()),
                         ["SHA256SUMS", archive.name])
        self.assertEqual((archive.parent / "SHA256SUMS").read_text(),
                         hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
        with tarfile.open(archive) as packaged:
            for filename in ("LICENSE", "NOTICE"):
                self.assertEqual(packaged.extractfile("infra-intelligence/" + filename).read(),
                                 (root / filename).read_bytes())
            metadata = packaged.extractfile("infra-intelligence/Chart.yaml").read().decode()
            self.assertIn('appVersion: "0.3.0"', metadata)
            self.assertNotIn("9.9.9", metadata)
        self.assertEqual([call[1] for call in self.helm_calls], ["lint", "template"])
        self.assertTrue(all("--set-json" in call for call in self.helm_calls))
        self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_invalid_tag_revision_and_repository_fail_before_commands(self) -> None:
        base = dict(root=self.directory, revision="a" * 40, tag=TAG,
                    github_repository=release.GITHUB_REPOSITORY, output=self.directory / "output")
        for change in ({"tag": "v0.7.0"}, {"tag": "helm-v0.7.0/evil"},
                       {"tag": "helm-v0.7.0\n"}, {"revision": "HEAD"},
                       {"github_repository": "other/infra-intelligence-platform"}):
            with self.subTest(change=change), patch.object(release, "_run") as command:
                with self.assertRaises(release.HelmReleaseError):
                    release.prepare(**{**base, **change})
                command.assert_not_called()

    def test_tag_must_match_chart_and_exact_checkout_revision(self) -> None:
        root, revision = self.fixture()
        git(root, "tag", "helm-v0.7.1")
        with self.assertRaisesRegex(release.HelmReleaseError, "tag.mismatch"):
            self.prepare(root, revision, tag="helm-v0.7.1")
        with self.assertRaisesRegex(release.HelmReleaseError, "source.mismatch"):
            self.prepare(root, "f" * 40)
        self.assertFalse((self.directory / "artifacts").exists())

    def test_dirty_or_untracked_source_is_rejected(self) -> None:
        for filename in ("LICENSE", "untracked.txt"):
            root, revision = self.fixture()
            (root / filename).write_text("not committed")
            with self.subTest(filename=filename), self.assertRaisesRegex(release.HelmReleaseError, "source.dirty"):
                self.prepare(root, revision)
        self.assertEqual(self.helm_calls, [])

    def test_clean_tag_off_fetched_main_is_rejected_before_packaging(self) -> None:
        root, revision = self.fixture()
        git(root, "-c", "user.name=Chart fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "commit", "--allow-empty", "-qm", "New main tip")
        git(root, "update-ref", "refs/remotes/origin/main", git(root, "rev-parse", "HEAD"))
        git(root, "checkout", "--detach", revision)
        self.assertEqual(git(root, "status", "--porcelain"), "")
        with patch.object(release, "package_chart") as package:
            with self.assertRaisesRegex(release.HelmReleaseError, "source.not-main"):
                self.prepare(root, revision)
            package.assert_not_called()
        self.assertEqual(self.helm_calls, [])
        self.assertFalse((self.directory / "artifacts").exists())

    def test_missing_or_placeholder_or_other_registry_pin_is_blocked(self) -> None:
        for repository, digest in ((release.IMAGE_REPOSITORY, ""),
                                   ("ghcr.io/replace-me/infra-intelligence-platform", PIN),
                                   ("docker.io/other/iip", PIN),
                                   (release.IMAGE_REPOSITORY, "sha256:" + "A" * 64)):
            root, revision = self.fixture(repository=repository, digest=digest)
            with self.subTest(repository=repository, digest=digest), self.assertRaisesRegex(
                release.HelmReleaseError, "image.immutable-pin-required",
            ):
                self.prepare(root, revision)
        self.assertEqual(self.helm_calls, [])
        self.assertFalse((self.directory / "artifacts").exists())

    def test_existing_output_or_symlink_is_never_replaced(self) -> None:
        root, revision = self.fixture()
        output = self.directory / "artifacts"
        output.mkdir()
        sentinel = output / "keep"
        sentinel.write_text("retained")
        with self.assertRaisesRegex(release.HelmReleaseError, "output.exists"):
            self.prepare(root, revision)
        self.assertEqual(sentinel.read_text(), "retained")
        linked = self.directory / "linked"
        linked.symlink_to(self.directory / "missing")
        with self.assertRaisesRegex(release.HelmReleaseError, "output.exists"):
            self.prepare(root, revision, output=linked)
        self.assertTrue(linked.is_symlink())

    def test_lint_failure_and_different_rendered_image_publish_nothing(self) -> None:
        root, revision = self.fixture()
        def failing_lint(command, **kwargs):
            if command[0] == "fixture-helm":
                self.assertEqual(command[1], "lint")
                raise release.HelmReleaseError("helm-release.command.failed")
            return self.real_run(command, **kwargs)

        with patch.object(release, "_run", side_effect=failing_lint), self.assertRaisesRegex(
            release.HelmReleaseError, "command.failed",
        ):
            release.prepare(root=root, revision=revision, tag=TAG,
                            github_repository=release.GITHUB_REPOSITORY,
                            output=self.directory / "artifacts", helm="fixture-helm")
        self.assertFalse((self.directory / "artifacts").exists())
        for bad in (b'  image: "docker.io/other/iip@' + PIN.encode() + b'"\n', b"no workloads"):
            def run(command, **kwargs):
                if command[0] == "fixture-helm":
                    return bad if command[1] == "template" else b""
                return self.real_run(command, **kwargs)
            with patch.object(release, "_run", side_effect=run), self.assertRaisesRegex(
                release.HelmReleaseError, "render.image-mismatch",
            ):
                release.prepare(root=root, revision=revision, tag=TAG,
                                github_repository=release.GITHUB_REPOSITORY,
                                output=self.directory / "artifacts", helm="fixture-helm")
        self.assertFalse((self.directory / "artifacts").exists())

    @unittest.skipUnless(shutil.which("helm"), "Helm is unavailable")
    def test_real_packaged_chart_lints_and_renders_synthetic_core_dependencies(self) -> None:
        root, revision = self.fixture()
        archive = release.prepare(root=root, revision=revision, tag=TAG,
                                  github_repository=release.GITHUB_REPOSITORY,
                                  output=self.directory / "artifacts", helm=shutil.which("helm"))
        self.assertTrue(archive.is_file())


class HelmReleaseWorkflowTests(unittest.TestCase):
    def test_independent_protected_chart_only_publication(self) -> None:
        workflow = (ROOT / ".github/workflows/helm-release.yml").read_text()
        self.assertIn('      - "helm-v*"', workflow)
        self.assertNotIn('      - "v*"', workflow)
        self.assertIn("environment: release", workflow)
        self.assertIn("github.repository == 'thedevopshuman/infra-intelligence-platform'", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("persist-credentials: false", workflow)
        actions = re.findall(r"(?m)^\s+- uses: ([^\s]+)", workflow)
        self.assertEqual(len(actions), 3)
        self.assertTrue(all(re.fullmatch(r"[\w/-]+@[a-f0-9]{40}", action) for action in actions))
        for forbidden in ("make verify\n", "make release-bundle", "docker login", "docker push",
                          "buildx", "DOCKERHUB_TOKEN", "packages: write", "--clobber", "git tag", "git push"):
            self.assertNotIn(forbidden, workflow)
        ordered = ("python -m pip install --requirement requirements/verify.txt",
                   "make verify-workflows", "make verify-charts", "gh release view",
                   "git fetch --no-tags origin +refs/heads/main:refs/remotes/origin/main",
                   "prepare_helm_release.py", "cosign_container.sh sign-blob",
                   "cosign_container.sh verify-blob", "gh release create")
        offsets = [workflow.index(value) for value in ordered]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn("PYTHONPATH: src:sdks/python/src", workflow)
        self.assertIn("--verify-tag --latest=false", workflow)
        self.assertIn("helm-release.yml@refs/tags/$HELM_RELEASE_TAG", workflow)
        self.assertIn("--certificate-oidc-issuer https://token.actions.githubusercontent.com", workflow)
        self.assertIn('for asset in "$HELM_RELEASE_ARCHIVE" "$HELM_RELEASE_CHECKSUMS"', workflow)
        self.assertIn('"$HELM_RELEASE_CHECKSUMS.sigstore.json"', workflow)


if __name__ == "__main__":
    unittest.main()
