#!/usr/bin/env python3
"""Finalize and verify an unsigned, attested local release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


SPDX_PREDICATE = "https://spdx.dev/Document"
SLSA_PREDICATE = "https://slsa.dev/provenance/v1"
REQUIRED_PREDICATES = frozenset({SPDX_PREDICATE, SLSA_PREDICATE})
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
IN_TOTO = "application/vnd.in-toto+json"
HEX_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
REVISION = re.compile(r"^[0-9a-f]{40,64}$")
MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_PILOT_HANDOFF_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_PILOT_HANDOFF_CONTENT_BYTES = 64 * 1024 * 1024
MAX_PILOT_HANDOFF_MEMBERS = 4096
REQUIRED_PILOT_HANDOFF_PATHS = frozenset(
    {
        "SECURITY.md",
        "SUPPORT.md",
        "docs/product/private-pilot-v1.md",
        "docs/operations/private-pilot-onboarding.md",
        "docs/operations/private-pilot-feedback.md",
        "docs/operations/deployment-diagnostics.md",
        "docs/operations/customer-pilot-readiness.md",
        "docs/operations/release-artifacts.md",
        "docs/operations/helm-deployment.md",
    }
)


class ReleaseBundleError(RuntimeError):
    """A stable release-bundle validation failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_member(archive: tarfile.TarFile, name: str) -> Mapping[str, Any]:
    try:
        member = archive.getmember(name)
    except KeyError:
        raise ReleaseBundleError("release.image.oci.invalid") from None
    if not member.isfile() or member.size > MAX_METADATA_BYTES:
        raise ReleaseBundleError("release.image.oci.invalid")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ReleaseBundleError("release.image.oci.invalid")
    try:
        document = json.load(extracted)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseBundleError("release.image.oci.invalid") from None
    if not isinstance(document, dict):
        raise ReleaseBundleError("release.image.oci.invalid")
    return document


def _blob(archive: tarfile.TarFile, digest: object) -> Mapping[str, Any]:
    if not isinstance(digest, str) or HEX_DIGEST.fullmatch(digest) is None:
        raise ReleaseBundleError("release.image.oci.invalid")
    return _json_member(archive, f"blobs/sha256/{digest.removeprefix('sha256:')}")


def inspect_oci_image(path: Path) -> Mapping[str, Any]:
    """Require one OCI image index with SPDX and SLSA attestations per platform."""

    try:
        archive = tarfile.open(path, mode="r:*")
    except (OSError, tarfile.TarError):
        raise ReleaseBundleError("release.image.oci.invalid") from None

    with archive:
        root = _json_member(archive, "index.json")
        root_manifests = root.get("manifests")
        if root.get("schemaVersion") != 2 or not isinstance(root_manifests, list):
            raise ReleaseBundleError("release.image.oci.invalid")
        if len(root_manifests) != 1:
            raise ReleaseBundleError("release.image.index.invalid")
        root_descriptor = root_manifests[0]
        if not isinstance(root_descriptor, dict):
            raise ReleaseBundleError("release.image.index.invalid")
        index_digest = root_descriptor.get("digest")
        if root_descriptor.get("mediaType") != OCI_INDEX or not isinstance(
            index_digest, str
        ):
            raise ReleaseBundleError("release.image.index.invalid")

        images: dict[str, str] = {}
        predicates: dict[str, set[str]] = {}
        seen: set[str] = set()

        def walk(descriptor: Mapping[str, Any]) -> None:
            digest = descriptor.get("digest")
            media_type = descriptor.get("mediaType")
            if not isinstance(digest, str) or digest in seen:
                raise ReleaseBundleError("release.image.oci.invalid")
            seen.add(digest)
            document = _blob(archive, digest)
            if media_type == OCI_INDEX:
                children = document.get("manifests")
                if not isinstance(children, list) or not children:
                    raise ReleaseBundleError("release.image.index.invalid")
                for child in children:
                    if not isinstance(child, dict):
                        raise ReleaseBundleError("release.image.index.invalid")
                    walk(child)
                return
            if media_type != OCI_MANIFEST:
                raise ReleaseBundleError("release.image.oci.invalid")

            annotations = descriptor.get("annotations", {})
            if not isinstance(annotations, dict):
                raise ReleaseBundleError("release.image.oci.invalid")
            if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
                reference = annotations.get("vnd.docker.reference.digest")
                if not isinstance(reference, str) or HEX_DIGEST.fullmatch(reference) is None:
                    raise ReleaseBundleError("release.image.attestation.invalid")
                layers = document.get("layers")
                if not isinstance(layers, list) or not layers:
                    raise ReleaseBundleError("release.image.attestation.invalid")
                found = predicates.setdefault(reference, set())
                for layer in layers:
                    if not isinstance(layer, dict) or layer.get("mediaType") != IN_TOTO:
                        continue
                    statement = _blob(archive, layer.get("digest"))
                    predicate = statement.get("predicateType")
                    if statement.get("_type") != "https://in-toto.io/Statement/v1":
                        raise ReleaseBundleError("release.image.attestation.invalid")
                    if isinstance(predicate, str):
                        found.add(predicate)
                return

            platform = descriptor.get("platform")
            if not isinstance(platform, dict):
                raise ReleaseBundleError("release.image.platform.invalid")
            operating_system = platform.get("os")
            architecture = platform.get("architecture")
            variant = platform.get("variant")
            if (
                operating_system != "linux"
                or not isinstance(architecture, str)
                or not architecture
                or variant is not None and not isinstance(variant, str)
            ):
                raise ReleaseBundleError("release.image.platform.invalid")
            name = f"{operating_system}/{architecture}"
            if variant:
                name = f"{name}/{variant}"
            if name in images:
                raise ReleaseBundleError("release.image.platform.duplicate")
            images[name] = digest

        walk(root_descriptor)

    if not images:
        raise ReleaseBundleError("release.image.platform.required")
    for digest in images.values():
        if not REQUIRED_PREDICATES.issubset(predicates.get(digest, set())):
            raise ReleaseBundleError("release.image.attestation.required")

    return {
        "indexDigest": index_digest,
        "platforms": [
            {
                "name": name,
                "manifestDigest": images[name],
                "attestations": sorted(predicates[images[name]]),
            }
            for name in sorted(images)
        ],
    }


