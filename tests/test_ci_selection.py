from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from scripts import select_ci_checks as selection


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
FULL_JOBS = (
    "community-recovery",
    "learning-preview",
    "verify",
    "plugin-compatibility",
    "identity-and-policy-compatibility",
    "postgresql-continuity",
    "instrumentation-compatibility",
    "release-supply-chain-compatibility",
)


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()


class CiPathClassificationTests(unittest.TestCase):
    def test_documentation_chart_and_combined_changes_are_lightweight(self) -> None:
        cases = (
            (("docs/operations/install.md", "README.md"), "documentation-only"),
            (("deploy/helm/infra-intelligence/values.yaml",), "chart-only"),
            (
                (
                    "docs/operations/helm-deployment.md",
                    "deploy/helm/infra-intelligence/Chart.yaml",
                ),
                "documentation-and-chart-only",
            ),
        )
        for paths, reason in cases:
            with self.subTest(paths=paths):
                result = selection.classify_paths(paths)
                self.assertEqual(result.mode, "charts")
                self.assertEqual(result.reason, reason)
                self.assertIn("run_lightweight=true", result.output_lines())

    def test_runtime_unknown_and_ci_policy_changes_are_full(self) -> None:
        for path in (
            "src/iip/bootstrap.py",
            "contracts/schemas/resource.schema.json",
            "scripts/select_ci_checks.py",
            "tests/test_ci_selection.py",
            ".github/workflows/ci.yml",
            "AGENTS.md",
            "Makefile",
        ):
            with self.subTest(path=path):
                result = selection.classify_paths((path,))
                self.assertEqual(result.mode, "full")
                self.assertEqual(result.reason, "runtime-or-unknown-path")

    def test_any_runtime_path_makes_a_mixed_change_full(self) -> None:
        result = selection.classify_paths(
            ("docs/architecture/overview.md", "src/iip/domain/resource.py")
        )
        self.assertEqual(result, selection.Selection("full", "runtime-or-unknown-path"))

    def test_empty_or_unsafe_paths_fail_closed(self) -> None:
        self.assertEqual(selection.classify_paths(()).mode, "full")
        for path in ("../docs/a.md", "/docs/a.md", "docs//a.md", "docs\\a.md", "docs/a\n.md"):
            with self.subTest(path=path):
                result = selection.classify_paths((path,))
                self.assertEqual(result.mode, "full")
                self.assertEqual(result.reason, "changed-path-invalid")

    def test_manual_scope_is_explicit_and_auto_without_a_diff_is_full(self) -> None:
        common = {
            "repository": ROOT,
            "event_name": "workflow_dispatch",
            "base_sha": "",
            "head_sha": "",
        }
        self.assertEqual(
            selection.select_checks(requested_scope="full", **common),
            selection.Selection("full", "manual-full"),
        )
        self.assertEqual(
            selection.select_checks(requested_scope="charts", **common),
            selection.Selection("charts", "manual-charts"),
        )
        self.assertEqual(
            selection.select_checks(requested_scope="auto", **common),
            selection.Selection("full", "manual-auto-without-diff"),
        )


class CiGitDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="iip-ci-selection-")
        self.addCleanup(temporary.cleanup)
        self.repository = Path(temporary.name)
        git(self.repository, "init", "--quiet")
        git(self.repository, "config", "user.name", "CI selection fixture")
        git(self.repository, "config", "user.email", "fixture@example.invalid")
        (self.repository / "README.md").write_text("base\n", encoding="utf-8")
        (self.repository / "src").mkdir()
        (self.repository / "src" / "runtime.py").write_text("base\n", encoding="utf-8")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "--quiet", "-m", "base")
        self.base = git(self.repository, "rev-parse", "HEAD")

    def commit(self, path: str, content: str) -> str:
        target = self.repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git(self.repository, "add", "-A")
        git(self.repository, "commit", "--quiet", "-m", path)
        return git(self.repository, "rev-parse", "HEAD")

    def test_push_uses_exact_before_to_head_path_set(self) -> None:
        head = self.commit("docs/guide.md", "docs\n")
        result = selection.select_checks(
            repository=self.repository,
            event_name="push",
            requested_scope="auto",
            base_sha=self.base,
            head_sha=head,
        )
        self.assertEqual(result, selection.Selection("charts", "documentation-only"))

    def test_pull_request_uses_merge_base_and_does_not_collapse_renames(self) -> None:
        git(self.repository, "checkout", "--quiet", "-b", "feature", self.base)
        head = self.commit("deploy/helm/example/values.yaml", "enabled: true\n")
        git(self.repository, "checkout", "--quiet", "-b", "updated-base", self.base)
        updated_base = self.commit("src/runtime.py", "base branch update\n")

        result = selection.select_checks(
            repository=self.repository,
            event_name="pull_request",
            requested_scope="auto",
            base_sha=updated_base,
            head_sha=head,
        )
        self.assertEqual(result, selection.Selection("charts", "chart-only"))

        git(self.repository, "checkout", "--quiet", "feature")
        (self.repository / "docs").mkdir()
        git(self.repository, "mv", "src/runtime.py", "docs/runtime.md")
        git(self.repository, "commit", "--quiet", "-m", "rename runtime")
        renamed_head = git(self.repository, "rev-parse", "HEAD")
        renamed = selection.select_checks(
            repository=self.repository,
            event_name="pull_request",
            requested_scope="auto",
            base_sha=updated_base,
            head_sha=renamed_head,
        )
        self.assertEqual(renamed.mode, "full")

    def test_missing_or_unresolvable_revision_fails_closed_to_full(self) -> None:
        head = self.commit("docs/guide.md", "docs\n")
        for base in ("", "0" * 40, "not-a-revision"):
            with self.subTest(base=base):
                result = selection.select_checks(
                    repository=self.repository,
                    event_name="push",
                    requested_scope="auto",
                    base_sha=base,
                    head_sha=head,
                )
                self.assertEqual(
                    result,
                    selection.Selection("full", "changed-paths-unavailable"),
                )


class CiWorkflowSelectionTests(unittest.TestCase):
    def test_workflow_exposes_only_the_bounded_manual_scope(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:\n", workflow)
        self.assertIn("      scope:\n", workflow)
        self.assertIn("        options:\n          - auto\n          - full\n          - charts\n", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)

    def test_selector_receives_event_values_through_environment_only(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        selector = workflow.split("  select-checks:\n", 1)[1].split(
            "\n  charts-and-docs:\n", 1
        )[0]
        self.assertIn("fetch-depth: 0", selector)
        self.assertIn("IIP_CI_SCOPE: ${{ inputs.scope || 'auto' }}", selector)
        self.assertIn("IIP_CI_BASE_SHA:", selector)
        self.assertIn("IIP_CI_HEAD_SHA:", selector)
        run = selector.split("        run: |\n", 1)[1]
        self.assertNotIn("${{", run)
        self.assertEqual(
            run.strip(), 'python scripts/select_ci_checks.py >> "$GITHUB_OUTPUT"'
        )

    def test_eight_existing_jobs_remain_behind_full_selection(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for index, job in enumerate(FULL_JOBS):
            end = (
                f"\n  {FULL_JOBS[index + 1]}:\n"
                if index + 1 < len(FULL_JOBS)
                else None
            )
            block = workflow.split(f"\n  {job}:\n", 1)[1]
            if end is not None:
                block = block.split(end, 1)[0]
            self.assertIn("needs: select-checks", block)
            self.assertIn("if: needs.select-checks.outputs.run_full == 'true'", block)

    def test_lightweight_lane_has_only_focused_static_chart_checks(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        block = workflow.split("\n  charts-and-docs:\n", 1)[1].split(
            "\n  community-recovery:\n", 1
        )[0]
        self.assertIn("if: needs.select-checks.outputs.run_lightweight == 'true'", block)
        self.assertIn("run: make verify-charts", block)
        self.assertNotRegex(block, r"run:\s+make verify(?:\s|$)")
        for forbidden in ("docker ", "make release-bundle", "secrets."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, block)

    def test_all_actions_remain_commit_pinned(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        actions = re.findall(r"^\s*- uses: ([^\s#]+)", workflow, re.M)
        self.assertEqual(len(actions), 20)
        for action in actions:
            self.assertRegex(action, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[a-f0-9]{40}$")


if __name__ == "__main__":
    unittest.main()
