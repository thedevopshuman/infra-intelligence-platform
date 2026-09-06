from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.release_bundle import (
    IN_TOTO,
    OCI_INDEX,
    OCI_MANIFEST,
    REQUIRED_PILOT_HANDOFF_PATHS,
    REQUIRED_PREDICATES,
    ReleaseBundleError,
    finalize_bundle,
    inspect_oci_image,
    verify_bundle,
)


VERSION = "0.84.0"
CHART_VERSION = "0.87.0"
SDK_VERSION = "0.66.0"
BEDROCK_INSTRUMENTATION_VERSION = "0.1.0"
REVISION = "0123456789abcdef0123456789abcdef01234567"
SOURCE_DATE = "2026-08-17T04:45:00+00:00"
PLATFORMS = ("linux/amd64", "linux/arm64")


def canonical(document: Any) -> bytes:
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def write_oci_fixture(
    path: Path,
    *,
    include_sbom: bool = True,
    marker: str = "release-bundle-fixture",
) -> None:
    blobs: dict[str, bytes] = {}

    def store(document: Any) -> tuple[str, int]:
        content = canonical(document)
        identifier = digest(content)
        blobs[identifier] = content
        return identifier, len(content)

    empty_digest, empty_size = store({})
    descriptors = []
    for platform in PLATFORMS:
        os_name, architecture = platform.split("/")
        config_digest, config_size = store(
            {
                "architecture": architecture,
                "os": os_name,
                "config": {},
                "rootfs": {"type": "layers", "diff_ids": []},
            }
        )
        image_digest, image_size = store(
            {
                "schemaVersion": 2,
                "mediaType": OCI_MANIFEST,
                "config": {
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": config_digest,
                    "size": config_size,
                },
                "layers": [],
                "annotations": {
                    "fixture.platform": platform,
                    "fixture.marker": marker,
                },
            }
        )
        descriptors.append(
            {
                "mediaType": OCI_MANIFEST,
                "digest": image_digest,
                "size": image_size,
                "platform": {"os": os_name, "architecture": architecture},
            }
        )
        layers = []
        for predicate in sorted(REQUIRED_PREDICATES):
            if predicate.endswith("Document") and not include_sbom:
                continue
            statement_digest, statement_size = store(
                {
                    "_type": "https://in-toto.io/Statement/v1",
                    "predicateType": predicate,
                    "subject": [],
                    "predicate": {"fixturePlatform": platform},
                }
            )
            layers.append(
                {
                    "mediaType": IN_TOTO,
                    "digest": statement_digest,
                    "size": statement_size,
                    "annotations": {"in-toto.io/predicate-type": predicate},
                }
            )
        attestation_digest, attestation_size = store(
            {
                "schemaVersion": 2,
                "mediaType": OCI_MANIFEST,
                "config": {
                    "mediaType": "application/vnd.oci.empty.v1+json",
                    "digest": empty_digest,
                    "size": empty_size,
                },
                "layers": layers,
                "subject": {
                    "mediaType": OCI_MANIFEST,
                    "digest": image_digest,
                    "size": image_size,
                },
            }
        )
        descriptors.append(
            {
                "mediaType": OCI_MANIFEST,
                "digest": attestation_digest,
                "size": attestation_size,
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": image_digest,
                },
            }
        )
    index_digest, index_size = store(
        {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": descriptors}
    )
    root = canonical(
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX,
            "manifests": [
                {
                    "mediaType": OCI_INDEX,
                    "digest": index_digest,
                    "size": index_size,
                }
            ],
        }
    )

    with tarfile.open(path, mode="w") as archive:
        for name, content in {
            "index.json": root,
            "oci-layout": canonical({"imageLayoutVersion": "1.0.0"}),
            **{
                f"blobs/sha256/{identifier.removeprefix('sha256:')}": content
                for identifier, content in blobs.items()
            },
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))


