#!/usr/bin/env python3
"""Select the full or lightweight CI lane from a validated Git diff."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
MAX_DIFF_BYTES = 8 * 1024 * 1024
MAX_CHANGED_PATHS = 10_000
MANUAL_SCOPES = frozenset(("auto", "full", "charts"))
DOCUMENTATION_FILES = frozenset(
    (
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "SECURITY.md",
        "SUPPORT.md",
    )
)


class SelectionError(RuntimeError):
    """The changed-path set could not be established safely."""


@dataclass(frozen=True)
class Selection:
    mode: str
    reason: str

    def output_lines(self) -> tuple[str, ...]:
        return (
            f"selection={self.mode}",
            f"reason={self.reason}",
            f"run_full={'true' if self.mode == 'full' else 'false'}",
            f"run_lightweight={'true' if self.mode == 'charts' else 'false'}",
        )


def _full(reason: str) -> Selection:
    return Selection(mode="full", reason=reason)


def _charts(reason: str) -> Selection:
    return Selection(mode="charts", reason=reason)


def _safe_repository_path(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    if any(ord(character) < 32 for character in value):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _lightweight_path(value: str) -> bool:
    return (
        value in DOCUMENTATION_FILES
        or value.startswith("docs/")
        or value.startswith("deploy/helm/")
    )


def classify_paths(paths: Sequence[str]) -> Selection:
    """Classify a complete, validated set of changed repository paths."""

    unique = tuple(sorted(set(paths)))
    if not unique:
        return _full("changed-paths-empty")
    if any(not _safe_repository_path(path) for path in unique):
        return _full("changed-path-invalid")
    if any(not _lightweight_path(path) for path in unique):
        return _full("runtime-or-unknown-path")

    has_docs = any(
        path in DOCUMENTATION_FILES or path.startswith("docs/") for path in unique
    )
    has_chart = any(path.startswith("deploy/helm/") for path in unique)
    if has_docs and has_chart:
        return _charts("documentation-and-chart-only")
    if has_chart:
        return _charts("chart-only")
    return _charts("documentation-only")


def _git(*arguments: str, repository: Path) -> bytes:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise SelectionError("ci-selection.git-unavailable") from None
    if completed.returncode != 0 or len(completed.stdout) > MAX_DIFF_BYTES:
        raise SelectionError("ci-selection.git-unavailable")
    return completed.stdout


def _require_commit(repository: Path, revision: str) -> None:
    if SHA.fullmatch(revision) is None:
        raise SelectionError("ci-selection.revision-invalid")
    _git("cat-file", "-e", f"{revision}^{{commit}}", repository=repository)


def changed_paths(
    *, repository: Path, event_name: str, base_sha: str, head_sha: str
) -> tuple[str, ...]:
    """Return changed paths without rename collapsing or shell evaluation."""

    if event_name not in ("pull_request", "push"):
        raise SelectionError("ci-selection.event-unsupported")
    _require_commit(repository, base_sha)
    _require_commit(repository, head_sha)
    revision_range = (
        f"{base_sha}...{head_sha}"
        if event_name == "pull_request"
        else f"{base_sha}..{head_sha}"
    )
    encoded = _git(
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        revision_range,
        "--",
        repository=repository,
    )
    try:
        values = tuple(
            item.decode("utf-8", errors="strict")
            for item in encoded.split(b"\0")
            if item
        )
    except UnicodeDecodeError:
        raise SelectionError("ci-selection.path-invalid") from None
    if len(values) > MAX_CHANGED_PATHS:
        raise SelectionError("ci-selection.path-limit")
    return values


def select_checks(
    *,
    repository: Path,
    event_name: str,
    requested_scope: str,
    base_sha: str,
    head_sha: str,
) -> Selection:
    """Select CI checks, failing closed to the full lane."""

    scope = requested_scope or "auto"
    if scope not in MANUAL_SCOPES:
        return _full("scope-invalid")
    if event_name == "workflow_dispatch":
        if scope == "full":
            return _full("manual-full")
        if scope == "charts":
            return _charts("manual-charts")
        return _full("manual-auto-without-diff")
    if scope != "auto":
        return _full("scope-event-mismatch")
    try:
        paths = changed_paths(
            repository=repository,
            event_name=event_name,
            base_sha=base_sha,
            head_sha=head_sha,
        )
    except SelectionError:
        return _full("changed-paths-unavailable")
    return classify_paths(paths)


def main() -> int:
    selection = select_checks(
        repository=ROOT,
        event_name=os.environ.get("IIP_CI_EVENT_NAME", ""),
        requested_scope=os.environ.get("IIP_CI_SCOPE", "auto"),
        base_sha=os.environ.get("IIP_CI_BASE_SHA", ""),
        head_sha=os.environ.get("IIP_CI_HEAD_SHA", ""),
    )
    for line in selection.output_lines():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
