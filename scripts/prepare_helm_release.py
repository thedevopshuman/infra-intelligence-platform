#!/usr/bin/env python3
"""Prepare a clean, independently versioned core Helm chart; never publish it.

The committed chart must already pin its explicitly selected application image.
Lint/render are packaging checks, not installation or image qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from package_licensed_chart import CHART_SOURCE, package_chart  # noqa: E402


GITHUB_REPOSITORY = "thedevopshuman/infra-intelligence-platform"
IMAGE_REPOSITORY = "docker.io/thedevopshuman/iip"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
REVISION = re.compile(r"[a-f0-9]{40,64}")
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
RENDER_VALUES = (
    "--set-string", "database.existingSecret=iip-chart-check-database",
    "--set-string", "database.transportSecurity.caExistingSecret=iip-chart-check-database-ca",
    "--set-string", "auth.existingSecret=iip-chart-check-auth",
    "--set", "database.migrations.enabled=true",
    "--set", "worker.enabled=true",
    "--set-json", 'worker.tenants=["chart-check"]',
)


class HelmReleaseError(ValueError):
    """Stable packaging failures without subprocess output or private paths."""


def _run(command: Sequence[str], *, root: Path, timeout: int = 60) -> bytes:
    try:
        completed = subprocess.run(
            list(command), cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            timeout=timeout,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        )
    except (OSError, subprocess.SubprocessError):
        raise HelmReleaseError("helm-release.command.failed") from None
    if len(completed.stdout) > MAX_OUTPUT_BYTES:
        raise HelmReleaseError("helm-release.command.output-invalid")
    return completed.stdout


def _git(root: Path, *arguments: str) -> bytes:
    return _run(("git", *arguments), root=root, timeout=30)


def _clean_source(root: Path, revision: str, tag: str) -> None:
    if (_git(root, "rev-parse", "HEAD").decode().strip() != revision
            or _git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").decode().strip() != revision):
        raise HelmReleaseError("helm-release.source.mismatch")
    if _git(root, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}").decode().strip() != revision:
        raise HelmReleaseError("helm-release.source.not-main")
    if _git(root, "status", "--porcelain", "--untracked-files=normal").strip():
        raise HelmReleaseError("helm-release.source.dirty")


def _scalar(document: str, key: str, *, indent: str = "") -> str:
    # This small source parser deliberately accepts only the chart's existing
    # one-line scalar style. It does not reinterpret YAML aliases or objects.
    candidates = [line for line in document.splitlines() if line.startswith(indent + key + ":")]
    if len(candidates) != 1:
        raise HelmReleaseError("helm-release.chart.invalid")
    value = candidates[0][len(indent + key + ":"):].strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    if not value or re.search(r"[\s\"'#{}\[\]&*!]", value):
        raise HelmReleaseError("helm-release.chart.invalid")
    return value


def _image_defaults(values: str) -> tuple[str, str]:
    lines = values.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("image:")]
    if len(starts) != 1 or lines[starts[0]] != "image:":
        raise HelmReleaseError("helm-release.image.immutable-pin-required")
    selected: list[str] = []
    for line in lines[starts[0] + 1:]:
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        selected.append(line)
    try:
        block = "\n".join(selected)
        repository = _scalar(block, "repository", indent="  ")
        digest = _scalar(block, "digest", indent="  ")
    except HelmReleaseError:
        raise HelmReleaseError("helm-release.image.immutable-pin-required") from None
    if repository != IMAGE_REPOSITORY or DIGEST.fullmatch(digest) is None:
        raise HelmReleaseError("helm-release.image.immutable-pin-required")
    return repository, digest


def prepare(
    *, root: Path, tag: str, revision: str, github_repository: str,
    output: Path, helm: str = "helm",
) -> Path:
    if (not tag.startswith("helm-v") or len(tag) > 135
            or VERSION.fullmatch(tag.removeprefix("helm-v")) is None):
        raise HelmReleaseError("helm-release.tag.invalid")
    if github_repository != GITHUB_REPOSITORY:
        raise HelmReleaseError("helm-release.repository.invalid")
    if REVISION.fullmatch(revision) is None:
        raise HelmReleaseError("helm-release.revision.invalid")
    root, output = root.resolve(), output.absolute()
    if output.exists() or output.is_symlink():
        raise HelmReleaseError("helm-release.output.exists")
    _clean_source(root, revision, tag)
    metadata = _git(root, "show", f"{revision}:{CHART_SOURCE}/Chart.yaml").decode()
    chart_version, app_version = _scalar(metadata, "version"), _scalar(metadata, "appVersion")
    if (_scalar(metadata, "name") != "infra-intelligence"
            or VERSION.fullmatch(chart_version) is None
            or VERSION.fullmatch(app_version) is None):
        raise HelmReleaseError("helm-release.chart.invalid")
    if tag != "helm-v" + chart_version:
        raise HelmReleaseError("helm-release.tag.mismatch")
    # appVersion is an independently reviewed compatibility pin. Never read
    # pyproject.toml or require it to equal this repository's current app version.
    repository, digest = _image_defaults(
        _git(root, "show", f"{revision}:{CHART_SOURCE}/values.yaml").decode()
    )
    filename = f"infra-intelligence-{chart_version}.tgz"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="iip-helm-release-") as temporary:
        staged = Path(temporary) / filename
        _clean_source(root, revision, tag)
        package_chart(root, revision, staged)
        _run((helm, "lint", str(staged), *RENDER_VALUES), root=root)
        rendered = _run((helm, "template", "iip-chart-check", str(staged),
                         "--namespace", "iip-chart-check", *RENDER_VALUES), root=root).decode()
        images = re.findall(r'^\s+image:\s+"([^"\n]+)"\s*$', rendered, re.M)
        image_lines = re.findall(r'^\s+image:.*$', rendered, re.M)
        if (len(images) < 3 or len(images) != len(image_lines)
                or any(image != repository + "@" + digest for image in images)):
            raise HelmReleaseError("helm-release.render.image-mismatch")
        _clean_source(root, revision, tag)
        checksum = hashlib.sha256(staged.read_bytes()).hexdigest() + "  " + filename + "\n"
        # Reserve a brand-new directory; a failed/partial run is never overwritten.
        output.mkdir(mode=0o755)
        with staged.open("rb") as source, (output / filename).open("xb") as target:
            shutil.copyfileobj(source, target)
        with (output / "SHA256SUMS").open("x", encoding="ascii") as target:
            target.write(checksum)
    return output / filename


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--github-repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--helm", default="helm")
    arguments = parser.parse_args(argv)
    try:
        archive = prepare(root=ROOT, tag=arguments.tag, revision=arguments.revision,
                          github_repository=arguments.github_repository,
                          output=arguments.output, helm=arguments.helm)
    except HelmReleaseError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        print("helm-release.preparation.failed", file=sys.stderr)
        return 2
    print(f"helm-release.prepared {archive.name}")
    print("Chart-only packaging; not application publication, image qualification, or a cluster installation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
