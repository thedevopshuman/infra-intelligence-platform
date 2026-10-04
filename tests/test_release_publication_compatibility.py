from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_release_publication_compatibility as compatibility


class ReleasePublicationCompatibilityFixtureTests(unittest.TestCase):
    def test_fixture_bundle_includes_inspected_exact_version_handoff(self) -> None:
        for version in ("0.84.0", "0.85.0"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                with (
                    patch.object(
                        compatibility, "_versions", return_value=(version, "0.87.0")
                    ),
                    patch.object(
                        compatibility.release_publication,
                        "source_identity",
                        return_value=("0123456789abcdef0123456789abcdef01234567", False),
                    ),
                    patch.object(
                        compatibility, "_run", return_value="2026-10-04T06:00:00Z"
                    ),
                ):
                    bundle, actual_version = compatibility._fixture_bundle(Path(temp))

                self.assertEqual(actual_version, version)
                manifest = compatibility.release_bundle.verify_bundle(bundle)
                handoff = [
                    artifact
                    for artifact in manifest["spec"]["artifacts"]
                    if artifact["role"] == "private-pilot-operating-handoff"
                ]
                self.assertEqual(len(handoff), 1)
                self.assertEqual(
                    handoff[0]["path"],
                    f"infra-intelligence-pilot-handoff-{version}.tar.gz",
                )


if __name__ == "__main__":
    unittest.main()
