"""Keep the selected license intact across independently packaged boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGES = (
    ROOT,
    ROOT / "sdks/python",
    ROOT / "plugins/examples/kubernetes-observer",
    ROOT / "instrumentation/python/aws-bedrock",
)
STANDALONE_PACKAGES = (*PYTHON_PACKAGES[1:], ROOT / "sdks/typescript")
OFFICIAL_APACHE_2_SHA256 = (
    "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
)


class LicensePackagingTests(unittest.TestCase):
    def test_canonical_license_matches_official_apache_text(self) -> None:
        # Exact https://www.apache.org/licenses/LICENSE-2.0.txt; retain the
        # appendix placeholders because this is the unmodified license text.
        self.assertEqual(
            hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest(),
            OFFICIAL_APACHE_2_SHA256,
        )

    def test_notice_identifies_project_contributors(self) -> None:
        self.assertEqual(
            (ROOT / "NOTICE").read_text(encoding="utf-8"),
            "Infrastructure Intelligence Platform\n"
            "Copyright 2026 Infrastructure Intelligence Platform contributors\n",
        )

    def test_standalone_packages_have_exact_license_and_notice_copies(self) -> None:
        for package in STANDALONE_PACKAGES:
            for filename in ("LICENSE", "NOTICE"):
                with self.subTest(package=str(package.relative_to(ROOT)), file=filename):
                    self.assertEqual(
                        (package / filename).read_bytes(),
                        (ROOT / filename).read_bytes(),
                    )

    def test_python_metadata_declares_license_and_bundles_both_files(self) -> None:
        for package in PYTHON_PACKAGES:
            with self.subTest(package=str(package.relative_to(ROOT))):
                metadata = tomllib.loads(
                    (package / "pyproject.toml").read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["project"]["license"], {"text": "Apache-2.0"})
                self.assertEqual(
                    metadata["tool"]["setuptools"]["license-files"],
                    ["LICENSE", "NOTICE"],
                )

    def test_typescript_metadata_and_lock_agree_and_bundle_both_files(self) -> None:
        package = ROOT / "sdks/typescript"
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((package / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["license"], "Apache-2.0")
        self.assertEqual(lock["packages"][""]["license"], metadata["license"])
        self.assertIn("LICENSE", metadata["files"])
        self.assertIn("NOTICE", metadata["files"])


if __name__ == "__main__":
    unittest.main()
