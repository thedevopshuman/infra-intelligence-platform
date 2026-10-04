#!/usr/bin/env python3
"""Rebuild only the v0.84.2 non-image release assets from its exact source."""

from __future__ import annotations

import argparse
from datetime import datetime
import io
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
RELEASE_TAG = "v0.84.2"
RELEASE_REVISION = "cd95235d0c95ffa011eeea4fec3edf89faac9a2a"
RELEASE_TAG_OBJECT = "11ba80dc71387666797bd202210c0625d6c4e27a"
EXPECTED_VERSIONS = {
    "version": "0.84.2",
    "chartVersion": "0.87.2",
    "pythonSdkVersion": "0.84.0",
    "typescriptSdkVersion": "0.84.0",
    "bedrockInstrumentationVersion": "0.1.0",
}
ARTIFACT_NAMES = (
    "infra-intelligence-0.87.2.tgz",
    "infra-intelligence-contracts-0.84.2.tar.gz",
    "infra-intelligence-sdk-0.84.0.tar.gz",
    "iip-sdk-0.84.0.tgz",
    "iip-opentelemetry-aws-bedrock-0.1.0.tar.gz",
    "infra-intelligence-pilot-handoff-0.84.2.tar.gz",
    "infra-intelligence-community-0.84.2.tar.gz",
)
MAX_COMMAND_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TYPESCRIPT_SOURCE_FILES = 1024
MAX_TYPESCRIPT_SOURCE_BYTES = 64 * 1024 * 1024
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")


class SourceAssetRecoveryError(RuntimeError):
    """Stable recovery failure that never includes command output or paths."""


def _executable(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(ord(character) < 32 for character in value)
    ):
        raise SourceAssetRecoveryError("release-recovery.tool.invalid")
    return value


