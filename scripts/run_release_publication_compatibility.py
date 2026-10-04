#!/usr/bin/env python3
"""Prove exact OCI-layout publication against an isolated local registry."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import release_bundle  # noqa: E402
import release_publication  # noqa: E402
from tests.test_release_bundle import (  # noqa: E402
    write_oci_fixture,
    write_pilot_handoff_fixture,
)


REGISTRY_IMAGE = (
    "registry@sha256:"
    "a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
)
SDK_VERSION = "0.66.0"
BEDROCK_INSTRUMENTATION_VERSION = "0.1.0"


def _run(command: tuple[str, ...], *, capture: bool = False) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=180,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise release_publication.ReleasePublicationError(
            "release-publication.compatibility.command-failed"
        ) from None
    return completed.stdout.strip() if capture else ""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_registry(port: int) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{port}/v2/", timeout=1) as response:
                if response.status == 200 and response.read(1024) == b"{}":
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.2)
    raise release_publication.ReleasePublicationError(
        "release-publication.compatibility.registry-unavailable"
    )


def _versions() -> tuple[str, str]:
    application = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    for line in (ROOT / "deploy/helm/infra-intelligence/Chart.yaml").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.startswith("version:"):
            return str(application), line.split(":", 1)[1].strip()
    raise release_publication.ReleasePublicationError(
        "release-publication.compatibility.version-invalid"
    )


def _fixture_bundle(directory: Path) -> tuple[Path, str]:
    version, chart_version = _versions()
    revision, _ = release_publication.source_identity()
    bundle = directory / "bundle"
    bundle.mkdir()
    for role in ("control-plane", "plugin-mediation-bridge"):
        write_oci_fixture(
            bundle / f"infra-intelligence-{role}-{version}.oci.tar",
            marker=role,
        )
    for filename in (
        f"infra-intelligence-{chart_version}.tgz",
        f"infra-intelligence-contracts-{version}.tar.gz",
        f"infra-intelligence-sdk-{SDK_VERSION}.tar.gz",
        f"iip-sdk-{SDK_VERSION}.tgz",
        "iip-opentelemetry-aws-bedrock-"
        f"{BEDROCK_INSTRUMENTATION_VERSION}.tar.gz",
    ):
        (bundle / filename).write_bytes(filename.encode("ascii"))
    write_pilot_handoff_fixture(
        bundle / f"infra-intelligence-pilot-handoff-{version}.tar.gz",
        version=version,
    )
    source_date = _run(
        ("git", "show", "-s", "--format=%cI", revision),
        capture=True,
    )
    release_bundle.finalize_bundle(
        bundle,
        version=version,
        chart_version=chart_version,
        python_sdk_version=SDK_VERSION,
        typescript_sdk_version=SDK_VERSION,
        bedrock_instrumentation_version=BEDROCK_INSTRUMENTATION_VERSION,
        revision=revision,
        source_date=source_date,
        platforms=("linux/amd64", "linux/arm64"),
    )
    return bundle, version


def run(docker: str) -> None:
    port = _free_port()
    container = f"iip-release-publication-{os.getpid()}"
    if _container_exists(docker, container):
        _run((docker, "rm", "-f", container))
    with tempfile.TemporaryDirectory(prefix="iip-release-publication-") as temporary:
        directory = Path(temporary)
        bundle, version = _fixture_bundle(directory)
        host = f"127.0.0.1:{port}"
        repositories = {
            release_publication.ROLES[0]: f"{host}/iip/control-plane",
            release_publication.ROLES[1]: f"{host}/iip/plugin-mediation-bridge",
        }
        report_path = directory / "publication.json"
        try:
            _run(
                (
                    docker,
                    "run",
                    "--rm",
                    "--detach",
                    "--name",
                    container,
                    "--publish",
                    f"127.0.0.1:{port}:5000",
                    REGISTRY_IMAGE,
                )
            )
            _wait_for_registry(port)
            publisher = release_publication.DockerBuildxPublisher(docker)
            report = release_publication.publish_candidate(
                bundle=bundle,
                repositories=repositories,
                tag=f"v{version}",
                output=report_path,
                publisher=publisher,
            )
            release_publication.validate_report(report)
            encoded = json.dumps(report, sort_keys=True)
            if any(
                prohibited in encoded.lower()
                for prohibited in ("authorization", "password", "credential", "token")
            ):
                raise release_publication.ReleasePublicationError(
                    "release-publication.compatibility.output-not-minimized"
                )

            targets = report["spec"]["targets"]  # type: ignore[index]
            control = targets[0]  # type: ignore[index]
            bridge = targets[1]  # type: ignore[index]
            _run(
                (
                    docker,
                    "buildx",
                    "imagetools",
                    "create",
                    "--tag",
                    control["tagReference"],
                    bridge["immutableReference"],
                )
            )
            try:
                publisher.publish(
                    archive=(
                        bundle
                        / f"infra-intelligence-control-plane-{version}.oci.tar"
                    ),
                    repository=control["repository"],
                    tag=f"v{version}",
                    digest=control["indexDigest"],
                )
            except release_publication.ReleasePublicationError as error:
                if str(error) != "release-publication.tag.conflict":
                    raise
            else:
                raise release_publication.ReleasePublicationError(
                    "release-publication.compatibility.tag-conflict-missed"
                )
        finally:
            if _container_exists(docker, container):
                _run((docker, "rm", "-f", container))


def _container_exists(docker: str, name: str) -> bool:
    try:
        return bool(
            _run(
                (docker, "ps", "--all", "--quiet", "--filter", f"name=^{name}$"),
                capture=True,
            )
        )
    except release_publication.ReleasePublicationError:
        return False


def main() -> int:
    docker = os.environ.get("IIP_DOCKER_BIN", "docker")
    try:
        run(docker)
    except release_publication.ReleasePublicationError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        "release publication compatibility passed: exact OCI indexes and "
        "attestations retained; conflicting version tag rejected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
