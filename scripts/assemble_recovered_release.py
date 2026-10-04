#!/usr/bin/env python3
"""Reconstruct the fixed v0.84.2 bundle from original source and published blobs.

This writes new archive bytes/checksums, never a claim to reproduce the lost
runner archive. Network pulls, signatures, vulnerability checks and public
publication are separate stages. Existing outputs are never replaced.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import stat
import sys
from typing import Mapping

if __package__:
    from . import recover_source_assets, recover_oci_layout, release_bundle
    from .cosign_recovery_adapter import TARGETS
else:
    import recover_source_assets
    import recover_oci_layout
    import release_bundle
    from cosign_recovery_adapter import TARGETS


IMAGE_FILES = (
    "infra-intelligence-control-plane-0.84.2.oci.tar",
    "infra-intelligence-plugin-mediation-bridge-0.84.2.oci.tar",
)
ARTIFACT_FILES = frozenset((*recover_source_assets.ARTIFACT_NAMES, *IMAGE_FILES))
FINAL_FILES = ARTIFACT_FILES | {"release-manifest.json", "SHA256SUMS"}


class AssemblyError(ValueError):
    """Stable local assembly failure without operator/provider content."""


def validate_inventory(directory: Path, *, finalized: bool) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise AssemblyError("release-recovery.bundle.directory-invalid")
    entries = tuple(directory.iterdir())
    expected = FINAL_FILES if finalized else ARTIFACT_FILES
    if {path.name for path in entries} != expected:
        raise AssemblyError("release-recovery.bundle.inventory-invalid")
    for path in entries:
        status = path.lstat()
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or status.st_size < 1:
            raise AssemblyError("release-recovery.bundle.regular-file-required")


def verify_recovered_bundle(bundle: Path) -> Mapping:
    validate_inventory(bundle, finalized=True)
    manifest = release_bundle.verify_bundle(bundle)
    metadata, spec = manifest["metadata"], manifest["spec"]
    expected = recover_source_assets.EXPECTED_VERSIONS
    if (any(metadata.get(key) != value for key, value in expected.items())
            or metadata.get("revision") != recover_source_assets.RELEASE_REVISION):
        raise AssemblyError("release-recovery.bundle.source-mismatch")
    for key, filename, digest in zip(("image", "pluginMediationBridgeImage"), IMAGE_FILES, TARGETS.values()):
        if (spec[key]["indexDigest"] != digest
                or [item["name"] for item in spec[key]["platforms"]] != ["linux/amd64", "linux/arm64"]):
            raise AssemblyError("release-recovery.bundle.image-mismatch")
        # Outer checksums alone are not evidence that embedded layers/configs
        # still match the immutable registry index after a checkpoint reload.
        recovered = recover_oci_layout.verify_archive(bundle / filename, digest)
        if {**recovered, "path": filename} != spec[key]:
            raise AssemblyError("release-recovery.bundle.image-mismatch")
    return manifest


def assemble(source: Path, control_layout: Path, bridge_layout: Path, output: Path,
             *, python: str = sys.executable, npm: str = "npm", helm: str = "helm") -> Mapping:
    if output.exists() or output.is_symlink():
        raise AssemblyError("release-recovery.bundle.output-exists")
    if not control_layout.is_dir() or not bridge_layout.is_dir():
        raise AssemblyError("release-recovery.bundle.layout-required")
    metadata = recover_source_assets.build_source_assets(source, output, python, npm, helm)
    for layout, filename, digest in zip((control_layout, bridge_layout), IMAGE_FILES, TARGETS.values()):
        recover_oci_layout.recover_layout(layout, digest, output / filename)
    validate_inventory(output, finalized=False)
    release_bundle.finalize_bundle(output, **metadata)
    return verify_recovered_bundle(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("assemble")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--control-layout", type=Path, required=True)
    build.add_argument("--bridge-layout", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--python", default=sys.executable)
    build.add_argument("--npm", default="npm")
    build.add_argument("--helm", default="helm")
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "assemble":
            assemble(args.source, args.control_layout, args.bridge_layout, args.output,
                     python=args.python, npm=args.npm, helm=args.helm)
        else:
            verify_recovered_bundle(args.bundle)
    except (AssemblyError, recover_source_assets.SourceAssetRecoveryError,
            recover_oci_layout.RecoveryError, release_bundle.ReleaseBundleError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError):
        print("release-recovery.bundle.local-input-invalid", file=sys.stderr)
        return 2
    print("Recovered v0.84.2 bundle verified; new archive checksums, original source and image identities.")
    print("Not release approval: original-image signature and fresh vulnerability qualification remain separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