def write_pilot_handoff_fixture(
    path: Path,
    *,
    paths: set[str] | None = None,
    add_symlink: bool = False,
) -> None:
    selected = paths if paths is not None else set(REQUIRED_PILOT_HANDOFF_PATHS)
    prefix = f"infra-intelligence-pilot-handoff-{VERSION}"
    with tarfile.open(path, mode="w:gz") as archive:
        for relative in sorted(selected):
            content = f"fixture:{relative}\n".encode()
            member = tarfile.TarInfo(f"{prefix}/{relative}")
            member.size = len(content)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))
        if add_symlink:
            member = tarfile.TarInfo(f"{prefix}/docs/latest")
            member.type = tarfile.SYMTYPE
            member.linkname = "operations/private-pilot-onboarding.md"
            archive.addfile(member)


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.bundle = Path(self.temporary.name)
        write_oci_fixture(
            self.bundle / f"infra-intelligence-control-plane-{VERSION}.oci.tar"
        )
        write_oci_fixture(
            self.bundle
            / f"infra-intelligence-plugin-mediation-bridge-{VERSION}.oci.tar"
        )
        for filename in (
            f"infra-intelligence-{CHART_VERSION}.tgz",
            f"infra-intelligence-contracts-{VERSION}.tar.gz",
            f"infra-intelligence-sdk-{SDK_VERSION}.tar.gz",
            f"iip-sdk-{SDK_VERSION}.tgz",
            "iip-opentelemetry-aws-bedrock-"
            f"{BEDROCK_INSTRUMENTATION_VERSION}.tar.gz",
        ):
            (self.bundle / filename).write_bytes(f"fixture:{filename}".encode())
        write_pilot_handoff_fixture(
            self.bundle / f"infra-intelligence-pilot-handoff-{VERSION}.tar.gz"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def finalize(self) -> None:
        finalize_bundle(
            self.bundle,
            version=VERSION,
            chart_version=CHART_VERSION,
            python_sdk_version=SDK_VERSION,
            typescript_sdk_version=SDK_VERSION,
            bedrock_instrumentation_version=BEDROCK_INSTRUMENTATION_VERSION,
            revision=REVISION,
            source_date=SOURCE_DATE,
            platforms=PLATFORMS,
        )

    def test_finalized_bundle_binds_artifacts_and_attestations(self) -> None:
        self.finalize()

        manifest = verify_bundle(self.bundle)

        self.assertEqual(manifest["metadata"]["signatureStatus"], "unsigned")
        self.assertEqual(
            [item["name"] for item in manifest["spec"]["image"]["platforms"]],
            list(PLATFORMS),
        )
        self.assertEqual(
            [
                item["name"]
                for item in manifest["spec"]["pluginMediationBridgeImage"][
                    "platforms"
                ]
            ],
            list(PLATFORMS),
        )
        self.assertEqual(len(manifest["spec"]["artifacts"]), 8)
        self.assertIn(
            "private-pilot-operating-handoff",
            [item["role"] for item in manifest["spec"]["artifacts"]],
        )
        self.assertEqual(
            manifest["metadata"]["bedrockInstrumentationVersion"],
            BEDROCK_INSTRUMENTATION_VERSION,
        )

    def test_current_release_requires_mediation_bridge_metadata(self) -> None:
        self.finalize()
        manifest_path = self.bundle / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["spec"].pop("pluginMediationBridgeImage")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ReleaseBundleError, "release.manifest.invalid"):
            verify_bundle(self.bundle)

    def test_current_release_requires_pilot_handoff(self) -> None:
        self.finalize()
        manifest_path = self.bundle / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["spec"]["artifacts"] = [
            artifact
            for artifact in manifest["spec"]["artifacts"]
            if artifact["role"] != "private-pilot-operating-handoff"
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ReleaseBundleError, "release.manifest.invalid"):
            verify_bundle(self.bundle)

    def test_modified_artifact_fails_digest_verification(self) -> None:
        self.finalize()
        artifact = self.bundle / f"infra-intelligence-contracts-{VERSION}.tar.gz"
        artifact.write_bytes(b"substituted")

        with self.assertRaisesRegex(
            ReleaseBundleError, "release.artifact.(size|digest)-mismatch"
        ):
            verify_bundle(self.bundle)

    def test_image_without_sbom_fails_closed(self) -> None:
        image = self.bundle / f"infra-intelligence-control-plane-{VERSION}.oci.tar"
        write_oci_fixture(image, include_sbom=False)

        with self.assertRaisesRegex(
            ReleaseBundleError, "release.image.attestation.required"
        ):
            inspect_oci_image(image)

    def test_mediation_bridge_without_sbom_fails_closed(self) -> None:
        image = (
            self.bundle
            / f"infra-intelligence-plugin-mediation-bridge-{VERSION}.oci.tar"
        )
        write_oci_fixture(image, include_sbom=False)

        with self.assertRaisesRegex(
            ReleaseBundleError, "release.image.attestation.required"
        ):
            self.finalize()

    def test_pilot_handoff_requires_every_operating_document(self) -> None:
        handoff = (
            self.bundle / f"infra-intelligence-pilot-handoff-{VERSION}.tar.gz"
        )
        write_pilot_handoff_fixture(
            handoff,
            paths=set(REQUIRED_PILOT_HANDOFF_PATHS)
            - {"docs/operations/private-pilot-onboarding.md"},
        )

        with self.assertRaisesRegex(
            ReleaseBundleError, "release.pilot-handoff.required-file-missing"
        ):
            self.finalize()

    def test_pilot_handoff_rejects_links(self) -> None:
        handoff = (
            self.bundle / f"infra-intelligence-pilot-handoff-{VERSION}.tar.gz"
        )
        write_pilot_handoff_fixture(handoff, add_symlink=True)

        with self.assertRaisesRegex(
            ReleaseBundleError, "release.pilot-handoff.invalid"
        ):
            self.finalize()


if __name__ == "__main__":
    unittest.main()