def inspect_pilot_handoff(path: Path, version: str) -> Mapping[str, int]:
    """Require a bounded, link-free operating-document archive for one release."""

    if (
        not path.is_file()
        or path.stat().st_size < 1
        or path.stat().st_size > MAX_PILOT_HANDOFF_ARCHIVE_BYTES
    ):
        raise ReleaseBundleError("release.pilot-handoff.invalid")
    try:
        archive = tarfile.open(path, mode="r:gz")
    except (OSError, tarfile.TarError):
        raise ReleaseBundleError("release.pilot-handoff.invalid") from None

    prefix = f"infra-intelligence-pilot-handoff-{version}"
    files: set[str] = set()
    seen: set[str] = set()
    total_bytes = 0
    with archive:
        try:
            members = archive.getmembers()
        except (OSError, tarfile.TarError):
            raise ReleaseBundleError("release.pilot-handoff.invalid") from None
        if not members or len(members) > MAX_PILOT_HANDOFF_MEMBERS:
            raise ReleaseBundleError("release.pilot-handoff.invalid")
        for member in members:
            raw_name = member.name.rstrip("/")
            parts = raw_name.split("/")
            if (
                not raw_name
                or raw_name.startswith("/")
                or "\\" in raw_name
                or any(part in {"", ".", ".."} for part in parts)
                or PurePosixPath(raw_name).parts[0] != prefix
                or raw_name in seen
                or not (member.isdir() or member.isfile())
            ):
                raise ReleaseBundleError("release.pilot-handoff.invalid")
            seen.add(raw_name)
            if member.isfile():
                total_bytes += member.size
                if total_bytes > MAX_PILOT_HANDOFF_CONTENT_BYTES:
                    raise ReleaseBundleError("release.pilot-handoff.invalid")
                relative = "/".join(parts[1:])
                if not relative:
                    raise ReleaseBundleError("release.pilot-handoff.invalid")
                if relative in REQUIRED_PILOT_HANDOFF_PATHS and member.size < 1:
                    raise ReleaseBundleError("release.pilot-handoff.invalid")
                files.add(relative)

    if not REQUIRED_PILOT_HANDOFF_PATHS.issubset(files):
        raise ReleaseBundleError("release.pilot-handoff.required-file-missing")
    return {"fileCount": len(files), "contentBytes": total_bytes}


