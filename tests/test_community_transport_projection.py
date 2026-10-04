"""Transport projection commits only an exact, fully verified generation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "community_initialize",
    ROOT / "deploy/community/initialize.py",
)
assert SPEC is not None and SPEC.loader is not None
INITIALIZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INITIALIZE)


class CommunityTransportProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "transport"
        self.source.mkdir(mode=0o700)
        os.chmod(self.source, 0o700)
        self.values = {
            name: f"transport:{index}:{name}\n".encode()
            for index, name in enumerate(INITIALIZE.TRANSPORT_FILES)
        }
        for name, value in self.values.items():
            path = self.source / name
            path.write_bytes(value)
            os.chmod(path, 0o600)

        self.volumes = self.root / "volumes"
        self.volumes.mkdir(mode=0o700)
        self.owners = {
            name: (os.getuid(), os.getgid())
            for name in INITIALIZE.OWNER_IDS
        }
        for name in self.owners:
            path = self.volumes / name
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
        self.generation = INITIALIZE.transport_generation(self.values)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def receipts(self) -> list[Path]:
        return [
            self.volumes / name / INITIALIZE.TRANSPORT_RECEIPT
            for name in INITIALIZE.TRANSPORT_GROUP_FILES
        ]

    def seed_receipts(self) -> None:
        for receipt in self.receipts():
            receipt.write_text("f" * 64 + "\n")
            os.chmod(receipt, 0o600)

    def assert_no_receipts(self) -> None:
        self.assertTrue(all(not receipt.exists() for receipt in self.receipts()))

    def test_projects_exact_bytes_then_writes_identical_receipts_last(self) -> None:
        writes: list[Path] = []
        real_write = INITIALIZE.write_exact

        def recording_write(destination: Path, value: bytes, uid: int, gid: int) -> None:
            real_write(destination, value, uid, gid)
            writes.append(destination)

        with patch.object(INITIALIZE, "write_exact", side_effect=recording_write):
            INITIALIZE.project_transport(
                self.source,
                self.volumes,
                self.generation,
                owners=self.owners,
            )

        expected_receipts = self.receipts()
        self.assertEqual(writes[-4:], expected_receipts)
        mapping = {
            name: hashlib.sha256(self.values[name]).hexdigest()
            for name in INITIALIZE.TRANSPORT_FILES
        }
        expected_generation = hashlib.sha256(
            json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(self.generation, expected_generation)

        for group, files in INITIALIZE.TRANSPORT_GROUP_FILES.items():
            for name in files:
                path = self.volumes / group / name
                self.assertEqual(path.read_bytes(), self.values[name])
                info = path.lstat()
                self.assertTrue(stat.S_ISREG(info.st_mode))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
                self.assertEqual((info.st_uid, info.st_gid), self.owners[group])
                self.assertEqual(info.st_nlink, 1)
        for receipt in expected_receipts:
            self.assertEqual(receipt.read_bytes(), (self.generation + "\n").encode())
            self.assertEqual(stat.S_IMODE(receipt.lstat().st_mode), 0o600)

    def test_rejects_inexact_or_unsafe_source_and_removes_stale_receipts(self) -> None:
        mutations = {
            "extra": lambda: (self.source / "unexpected").write_bytes(b"x"),
            "public-mode": lambda: os.chmod(self.source / "ca.crt", 0o644),
            "oversized": lambda: (self.source / "ca.crt").write_bytes(
                b"x" * (INITIALIZE.MAX_TRANSPORT_FILE_BYTES + 1)
            ),
            "symlink": self._replace_ca_with_symlink,
            "hardlink": self._replace_ca_with_hardlink,
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.tearDown()
                self.setUp()
                self.seed_receipts()
                mutate()
                with self.assertRaisesRegex(ValueError, "community.transport.projection.invalid"):
                    INITIALIZE.project_transport(
                        self.source,
                        self.volumes,
                        self.generation,
                        owners=self.owners,
                    )
                self.assert_no_receipts()

    def _replace_ca_with_symlink(self) -> None:
        target = self.root / "outside-ca"
        target.write_bytes(self.values["ca.crt"])
        os.chmod(target, 0o600)
        (self.source / "ca.crt").unlink()
        (self.source / "ca.crt").symlink_to(target)

    def _replace_ca_with_hardlink(self) -> None:
        target = self.root / "outside-ca"
        target.write_bytes(self.values["ca.crt"])
        os.chmod(target, 0o600)
        (self.source / "ca.crt").unlink()
        os.link(target, self.source / "ca.crt")

    def test_generation_mismatch_removes_stale_receipts(self) -> None:
        self.seed_receipts()
        with self.assertRaisesRegex(ValueError, "community.transport.projection.invalid"):
            INITIALIZE.project_transport(
                self.source,
                self.volumes,
                "0" * 64,
                owners=self.owners,
            )
        self.assert_no_receipts()

    def test_destination_tamper_is_detected_before_receipts(self) -> None:
        real_write = INITIALIZE.write_exact

        def tampering_write(destination: Path, value: bytes, uid: int, gid: int) -> None:
            real_write(destination, value, uid, gid)
            if destination.name == "postgres.crt":
                os.chmod(destination, 0o644)

        with patch.object(INITIALIZE, "write_exact", side_effect=tampering_write):
            with self.assertRaisesRegex(ValueError, "community.transport.projection.invalid"):
                INITIALIZE.project_transport(
                    self.source,
                    self.volumes,
                    self.generation,
                    owners=self.owners,
                )
        self.assert_no_receipts()

    def test_partial_receipt_failure_clears_every_receipt(self) -> None:
        real_write = INITIALIZE.write_exact

        def failing_write(destination: Path, value: bytes, uid: int, gid: int) -> None:
            if destination == self.volumes / "trust" / INITIALIZE.TRANSPORT_RECEIPT:
                raise OSError("injected failure")
            real_write(destination, value, uid, gid)

        with patch.object(INITIALIZE, "write_exact", side_effect=failing_write):
            with self.assertRaisesRegex(ValueError, "community.transport.projection.invalid"):
                INITIALIZE.project_transport(
                    self.source,
                    self.volumes,
                    self.generation,
                    owners=self.owners,
                )
        self.assert_no_receipts()

    def test_initializer_invalidates_receipts_before_other_projection(self) -> None:
        self.seed_receipts()
        with patch.object(INITIALIZE, "copy_exact", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                INITIALIZE.initialize(
                    self.source,
                    self.root / "public",
                    self.volumes,
                    self.root / "collector.yaml",
                    self.root / "dashboard.json",
                    {
                        "IIP_COMMUNITY_TRANSPORT_GENERATION": self.generation,
                        "IIP_COMMUNITY_POSTGRES_PASSWORD": "database-secret",
                        "IIP_COMMUNITY_GRAFANA_PASSWORD": "grafana-secret",
                    },
                    owners=self.owners,
                )
        self.assert_no_receipts()

    def test_initializer_preserves_nontransport_projection_and_commits_transport_last(self) -> None:
        public = self.root / "public"
        public.mkdir(mode=0o700)
        public_values = {
            "pg_hba.conf": b"database-access\n",
            "prometheus.yml": b"prometheus-configuration\n",
            "datasources.yaml": b"datasource-configuration\n",
            "dashboards.yaml": b"dashboard-provider\n",
        }
        for name, value in public_values.items():
            (public / name).write_bytes(value)
        collector = self.root / "collector.yaml"
        collector.write_bytes(b"collector-configuration\n")
        dashboard = self.root / "dashboard.json"
        dashboard.write_bytes(b'{"dashboard":true}\n')
        writes: list[Path] = []
        real_write = INITIALIZE.write_exact

        def recording_write(destination: Path, value: bytes, uid: int, gid: int) -> None:
            real_write(destination, value, uid, gid)
            writes.append(destination)

        with patch.object(INITIALIZE, "write_exact", side_effect=recording_write):
            INITIALIZE.initialize(
                self.source,
                public,
                self.volumes,
                collector,
                dashboard,
                {
                    "IIP_COMMUNITY_TRANSPORT_GENERATION": self.generation,
                    "IIP_COMMUNITY_POSTGRES_PASSWORD": "database-secret",
                    "IIP_COMMUNITY_GRAFANA_PASSWORD": "grafana-secret",
                },
                owners=self.owners,
            )

        self.assertEqual(writes[-4:], self.receipts())
        self.assertEqual((self.volumes / "database/pg_hba.conf").read_bytes(), public_values["pg_hba.conf"])
        self.assertEqual((self.volumes / "database/password").read_text(), "database-secret")
        self.assertEqual((self.volumes / "prometheus/prometheus.yml").read_bytes(), public_values["prometheus.yml"])
        self.assertEqual((self.volumes / "collector/collector.yaml").read_bytes(), collector.read_bytes())
        self.assertEqual((self.volumes / "grafana/dashboard.json").read_bytes(), dashboard.read_bytes())
        self.assertEqual((self.volumes / "grafana/admin-password").read_text(), "grafana-secret")
        self.assertEqual(
            (self.volumes / "grafana/datasources/datasources.yaml").read_bytes(),
            public_values["datasources.yaml"],
        )
        self.assertEqual(
            (self.volumes / "grafana/dashboards/dashboards.yaml").read_bytes(),
            public_values["dashboards.yaml"],
        )


if __name__ == "__main__":
    unittest.main()