def _base_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in ("PATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def _run_process(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
) -> bytes:
    try:
        completed = subprocess.run(
            tuple(arguments),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise SourceAssetRecoveryError("release-recovery.command.failed") from None
    if (
        completed.returncode != 0
        or len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(completed.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise SourceAssetRecoveryError("release-recovery.command.failed")
    return completed.stdout


def _git(source: Path, environment: Mapping[str, str], *arguments: str) -> bytes:
    return _run_process(
        ("git", *arguments),
        cwd=source,
        environment=environment,
        timeout=60,
    )


def _run_tool(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
) -> None:
    _run_process(
        arguments,
        cwd=cwd,
        environment=environment,
        timeout=timeout,
    )


def _text(value: bytes) -> str:
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SourceAssetRecoveryError("release-recovery.source.invalid") from None


def _git_text(
    source: Path, environment: Mapping[str, str], *arguments: str
) -> str:
    return _text(_git(source, environment, *arguments)).strip()


def _clean_source_identity(
    source: Path, environment: Mapping[str, str]
) -> None:
    try:
        top = Path(
            _git_text(source, environment, "rev-parse", "--show-toplevel")
        ).resolve(strict=True)
    except (OSError, RuntimeError):
        raise SourceAssetRecoveryError("release-recovery.source.invalid") from None
    if top != source:
        raise SourceAssetRecoveryError("release-recovery.source.invalid")
    if _git_text(source, environment, "rev-parse", "HEAD") != RELEASE_REVISION:
        raise SourceAssetRecoveryError("release-recovery.source.revision-mismatch")
    if (
        _git_text(source, environment, "rev-parse", f"refs/tags/{RELEASE_TAG}")
        != RELEASE_TAG_OBJECT
        or _git_text(source, environment, "cat-file", "-t", f"refs/tags/{RELEASE_TAG}")
        != "tag"
        or _git_text(
            source,
            environment,
            "rev-parse",
            "--verify",
            f"refs/tags/{RELEASE_TAG}^{{commit}}",
        )
        != RELEASE_REVISION
    ):
        raise SourceAssetRecoveryError("release-recovery.source.tag-mismatch")
    if _git(source, environment, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SourceAssetRecoveryError("release-recovery.source.dirty")


def _git_object(
    source: Path, environment: Mapping[str, str], relative: str
) -> bytes:
    return _git(source, environment, "show", f"{RELEASE_REVISION}:{relative}")


def _chart_scalar(document: str, name: str) -> str:
    values = []
    for line in document.splitlines():
        if line.startswith(name + ":"):
            value = line[len(name) + 1 :].strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            values.append(value)
    if len(values) != 1 or _VERSION.fullmatch(values[0]) is None:
        raise SourceAssetRecoveryError("release-recovery.version.invalid")
    return values[0]


def _source_metadata(
    source: Path, environment: Mapping[str, str]
) -> dict[str, object]:
    try:
        application = tomllib.loads(
            _text(_git_object(source, environment, "pyproject.toml"))
        )["project"]["version"]
        python_sdk = tomllib.loads(
            _text(_git_object(source, environment, "sdks/python/pyproject.toml"))
        )["project"]["version"]
        typescript_sdk = json.loads(
            _text(_git_object(source, environment, "sdks/typescript/package.json"))
        )["version"]
        bedrock = tomllib.loads(
            _text(
                _git_object(
                    source,
                    environment,
                    "instrumentation/python/aws-bedrock/pyproject.toml",
                )
            )
        )["project"]["version"]
        chart = _text(
            _git_object(source, environment, "deploy/helm/infra-intelligence/Chart.yaml")
        )
        chart_version = _chart_scalar(chart, "version")
        chart_application = _chart_scalar(chart, "appVersion")
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
    ):
        raise SourceAssetRecoveryError("release-recovery.version.invalid") from None
    actual = {
        "version": application,
        "chartVersion": chart_version,
        "pythonSdkVersion": python_sdk,
        "typescriptSdkVersion": typescript_sdk,
        "bedrockInstrumentationVersion": bedrock,
    }
    if actual != EXPECTED_VERSIONS or chart_application != application:
        raise SourceAssetRecoveryError("release-recovery.version.mismatch")
    source_date = _git_text(
        source, environment, "show", "-s", "--format=%cI", RELEASE_REVISION
    )
    try:
        parsed = datetime.fromisoformat(source_date.replace("Z", "+00:00"))
    except ValueError:
        raise SourceAssetRecoveryError("release-recovery.source-date.invalid") from None
    if parsed.tzinfo is None:
        raise SourceAssetRecoveryError("release-recovery.source-date.invalid")
    return {
        "version": actual["version"],
        "chart_version": actual["chartVersion"],
        "python_sdk_version": actual["pythonSdkVersion"],
        "typescript_sdk_version": actual["typescriptSdkVersion"],
        "bedrock_instrumentation_version": actual[
            "bedrockInstrumentationVersion"
        ],
        "revision": RELEASE_REVISION,
        "source_date": source_date,
        "platforms": ("linux/amd64", "linux/arm64"),
    }


def _archive(
    source: Path,
    environment: Mapping[str, str],
    output: Path,
    prefix: str,
    tree: str,
    *paths: str,
) -> None:
    _git(
        source,
        environment,
        "archive",
        "--format=tar.gz",
        f"--prefix={prefix}/",
        f"--output={output}",
        tree,
        *paths,
    )


def _materialize_typescript_source(
    source: Path, environment: Mapping[str, str], destination: Path
) -> None:
    """Materialize the tracked SDK subtree from the fixed release Git object."""

    encoded = _git(
        source,
        environment,
        "archive",
        "--format=tar",
        f"{RELEASE_REVISION}:sdks/typescript",
    )
    try:
        destination.mkdir(mode=0o700)
        with tarfile.open(fileobj=io.BytesIO(encoded), mode="r:") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_TYPESCRIPT_SOURCE_FILES:
                raise SourceAssetRecoveryError(
                    "release-recovery.typescript-source.invalid"
                )
            total = 0
            for member in members:
                relative = PurePosixPath(member.name)
                if (
                    not member.name
                    or relative.is_absolute()
                    or any(part in ("", ".", "..") for part in relative.parts)
                    or member.pax_headers
                ):
                    raise SourceAssetRecoveryError(
                        "release-recovery.typescript-source.invalid"
                    )
                selected = destination.joinpath(*relative.parts)
                if member.isdir():
                    selected.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                if not member.isfile() or member.size < 0:
                    raise SourceAssetRecoveryError(
                        "release-recovery.typescript-source.invalid"
                    )
                total += member.size
                if total > MAX_TYPESCRIPT_SOURCE_BYTES:
                    raise SourceAssetRecoveryError(
                        "release-recovery.typescript-source.invalid"
                    )
                selected.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise SourceAssetRecoveryError(
                        "release-recovery.typescript-source.invalid"
                    )
                descriptor = os.open(
                    selected,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o755 if member.mode & 0o111 else 0o644,
                )
                with os.fdopen(descriptor, "wb") as target:
                    remaining = member.size
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise SourceAssetRecoveryError(
                                "release-recovery.typescript-source.invalid"
                            )
                        target.write(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        raise SourceAssetRecoveryError(
                            "release-recovery.typescript-source.invalid"
                        )
    except SourceAssetRecoveryError:
        raise
    except (OSError, tarfile.TarError, ValueError):
        raise SourceAssetRecoveryError(
            "release-recovery.typescript-source.invalid"
        ) from None


def _validate_inventory(directory: Path) -> None:
    try:
        entries = tuple(os.scandir(directory))
    except OSError:
        raise SourceAssetRecoveryError("release-recovery.output.invalid") from None
    if {entry.name for entry in entries} != set(ARTIFACT_NAMES):
        raise SourceAssetRecoveryError("release-recovery.output.inventory-invalid")
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise SourceAssetRecoveryError(
                    "release-recovery.output.inventory-invalid"
                )
            if entry.stat(follow_symlinks=False).st_size < 1:
                raise SourceAssetRecoveryError(
                    "release-recovery.output.inventory-invalid"
                )
        except OSError:
            raise SourceAssetRecoveryError(
                "release-recovery.output.inventory-invalid"
            ) from None


def _publish(candidate: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise SourceAssetRecoveryError("release-recovery.output.exists")
    linked: list[Path] = []
    created = False
    complete = False
    try:
        output.mkdir(mode=0o755)
        created = True
        for name in ARTIFACT_NAMES:
            destination = output / name
            os.link(candidate / name, destination, follow_symlinks=False)
            linked.append(destination)
        _validate_inventory(output)
        complete = True
    except SourceAssetRecoveryError:
        raise
    except (FileExistsError, OSError):
        raise SourceAssetRecoveryError("release-recovery.output.publish-failed") from None
    finally:
        if created and not complete:
            for path in reversed(linked):
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                output.rmdir()
            except OSError:
                pass


def build_source_assets(
    source: Path,
    output: Path,
    python: str = sys.executable,
    npm: str = "npm",
    helm: str = "helm",
) -> Mapping[str, object]:
    """Build the seven v0.84.2 source assets without building application images."""

    python, npm, helm = (_executable(item) for item in (python, npm, helm))
    try:
        selected_source = source.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise SourceAssetRecoveryError("release-recovery.source.invalid") from None
    if not selected_source.is_dir():
        raise SourceAssetRecoveryError("release-recovery.source.invalid")
    selected_root = ROOT.resolve()
    if (
        selected_source == selected_root
        or selected_source.is_relative_to(selected_root)
        or selected_root.is_relative_to(selected_source)
    ):
        raise SourceAssetRecoveryError("release-recovery.source.separate-required")

    selected_output = output.expanduser().absolute()
    try:
        resolved_output = selected_output.resolve(strict=False)
    except (OSError, RuntimeError):
        raise SourceAssetRecoveryError("release-recovery.output.invalid") from None
    if resolved_output.is_relative_to(selected_source):
        raise SourceAssetRecoveryError("release-recovery.output.invalid")
    if selected_output.exists() or selected_output.is_symlink():
        raise SourceAssetRecoveryError("release-recovery.output.exists")
    try:
        selected_output.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise SourceAssetRecoveryError("release-recovery.output.invalid") from None

    with tempfile.TemporaryDirectory(prefix="iip-recovery-tools-") as tools_temp:
        tools = Path(tools_temp)
        environment = _base_environment()
        helm_state = tools / "helm"
        for name in ("config", "cache", "data"):
            (helm_state / name).mkdir(parents=True, mode=0o700)
        environment.update(
            {
                "HELM_CONFIG_HOME": str(helm_state / "config"),
                "HELM_CACHE_HOME": str(helm_state / "cache"),
                "HELM_DATA_HOME": str(helm_state / "data"),
            }
        )
        _clean_source_identity(selected_source, environment)
        metadata = _source_metadata(selected_source, environment)

        with tempfile.TemporaryDirectory(
            prefix=".iip-source-assets-", dir=selected_output.parent
        ) as staging_temp:
            candidate = Path(staging_temp) / "assets"
            candidate.mkdir(mode=0o700)

            _run_tool(
                (
                    python,
                    "scripts/installation_kit.py",
                    "build",
                    "--root",
                    str(selected_source),
                    "--output",
                    str(candidate / ARTIFACT_NAMES[6]),
                    "--version",
                    EXPECTED_VERSIONS["version"],
                ),
                cwd=selected_source,
                environment=environment,
                timeout=180,
            )
            _run_tool(
                (
                    python,
                    "scripts/package_licensed_chart.py",
                    "--revision",
                    RELEASE_REVISION,
                    "--output",
                    str(candidate / ARTIFACT_NAMES[0]),
                ),
                cwd=selected_source,
                environment=environment,
                timeout=120,
            )
            _run_tool(
                (helm, "lint", str(candidate / ARTIFACT_NAMES[0])),
                cwd=selected_source,
                environment=environment,
                timeout=60,
            )

            _archive(
                selected_source,
                environment,
                candidate / ARTIFACT_NAMES[1],
                "infra-intelligence-contracts-0.84.2",
                RELEASE_REVISION,
                "LICENSE",
                "NOTICE",
                "contracts",
                "api/openapi",
                "docs/specifications",
            )
            _archive(
                selected_source,
                environment,
                candidate / ARTIFACT_NAMES[5],
                "infra-intelligence-pilot-handoff-0.84.2",
                RELEASE_REVISION,
                "LICENSE",
                "NOTICE",
                "SECURITY.md",
                "SUPPORT.md",
                "docs",
            )
            _archive(
                selected_source,
                environment,
                candidate / ARTIFACT_NAMES[2],
                "infra-intelligence-sdk-0.84.0",
                f"{RELEASE_REVISION}:sdks/python",
            )
            _archive(
                selected_source,
                environment,
                candidate / ARTIFACT_NAMES[4],
                "iip-opentelemetry-aws-bedrock-0.1.0",
                f"{RELEASE_REVISION}:instrumentation/python/aws-bedrock",
            )

            npm_state = tools / "npm"
            npm_cache = npm_state / "cache"
            npm_cache.mkdir(parents=True, mode=0o700)
            user_config = npm_state / "userconfig"
            global_config = npm_state / "globalconfig"
            user_config.write_text("", encoding="utf-8")
            global_config.write_text("", encoding="utf-8")
            user_config.chmod(0o600)
            global_config.chmod(0o600)
            npm_environment = {
                **environment,
                "NPM_CONFIG_USERCONFIG": str(user_config),
                "NPM_CONFIG_GLOBALCONFIG": str(global_config),
                "NPM_CONFIG_CACHE": str(npm_cache),
                "NPM_CONFIG_IGNORE_SCRIPTS": "true",
                "NPM_CONFIG_AUDIT": "false",
                "NPM_CONFIG_FUND": "false",
            }
            typescript = tools / "typescript-source"
            _materialize_typescript_source(
                selected_source, environment, typescript
            )
            _run_tool(
                (npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"),
                cwd=typescript,
                environment=npm_environment,
                timeout=300,
            )
            _run_tool(
                (npm, "run", "build"),
                cwd=typescript,
                environment=npm_environment,
                timeout=120,
            )
            _run_tool(
                (npm, "pack", "--pack-destination", str(candidate)),
                cwd=typescript,
                environment=npm_environment,
                timeout=120,
            )

            _validate_inventory(candidate)
            _clean_source_identity(selected_source, environment)
            _publish(candidate, selected_output)
    return metadata


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--npm", default="npm")
    parser.add_argument("--helm", default="helm")
    arguments = parser.parse_args(argv)
    try:
        metadata = build_source_assets(
            arguments.source,
            arguments.output,
            python=arguments.python,
            npm=arguments.npm,
            helm=arguments.helm,
        )
    except SourceAssetRecoveryError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