def _validate_release_identity(
    *,
    version: str,
    chart_version: str,
    python_sdk_version: str,
    typescript_sdk_version: str,
    bedrock_instrumentation_version: str,
    revision: str,
    source_date: str,
    platforms: Sequence[str],
) -> None:
    if any(
        SEMVER.fullmatch(value) is None
        for value in (
            version,
            chart_version,
            python_sdk_version,
            typescript_sdk_version,
            bedrock_instrumentation_version,
        )
    ):
        raise ReleaseBundleError("release.version.invalid")
    if REVISION.fullmatch(revision) is None:
        raise ReleaseBundleError("release.revision.invalid")
    try:
        parsed = datetime.fromisoformat(source_date.replace("Z", "+00:00"))
    except ValueError:
        raise ReleaseBundleError("release.source-date.invalid") from None
    if parsed.tzinfo is None:
        raise ReleaseBundleError("release.source-date.invalid")
    if not platforms or len(platforms) != len(set(platforms)) or any(
        re.fullmatch(r"linux/[a-z0-9_]+(?:/[a-z0-9.]+)?", item) is None
        for item in platforms
    ):
        raise ReleaseBundleError("release.platforms.invalid")


def _artifact_specs(
    version: str,
    chart_version: str,
    python_sdk_version: str,
    typescript_sdk_version: str,
    bedrock_instrumentation_version: str,
    *,
    include_mediation_bridge: bool = True,
    include_pilot_handoff: bool = True,
) -> tuple[tuple[str, str, str], ...]:
    image_specs = [
        (
            f"infra-intelligence-control-plane-{version}.oci.tar",
            "control-plane-image",
            "application/vnd.oci.image.layout.v1.tar",
        ),
    ]
    if include_mediation_bridge:
        image_specs.append(
            (
                f"infra-intelligence-plugin-mediation-bridge-{version}.oci.tar",
                "plugin-mediation-bridge-image",
                "application/vnd.oci.image.layout.v1.tar",
            )
        )
    portable_specs = (
        (
            f"infra-intelligence-{chart_version}.tgz",
            "helm-chart",
            "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
        ),
        (
            f"infra-intelligence-contracts-{version}.tar.gz",
            "public-contracts",
            "application/gzip",
        ),
        (
            f"infra-intelligence-sdk-{python_sdk_version}.tar.gz",
            "python-sdk-source",
            "application/gzip",
        ),
        (
            f"iip-sdk-{typescript_sdk_version}.tgz",
            "typescript-sdk",
            "application/vnd.npm.package+gzip",
        ),
        (
            "iip-opentelemetry-aws-bedrock-"
            f"{bedrock_instrumentation_version}.tar.gz",
            "bedrock-otel-instrumentation-source",
            "application/gzip",
        ),
    )
    if include_pilot_handoff:
        portable_specs += (
            (
                f"infra-intelligence-pilot-handoff-{version}.tar.gz",
                "private-pilot-operating-handoff",
                "application/gzip",
            ),
        )
    return tuple(image_specs) + portable_specs


