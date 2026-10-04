from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import assemble_recovered_release as recovery


class RecoveredBundleAssemblyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="iip-recovery-assembly-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        for filename in recovery.FINAL_FILES:
            (self.bundle / filename).write_bytes(b"fixture")

    def test_closed_regular_file_inventory_excludes_scratch_and_credentials(self):
        recovery.validate_inventory(self.bundle, finalized=True)
        for name in (".env", "docker-config.json", "report.json", "scratch"):
            with self.subTest(name=name):
                path = self.bundle / name
                path.write_text("credential sentinel")
                with self.assertRaisesRegex(recovery.AssemblyError, "inventory-invalid"):
                    recovery.validate_inventory(self.bundle, finalized=True)
                path.unlink()

    def test_symlink_and_hardlink_artifacts_are_rejected(self):
        path = self.bundle / recovery.IMAGE_FILES[0]
        path.unlink()
        outside = self.root / "private"
        outside.write_bytes(b"credential sentinel")
        path.symlink_to(outside)
        with self.assertRaisesRegex(recovery.AssemblyError, "regular-file-required"):
            recovery.validate_inventory(self.bundle, finalized=True)
        path.unlink()
        path.hardlink_to(outside)
        with self.assertRaisesRegex(recovery.AssemblyError, "regular-file-required"):
            recovery.validate_inventory(self.bundle, finalized=True)

    def test_existing_output_is_never_modified(self):
        with patch.object(recovery.recover_source_assets, "build_source_assets") as builder:
            with self.assertRaisesRegex(recovery.AssemblyError, "output-exists"):
                recovery.assemble(self.root, self.root, self.root, self.bundle)
            builder.assert_not_called()

    def test_verifier_requires_fixed_original_source_and_both_images(self):
        manifest = {"metadata": {**recovery.recover_source_assets.EXPECTED_VERSIONS,
                                 "revision": recovery.recover_source_assets.RELEASE_REVISION},
                    "spec": {key: {"path": filename, "indexDigest": digest, "platforms": [
                        {"name": "linux/amd64"}, {"name": "linux/arm64"}]}
                        for key, filename, digest in zip(("image", "pluginMediationBridgeImage"), recovery.IMAGE_FILES, recovery.TARGETS.values())}}
        def verified_archive(path, digest):
            key = "image" if path.name == recovery.IMAGE_FILES[0] else "pluginMediationBridgeImage"
            self.assertEqual(digest, manifest["spec"][key]["indexDigest"])
            return {name: value for name, value in manifest["spec"][key].items() if name != "path"}

        with patch.object(recovery.release_bundle, "verify_bundle", return_value=manifest), \
                patch.object(recovery.recover_oci_layout, "verify_archive", side_effect=verified_archive) as verifier:
            self.assertIs(recovery.verify_recovered_bundle(self.bundle), manifest)
            self.assertEqual(verifier.call_count, 2)
            manifest["metadata"]["revision"] = "0" * 40
            with self.assertRaisesRegex(recovery.AssemblyError, "source-mismatch"):
                recovery.verify_recovered_bundle(self.bundle)
            manifest["metadata"]["revision"] = recovery.recover_source_assets.RELEASE_REVISION
            manifest["spec"]["image"]["indexDigest"] = "sha256:" + "0" * 64
            with self.assertRaisesRegex(recovery.AssemblyError, "image-mismatch"):
                recovery.verify_recovered_bundle(self.bundle)


if __name__ == "__main__":
    unittest.main()
