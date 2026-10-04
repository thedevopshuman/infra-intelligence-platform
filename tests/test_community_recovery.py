"""Cold recovery verifies an encrypted whole-installation point, not live DR."""

from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import community_backup_crypto as crypto
import community_recovery as recovery
import community_stack as stack
from test_community_stack import installation_inputs


def volume_archive() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        root = tarfile.TarInfo(".")
        root.type, root.mode = tarfile.DIRTYPE, 0o700
        archive.addfile(root)
        entry = tarfile.TarInfo(".fixture")
        entry.mode, entry.size = 0o600, 10
        archive.addfile(entry, io.BytesIO(b"test-state"))
    return stream.getvalue()


def snapshot() -> dict:
    return {"format": 1, "deployment": recovery.deployment_digest(), "os": "linux", "architecture": "arm64",
            "images": {service: "sha256:" + ("a" if service in recovery.SERVICES[:5] else str(index)) * 64
                       for index, service in enumerate(recovery.SERVICES)}}


class FakeDocker:
    def __init__(self, state):
        self.source = state
        self.volumes = {f"{stack.project_name(state)}_{name}": volume_archive() for name in recovery.VOLUMES}
        self.running = set()
        self.calls = []
        self.clean = True
        self.fail_import = False
        self.hook = None
        self.images = True

    def bind(self, state):
        if not (state / "daemon.json").exists():
            stack.write_protected(state / "daemon.json", {"format": 1, "endpoint": "unix:///fixture.sock", "daemonId": "fixture-daemon"})
        return self

    def require_stopped(self, project):
        if project in self.running:
            raise recovery.RecoveryError("community.recovery.down-required")

    def require_images(self, value):
        recovery.validate_snapshot(value)
        if not self.images:
            raise recovery.RecoveryError("community.recovery.image-mismatch")

    def require_volume(self, project, name):
        volume = f"{project}_{name}"
        if volume not in self.volumes:
            raise recovery.RecoveryError("community.recovery.volume-ownership-invalid")
        return volume

    def volume_names(self):
        return set(self.volumes)

    def text(self, *args):
        self.calls.append(args)
        if args[:2] == ("volume", "create"):
            self.volumes[args[-1]] = None
            return args[-1]
        raise AssertionError(args)

    def helper(self, image, mounts, command, *, output=None, user="0:0"):
        self.calls.append(tuple(command))
        if command[0] == "pg_controldata":
            return "Database cluster state: " + ("shut down" if self.clean else "in production")
        volume = mounts[0].split("src=")[1].split(",")[0]
        if "export" in command:
            output.write(self.volumes[volume])
            if self.hook:
                self.hook()
                self.hook = None
        else:
            if self.fail_import:
                raise recovery.RecoveryError("community.recovery.helper-failed")
            source = Path(mounts[2].split("src=")[1].split(",")[0])
            self.volumes[volume] = source.read_bytes()
        return ""


class CommunityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.state = self.parent / "source"
        self.destination = self.parent / "restored"
        self.archive = self.parent / "backup.iipbak"
        self.key = self.parent / "key.bin"
        stack.initialize(self.state, installation_inputs(), image="iip-community:test")
        stack.write_protected(self.state / "runtime-images.json", snapshot())
        crypto.create_key(self.key)
        self.docker = FakeDocker(self.state)
        self.mock = patch.object(recovery, "RecoveryDocker", side_effect=self.docker.bind)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def backup(self):
        with stack.installation_lock(self.state):
            recovery.backup(self.state, self.archive, self.key)

    def restore(self, **kwargs):
        recovery.restore(self.destination, self.archive, self.key, source_fenced=True, **kwargs)

    def test_encrypted_round_trip_preserves_secrets_generations_and_volumes_stopped(self):
        self.backup()
        raw = self.archive.read_bytes()
        self.assertTrue(raw.startswith(crypto.MAGIC))
        self.assertNotIn(b"databasePassword", raw)
        self.assertNotIn(stack.read_protected(self.state / "credentials.json")["apiToken"].encode(), raw)
        self.restore()
        self.assertFalse((self.destination / ".recovery-incomplete").exists())
        self.assertEqual(stack.read_protected(self.state / "credentials.json"), stack.read_protected(self.destination / "credentials.json"))
        self.assertEqual(stack.read_protected(self.destination / "installation.json")["project"], stack.project_name(self.destination))
        self.assertEqual(stack.read_protected(self.destination / "runtime-images.json"), snapshot())
        for name in recovery.VOLUMES:
            self.assertEqual(self.docker.volumes[f"{stack.project_name(self.destination)}_{name}"], volume_archive())
        self.assertFalse(any("up" in call or "start" in call for call in self.docker.calls))
        self.assertEqual(os.stat(self.archive).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.destination).st_mode & 0o777, 0o700)

    def test_running_source_and_unclean_database_cannot_be_backed_up(self):
        self.docker.running.add(stack.project_name(self.state))
        with self.assertRaisesRegex(recovery.RecoveryError, "down-required"):
            self.backup()
        self.docker.running.clear()
        self.docker.clean = False
        with self.assertRaisesRegex(recovery.RecoveryError, "clean-database"):
            self.backup()
        self.assertFalse(self.archive.exists())

    def test_destination_is_never_overwritten(self):
        self.archive.write_bytes(b"existing")
        with self.assertRaisesRegex(recovery.RecoveryError, "destination-invalid"):
            self.backup()
        self.assertEqual(self.archive.read_bytes(), b"existing")
        self.destination.mkdir(mode=0o700)
        with self.assertRaisesRegex(recovery.RecoveryError, "destination-exists"):
            self.restore()

    def test_missing_or_different_images_cannot_restore(self):
        self.backup()
        self.docker.images = False
        with self.assertRaisesRegex(recovery.RecoveryError, "image-mismatch"):
            self.restore()
        self.assertFalse(self.destination.exists())

    def test_restore_requires_explicit_source_fencing(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "source-fencing"):
            recovery.restore(self.destination, self.archive, self.key, source_fenced=False)
        self.assertFalse(self.destination.exists())

    def test_same_daemon_running_source_still_blocks_acknowledged_restore(self):
        self.backup()
        self.docker.running.add(stack.project_name(self.state))
        with self.assertRaisesRegex(recovery.RecoveryError, "down-required"):
            self.restore()
        self.assertFalse(self.destination.exists())

    def test_existing_destination_volume_is_never_adopted(self):
        self.backup()
        name = f"{stack.project_name(self.destination)}_postgres-data"
        self.docker.volumes[name] = b"existing-data"
        with self.assertRaisesRegex(recovery.RecoveryError, "target-volumes-exist"):
            self.restore()
        self.assertEqual(self.docker.volumes[name], b"existing-data")
        self.assertFalse(self.destination.exists())

    def test_wrong_key_or_corrupted_backup_never_creates_target(self):
        self.backup()
        other = self.parent / "other.bin"
        crypto.create_key(other)
        with self.assertRaises(crypto.BackupCryptoError):
            recovery.restore(self.destination, self.archive, other, source_fenced=True)
        content = bytearray(self.archive.read_bytes())
        content[-1] ^= 1
        self.archive.write_bytes(content)
        with self.assertRaises(crypto.BackupCryptoError):
            self.restore()
        self.assertFalse(self.destination.exists())
        self.assertFalse(any(call[:2] == ("volume", "create") for call in self.docker.calls))

    def test_key_or_archive_symlink_is_not_resolved_before_crypto(self):
        linked = self.parent / "linked.key"
        linked.symlink_to(self.key)
        with self.assertRaises(crypto.BackupCryptoError):
            recovery.backup(self.state, self.archive, linked)
        self.assertFalse(self.archive.exists())
        self.backup()
        linked_archive = self.parent / "linked.iipbak"
        linked_archive.symlink_to(self.archive)
        with self.assertRaises(crypto.BackupCryptoError):
            recovery.restore(self.destination, linked_archive, self.key, source_fenced=True)
        self.assertFalse(self.destination.exists())

    def test_failed_import_preserves_fenced_partial_target(self):
        self.backup()
        self.docker.fail_import = True
        with self.assertRaisesRegex(recovery.RecoveryError, "helper-failed"):
            self.restore()
        self.assertTrue((self.destination / ".recovery-incomplete").is_file())
        self.assertEqual(stack.read_protected(self.destination / "credentials.json"), stack.read_protected(self.state / "credentials.json"))

    def test_source_mutation_before_encryption_is_rejected(self):
        def mutate():
            credentials = stack.read_protected(self.state / "credentials.json")
            credentials["apiToken"] = "z" * 64
            recovery.atomic_document(self.state / "credentials.json", credentials)
        self.docker.hook = mutate
        with self.assertRaisesRegex(recovery.RecoveryError, "source-changed"):
            self.backup()
        self.assertFalse(self.archive.exists())

    def test_installer_change_is_not_an_implicit_migration(self):
        self.backup()
        with patch.object(recovery, "deployment_digest", return_value="b" * 64):
            with self.assertRaisesRegex(recovery.RecoveryError, "same-deployment"):
                self.restore()
        self.assertFalse(self.destination.exists())

    def test_expired_catalog_does_not_prevent_cold_rescue(self):
        with patch.object(stack, "validate_inputs", side_effect=AssertionError("expiry validation belongs to startup")):
            self.backup()
            self.restore()

    def test_snapshot_rejects_bool_or_mixed_application_images(self):
        candidate = snapshot()
        candidate["format"] = True
        with self.assertRaises(recovery.RecoveryError):
            recovery.validate_snapshot(candidate)
        candidate = snapshot()
        candidate["images"]["migrate"] = "sha256:" + "f" * 64
        with self.assertRaises(recovery.RecoveryError):
            recovery.validate_snapshot(candidate)

    def test_limits_and_incomplete_source_fail_closed(self):
        for limit in (True, 0, recovery.MAX_BYTES + 1):
            with self.assertRaisesRegex(recovery.RecoveryError, "size-limit"):
                recovery.backup(self.state, self.archive, self.key, max_bytes=limit)
        (self.state / ".recovery-incomplete").write_text("unfinished")
        with self.assertRaisesRegex(recovery.RecoveryError, "incomplete"):
            self.backup()


if __name__ == "__main__":
    unittest.main()