def finalize_bundle(
    bundle: Path,
    *,
    version: str,
    chart_version: str,
    python_sdk_version: str,
    typescript_sdk_version: str,
    bedrock_instrumentation_version: str,
    revision: str,
    source_date: str,
    platforms: Sequence[str],
) -> Mapping[str, Any]:
    _validate_release_identity(
        version=version,
        chart_version=chart_version,
        python_sdk_version=python_sdk_version,
        typescript_sdk_version=typescript_sdk_version,
        bedrock_instrumentation_version=bedrock_instrumentation_version,
        revision=revision,
        source_date=source_date,
        platforms=platforms,
    )
    if not bundle.is_dir():
        raise ReleaseBundleError("release.bundle.missing")

    artifacts = []
    for filename, role, media_type in _artifact_specs(
        version,
        chart_version,
        python_sdk_version,
        typescript_sdk_version,
        bedrock_instrumentation_version,
    ):
        path = bundle / filename
        if not path.is_file() or path.stat().st_size < 1:
            raise ReleaseBundleError("release.artifact.missing")
        if role == "private-pilot-operating-handoff":
            inspect_pilot_handoff(path, version)
        artifacts.append(
            {
                "path": filename,
                "role": role,
                "mediaType": media_type,
                "sizeBytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    def image_entry(filename: str) -> dict[str, Any]:
        image_path = bundle / filename
        image = dict(inspect_oci_image(image_path))
        actual_platforms = [item["name"] for item in image["platforms"]]
        if sorted(platforms) != actual_platforms:
            raise ReleaseBundleError("release.image.platform.mismatch")
        image["path"] = image_path.name
        return image

    image = image_entry(f"infra-intelligence-control-plane-{version}.oci.tar")
    mediation_bridge_image = image_entry(
        f"infra-intelligence-plugin-mediation-bridge-{version}.oci.tar"
    )

    manifest: Mapping[str, Any] = {
        "apiVersion": "iip.dev/v1alpha1",
        "kind": "ReleaseManifest",
        "metadata": {
            "version": version,
            "chartVersion": chart_version,
            "pythonSdkVersion": python_sdk_version,
            "typescriptSdkVersion": typescript_sdk_version,
            "bedrockInstrumentationVersion": bedrock_instrumentation_version,
            "revision": revision,
            "sourceDate": source_date,
            "signatureStatus": "unsigned",
        },
        "spec": {
            "artifacts": artifacts,
            "image": image,
            "pluginMediationBridgeImage": mediation_bridge_image,
        },
    }
    manifest_path = bundle / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksum_paths = [bundle / item["path"] for item in artifacts] + [manifest_path]
    checksum_lines = [
        f"{sha256_file(path)}  {path.name}" for path in sorted(checksum_paths)
    ]
    (bundle / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )
    return manifest


def _expected_checksum_lines(bundle: Path, manifest: Mapping[str, Any]) -> list[str]:
    spec = manifest.get("spec")
    if not isinstance(spec, dict) or not isinstance(spec.get("artifacts"), list):
        raise ReleaseBundleError("release.manifest.invalid")
    paths = []
    for artifact in spec["artifacts"]:
        if not isinstance(artifact, dict):
            raise ReleaseBundleError("release.manifest.invalid")
        name = artifact.get("path")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in {"release-manifest.json", "SHA256SUMS"}
        ):
            raise ReleaseBundleError("release.manifest.invalid")
        path = bundle / name
        if not path.is_file():
            raise ReleaseBundleError("release.artifact.missing")
        if artifact.get("sizeBytes") != path.stat().st_size:
            raise ReleaseBundleError("release.artifact.size-mismatch")
        if artifact.get("sha256") != sha256_file(path):
            raise ReleaseBundleError("release.artifact.digest-mismatch")
        paths.append(path)
    paths.append(bundle / "release-manifest.json")
    return [f"{sha256_file(path)}  {path.name}" for path in sorted(paths)]


def verify_bundle(bundle: Path) -> Mapping[str, Any]:
    manifest_path = bundle / "release-manifest.json"
    checksums_path = bundle / "SHA256SUMS"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        checksum_lines = checksums_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseBundleError("release.manifest.invalid") from None
    if not isinstance(manifest, dict):
        raise ReleaseBundleError("release.manifest.invalid")
    metadata = manifest.get("metadata")
    spec = manifest.get("spec")
    if (
        manifest.get("apiVersion") != "iip.dev/v1alpha1"
        or manifest.get("kind") != "ReleaseManifest"
        or not isinstance(metadata, dict)
        or not isinstance(spec, dict)
        or metadata.get("signatureStatus") != "unsigned"
    ):
        raise ReleaseBundleError("release.manifest.invalid")
    identity_fields = (
        "version",
        "chartVersion",
        "pythonSdkVersion",
        "typescriptSdkVersion",
        "bedrockInstrumentationVersion",
        "revision",
        "sourceDate",
    )
    if any(not isinstance(metadata.get(field), str) for field in identity_fields):
        raise ReleaseBundleError("release.manifest.invalid")
    image = spec.get("image")
    if not isinstance(image, dict) or not isinstance(image.get("path"), str):
        raise ReleaseBundleError("release.manifest.invalid")
    declared_platforms = image.get("platforms")
    if not isinstance(declared_platforms, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str)
        for item in declared_platforms
    ):
        raise ReleaseBundleError("release.manifest.invalid")
    _validate_release_identity(
        version=metadata["version"],
        chart_version=metadata["chartVersion"],
        python_sdk_version=metadata["pythonSdkVersion"],
        typescript_sdk_version=metadata["typescriptSdkVersion"],
        bedrock_instrumentation_version=metadata["bedrockInstrumentationVersion"],
        revision=metadata["revision"],
        source_date=metadata["sourceDate"],
        platforms=tuple(item["name"] for item in declared_platforms),
    )
    artifacts = spec.get("artifacts")
    if not isinstance(artifacts, list):
        raise ReleaseBundleError("release.manifest.invalid")
    bridge_image = spec.get("pluginMediationBridgeImage")
    bridge_declared = "pluginMediationBridgeImage" in spec
    handoff_declared = any(
        isinstance(artifact, dict)
        and artifact.get("role") == "private-pilot-operating-handoff"
        for artifact in artifacts
    )
    version_match = re.match(
        r"^([0-9]+)\.([0-9]+)\.([0-9]+)", metadata["version"]
    )
    assert version_match is not None
    bridge_required = tuple(int(part) for part in version_match.groups()) >= (0, 41, 0)
    handoff_required = tuple(int(part) for part in version_match.groups()) >= (0, 84, 0)
    if bridge_required and not bridge_declared:
        raise ReleaseBundleError("release.manifest.invalid")
    if handoff_required and not handoff_declared:
        raise ReleaseBundleError("release.manifest.invalid")
    if bridge_declared and (
        not isinstance(bridge_image, dict)
        or not isinstance(bridge_image.get("path"), str)
        or not isinstance(bridge_image.get("platforms"), list)
    ):
        raise ReleaseBundleError("release.manifest.invalid")
    expected_specs = _artifact_specs(
        metadata["version"],
        metadata["chartVersion"],
        metadata["pythonSdkVersion"],
        metadata["typescriptSdkVersion"],
        metadata["bedrockInstrumentationVersion"],
        include_mediation_bridge=bridge_declared,
        include_pilot_handoff=handoff_declared,
    )
    expected_artifacts = {
        filename: (role, media_type)
        for filename, role, media_type in expected_specs
    }
    declared_artifacts = {
        artifact.get("path"): (artifact.get("role"), artifact.get("mediaType"))
        for artifact in artifacts
        if isinstance(artifact, dict)
    }
    if len(declared_artifacts) != len(artifacts) or declared_artifacts != expected_artifacts:
        raise ReleaseBundleError("release.manifest.invalid")
    expected_image_path = f"infra-intelligence-control-plane-{metadata['version']}.oci.tar"
    if image["path"] != expected_image_path:
        raise ReleaseBundleError("release.manifest.invalid")
    if bridge_declared:
        assert isinstance(bridge_image, dict)
        expected_bridge_path = (
            "infra-intelligence-plugin-mediation-bridge-"
            f"{metadata['version']}.oci.tar"
        )
        if bridge_image["path"] != expected_bridge_path:
            raise ReleaseBundleError("release.manifest.invalid")
        bridge_platforms = bridge_image["platforms"]
        if (
            any(
                not isinstance(item, dict) or not isinstance(item.get("name"), str)
                for item in bridge_platforms
            )
            or [item["name"] for item in bridge_platforms]
            != [item["name"] for item in declared_platforms]
        ):
            raise ReleaseBundleError("release.image.platform.mismatch")
    if handoff_declared:
        inspect_pilot_handoff(
            bundle
            / f"infra-intelligence-pilot-handoff-{metadata['version']}.tar.gz",
            metadata["version"],
        )
    expected_lines = _expected_checksum_lines(bundle, manifest)
    if checksum_lines != expected_lines:
        raise ReleaseBundleError("release.checksums.invalid")

    inspected = inspect_oci_image(bundle / image["path"])
    if image.get("indexDigest") != inspected["indexDigest"] or image.get(
        "platforms"
    ) != inspected["platforms"]:
        raise ReleaseBundleError("release.image.metadata-mismatch")
    if bridge_declared:
        assert isinstance(bridge_image, dict)
        inspected_bridge = inspect_oci_image(bundle / bridge_image["path"])
        if bridge_image.get("indexDigest") != inspected_bridge[
            "indexDigest"
        ] or bridge_image.get("platforms") != inspected_bridge["platforms"]:
            raise ReleaseBundleError("release.image.metadata-mismatch")
    return manifest


def _platforms(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("bundle", type=Path)
    finalize.add_argument("--version", required=True)
    finalize.add_argument("--chart-version", required=True)
    finalize.add_argument("--python-sdk-version", required=True)
    finalize.add_argument("--typescript-sdk-version", required=True)
    finalize.add_argument("--bedrock-instrumentation-version", required=True)
    finalize.add_argument("--revision", required=True)
    finalize.add_argument("--source-date", required=True)
    finalize.add_argument("--platforms", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    arguments = parser.parse_args(tuple(argv) if argv is not None else None)
    try:
        if arguments.command == "finalize":
            finalize_bundle(
                arguments.bundle,
                version=arguments.version,
                chart_version=arguments.chart_version,
                python_sdk_version=arguments.python_sdk_version,
                typescript_sdk_version=arguments.typescript_sdk_version,
                bedrock_instrumentation_version=(
                    arguments.bedrock_instrumentation_version
                ),
                revision=arguments.revision,
                source_date=arguments.source_date,
                platforms=_platforms(arguments.platforms),
            )
        else:
            verify_bundle(arguments.bundle)
    except ReleaseBundleError as exc:
        parser.exit(1, f"{exc}\n")
    print(f"release bundle {arguments.command} passed: {arguments.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
