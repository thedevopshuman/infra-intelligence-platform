from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from scripts.write_postgres_tls_fixture import write_fixture


class PostgresTlsFixtureTests(unittest.TestCase):
    def test_fixture_is_readable_by_container_postgres_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            directory.chmod(0o700)

            write_fixture(directory)

            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
            for fixture in (directory, directory / "untrusted"):
                self.assertEqual(stat.S_IMODE(fixture.stat().st_mode), 0o755)
                for name in ("ca.crt", "server.crt", "server.key"):
                    leaf = fixture / name
                    self.assertTrue(leaf.is_file())
                    self.assertEqual(stat.S_IMODE(leaf.stat().st_mode), 0o644)

    def test_nonempty_directory_is_rejected_without_relaxing_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            directory.chmod(0o700)
            existing = directory / "existing"
            existing.write_text("existing content", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "directory must be empty"):
                write_fixture(directory)

            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(existing.read_text(encoding="utf-8"), "existing content")
            self.assertEqual(list(directory.iterdir()), [existing])


if __name__ == "__main__":
    unittest.main()
