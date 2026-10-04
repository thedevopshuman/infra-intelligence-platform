"""Encrypted backup publication never exposes unauthenticated plaintext."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scripts import community_backup_crypto as crypto


class CommunityBackupCryptoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.key = self.root / "key"
        self.source = self.root / "plain"
        self.encrypted = self.root / "backup.iip"
        self.restored = self.root / "restored"
        crypto.create_key(self.key)
        self.payload = b"synthetic-backup-payload\x00" * 71
        self.write(self.source, self.payload)

    def write(self, path: Path, value: bytes) -> None:
        with path.open("wb") as stream:
            stream.write(value)
        path.chmod(0o600)

    def no_staging(self) -> None:
        self.assertEqual(list(self.root.glob(".iip-backup-*.tmp")), [])

    def encrypt(self) -> None:
        crypto.encrypt_file(self.source, self.encrypted, self.key)

    def test_multi_chunk_roundtrip_and_fixed_envelope(self) -> None:
        with patch.object(crypto, "CHUNK_BYTES", 37):
            self.encrypt()
            crypto.decrypt_file(self.encrypted, self.restored, self.key)
        self.assertEqual(self.restored.read_bytes(), self.payload)
        envelope = self.encrypted.read_bytes()
        self.assertEqual(envelope[:len(crypto.MAGIC)], b"IIPBACKUP\x00\x01")
        self.assertEqual(len(envelope), len(self.payload) + crypto.ENVELOPE_OVERHEAD)
        self.assertNotIn(self.payload[:100], envelope)
        for path in (self.key, self.encrypted, self.restored):
            info = path.stat()
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(info.st_uid, os.getuid())
        self.no_staging()

    def test_key_is_exact_random_binary_and_nonce_changes_per_encryption(self) -> None:
        second_key = self.root / "second-key"
        second_encrypted = self.root / "second-backup"
        crypto.create_key(second_key)
        self.assertEqual(len(self.key.read_bytes()), 32)
        self.assertEqual(len(second_key.read_bytes()), 32)
        self.assertNotEqual(self.key.read_bytes(), second_key.read_bytes())
        self.encrypt()
        crypto.encrypt_file(self.source, second_encrypted, self.key)
        self.assertNotEqual(
            self.encrypted.read_bytes()[len(crypto.MAGIC):crypto.HEADER_BYTES],
            second_encrypted.read_bytes()[len(crypto.MAGIC):crypto.HEADER_BYTES],
        )

    def test_envelope_interoperates_with_independent_one_shot_aead_api(self) -> None:
        self.encrypt()
        envelope = self.encrypted.read_bytes()
        header = envelope[:crypto.HEADER_BYTES]
        nonce = header[len(crypto.MAGIC):]
        self.assertEqual(
            AESGCM(self.key.read_bytes()).decrypt(nonce, envelope[crypto.HEADER_BYTES:], header),
            self.payload,
        )
        nonce = os.urandom(crypto.NONCE_BYTES)
        header = crypto.MAGIC + nonce
        self.write(self.encrypted, header + AESGCM(self.key.read_bytes()).encrypt(nonce, self.payload, header))
        crypto.decrypt_file(self.encrypted, self.restored, self.key)
        self.assertEqual(self.restored.read_bytes(), self.payload)

    def test_empty_payload_is_authenticated_at_zero_limit(self) -> None:
        self.write(self.source, b"")
        crypto.encrypt_file(self.source, self.encrypted, self.key, max_bytes=0)
        crypto.decrypt_file(self.encrypted, self.restored, self.key, max_bytes=0)
        self.assertEqual(self.encrypted.stat().st_size, crypto.ENVELOPE_OVERHEAD)
        self.assertEqual(self.restored.read_bytes(), b"")

    def test_magic_version_nonce_ciphertext_and_tag_tampering_never_publish(self) -> None:
        self.encrypt()
        original = self.encrypted.read_bytes()
        for index in (0, len(crypto.MAGIC) - 1, len(crypto.MAGIC), crypto.HEADER_BYTES, -1):
            with self.subTest(index=index):
                altered = bytearray(original)
                altered[index] ^= 1
                self.write(self.encrypted, bytes(altered))
                with self.assertRaises(crypto.BackupCryptoError):
                    crypto.decrypt_file(self.encrypted, self.restored, self.key)
                self.assertFalse(self.restored.exists())
                self.no_staging()

    def test_wrong_key_is_stable_authentication_error_without_output(self) -> None:
        self.encrypt()
        wrong_key = self.root / "wrong-key"
        crypto.create_key(wrong_key)
        with self.assertRaisesRegex(crypto.BackupCryptoError, r"^community\.backup\.crypto\.authentication\.failed$"):
            crypto.decrypt_file(self.encrypted, self.restored, wrong_key)
        self.assertFalse(self.restored.exists())
        self.no_staging()

    def test_truncated_or_appended_envelope_is_not_published(self) -> None:
        self.encrypt()
        original = self.encrypted.read_bytes()
        for altered in (b"", original[:5], original[:crypto.HEADER_BYTES], original[:-1], original + b"x"):
            with self.subTest(length=len(altered)):
                self.write(self.encrypted, altered)
                with self.assertRaises(crypto.BackupCryptoError):
                    crypto.decrypt_file(self.encrypted, self.restored, self.key)
                self.assertFalse(self.restored.exists())
                self.no_staging()

    def test_plaintext_and_encrypted_input_limits_are_exact(self) -> None:
        with self.assertRaisesRegex(crypto.BackupCryptoError, "size.exceeded"):
            crypto.encrypt_file(self.source, self.encrypted, self.key, max_bytes=len(self.payload) - 1)
        self.assertFalse(self.encrypted.exists())
        crypto.encrypt_file(self.source, self.encrypted, self.key, max_bytes=len(self.payload))
        with self.assertRaisesRegex(crypto.BackupCryptoError, "size.exceeded"):
            crypto.decrypt_file(self.encrypted, self.restored, self.key, max_bytes=len(self.payload) - 1)
        self.assertFalse(self.restored.exists())
        crypto.decrypt_file(self.encrypted, self.restored, self.key, max_bytes=len(self.payload))
        self.assertEqual(self.restored.read_bytes(), self.payload)
        self.no_staging()

    def test_invalid_limits_cannot_disable_hard_cap(self) -> None:
        for value in (-1, True, 1.0, "1", crypto.MAX_PLAINTEXT_BYTES + 1):
            with self.subTest(value=value):
                for operation in (crypto.encrypt_file, crypto.decrypt_file):
                    with self.assertRaisesRegex(crypto.BackupCryptoError, "limit.invalid"):
                        operation(self.source, self.encrypted, self.key, max_bytes=value)
        self.assertFalse(self.encrypted.exists())

    def test_key_permissions_and_length_are_strict(self) -> None:
        original = self.key.read_bytes()
        for value in (b"", b"a" * 31, b"a" * 33, b'{"key":"not-a-key"}'):
            self.write(self.key, value)
            with self.assertRaises(crypto.BackupCryptoError):
                self.encrypt()
        self.write(self.key, original)
        for mode in (0o400, 0o640, 0o644, 0o660, 0o1600):
            self.key.chmod(mode)
            with self.assertRaises(crypto.BackupCryptoError):
                self.encrypt()
        self.assertFalse(self.encrypted.exists())

    def test_unprotected_source_and_parent_fail_closed(self) -> None:
        for mode in (0o400, 0o640, 0o644):
            self.source.chmod(mode)
            with self.assertRaises(crypto.BackupCryptoError):
                self.encrypt()
        self.source.chmod(0o600)
        self.root.chmod(0o750)
        try:
            with self.assertRaises(crypto.BackupCryptoError):
                self.encrypt()
            with self.assertRaises(crypto.BackupCryptoError):
                crypto.create_key(self.root / "new-key")
        finally:
            self.root.chmod(0o700)
        self.assertFalse(self.encrypted.exists())

    def test_unprotected_output_directory_is_rejected(self) -> None:
        output = self.root / "shared"
        output.mkdir(mode=0o755)
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.encrypt_file(self.source, output / "backup", self.key)
        self.assertEqual(list(output.iterdir()), [])

    def test_owner_mismatch_is_rejected(self) -> None:
        with patch.object(crypto.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(crypto.BackupCryptoError):
                self.encrypt()
            with self.assertRaises(crypto.BackupCryptoError):
                crypto.create_key(self.root / "new-key")

    def test_input_symlinks_and_hardlinks_are_rejected(self) -> None:
        for original in (self.source, self.key):
            for hard in (False, True):
                with self.subTest(original=original.name, hard=hard):
                    alias = self.root / "alias"
                    os.link(original, alias) if hard else alias.symlink_to(original)
                    try:
                        source, key = (alias, self.key) if original == self.source else (self.source, alias)
                        with self.assertRaises(crypto.BackupCryptoError):
                            crypto.encrypt_file(source, self.encrypted, key)
                    finally:
                        alias.unlink()
        self.assertFalse(self.encrypted.exists())

    def test_encrypted_input_obeys_same_file_protection_rules(self) -> None:
        self.encrypt()
        alias = self.root / "alias"
        alias.symlink_to(self.encrypted)
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.decrypt_file(alias, self.restored, self.key)
        alias.unlink()
        os.link(self.encrypted, alias)
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.decrypt_file(self.encrypted, self.restored, self.key)
        alias.unlink()
        self.encrypted.chmod(0o644)
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.decrypt_file(self.encrypted, self.restored, self.key)
        self.assertFalse(self.restored.exists())
        self.no_staging()

    def test_nonregular_inputs_do_not_block(self) -> None:
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        directory = self.root / "directory"
        directory.mkdir(mode=0o700)
        for source in (fifo, directory):
            with self.subTest(source=source.name):
                with self.assertRaises(crypto.BackupCryptoError):
                    crypto.encrypt_file(source, self.encrypted, self.key)
                with self.assertRaises(crypto.BackupCryptoError):
                    crypto.encrypt_file(self.source, self.encrypted, source)

    def test_symlinked_parent_and_nonabsolute_paths_are_rejected(self) -> None:
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        link = self.root / "link"
        link.symlink_to(actual, target_is_directory=True)
        for destination in (link / "backup", Path("relative-backup"), self.root / ".." / "backup"):
            with self.assertRaises(crypto.BackupCryptoError):
                crypto.encrypt_file(self.source, destination, self.key)
        self.assertEqual(list(actual.iterdir()), [])

    def test_source_and_key_parent_symlinks_are_rejected_without_resolving(self) -> None:
        link = self.root / "alias-directory"
        link.symlink_to(self.root, target_is_directory=True)
        for source, key in ((link / self.source.name, self.key),
                            (self.source, link / self.key.name)):
            with self.subTest(source=str(source), key=str(key)):
                with self.assertRaises(crypto.BackupCryptoError):
                    crypto.encrypt_file(source, self.encrypted, key)
                self.assertFalse(self.encrypted.exists())
                self.no_staging()
        self.encrypt()
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.decrypt_file(link / self.encrypted.name, self.restored, self.key)
        self.assertFalse(self.restored.exists())

    def test_existing_destinations_never_change(self) -> None:
        self.encrypt()
        original = self.encrypted.read_bytes()
        with self.assertRaisesRegex(crypto.BackupCryptoError, "destination.exists"):
            self.encrypt()
        self.assertEqual(self.encrypted.read_bytes(), original)
        self.write(self.restored, b"existing-data")
        with self.assertRaisesRegex(crypto.BackupCryptoError, "destination.exists"):
            crypto.decrypt_file(self.encrypted, self.restored, self.key)
        self.assertEqual(self.restored.read_bytes(), b"existing-data")
        self.no_staging()

    def test_in_place_operation_is_rejected_without_touching_input(self) -> None:
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.encrypt_file(self.source, self.source, self.key)
        self.assertEqual(self.source.read_bytes(), self.payload)
        self.encrypt()
        original = self.encrypted.read_bytes()
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.decrypt_file(self.encrypted, self.encrypted, self.key)
        self.assertEqual(self.encrypted.read_bytes(), original)

    def test_existing_symlink_hardlink_fifo_and_directory_destinations_are_untouched(self) -> None:
        target = self.root / "target"
        self.write(target, b"keep")
        for kind in ("symlink", "hardlink", "fifo", "directory"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.encrypted.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, self.encrypted)
                elif kind == "fifo":
                    os.mkfifo(self.encrypted, 0o600)
                else:
                    self.encrypted.mkdir(mode=0o700)
                with self.assertRaises(crypto.BackupCryptoError):
                    self.encrypt()
                self.assertEqual(target.read_bytes(), b"keep")
                self.encrypted.rmdir() if kind == "directory" else self.encrypted.unlink()

    def test_atomic_publish_refuses_concurrently_created_destination(self) -> None:
        real_link = os.link

        def raced_link(source: str, destination: str, **kwargs: object) -> None:
            self.write(self.encrypted, b"concurrent-owner-data")
            real_link(source, destination, **kwargs)

        with patch.object(crypto.os, "link", side_effect=raced_link):
            with self.assertRaises(crypto.BackupCryptoError):
                self.encrypt()
        self.assertEqual(self.encrypted.read_bytes(), b"concurrent-owner-data")
        self.no_staging()

    def test_decryption_does_not_publish_until_authentication_succeeds(self) -> None:
        self.encrypt()
        real_link = os.link
        calls = []

        def checked_link(source: str, destination: str, **kwargs: object) -> None:
            self.assertFalse(self.restored.exists())
            calls.append(destination)
            real_link(source, destination, **kwargs)

        altered = bytearray(self.encrypted.read_bytes())
        altered[-1] ^= 1
        self.write(self.encrypted, altered)
        with patch.object(crypto.os, "link", side_effect=checked_link):
            with self.assertRaises(crypto.BackupCryptoError):
                crypto.decrypt_file(self.encrypted, self.restored, self.key)
        self.assertEqual(calls, [])
        self.no_staging()

    def test_key_creation_never_replaces_and_cleans_its_failed_file(self) -> None:
        original = self.key.read_bytes()
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.create_key(self.key)
        self.assertEqual(self.key.read_bytes(), original)
        new_key = self.root / "new-key"
        with patch.object(crypto.os, "urandom", side_effect=OSError("secret-detail")):
            with self.assertRaises(crypto.BackupCryptoError) as error:
                crypto.create_key(new_key)
        self.assertNotIn("secret-detail", str(error.exception))
        self.assertFalse(new_key.exists())

    def test_key_creation_refuses_symlink_and_failed_sync_removes_only_new_key(self) -> None:
        new_key = self.root / "new-key"
        new_key.symlink_to(self.source)
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.create_key(new_key)
        self.assertTrue(new_key.is_symlink())
        self.assertEqual(self.source.read_bytes(), self.payload)
        new_key.unlink()
        with patch.object(crypto.os, "fsync", side_effect=OSError("secret-device-name")):
            with self.assertRaises(crypto.BackupCryptoError):
                crypto.create_key(new_key)
        self.assertFalse(new_key.exists())
        self.assertEqual(len(self.key.read_bytes()), 32)

    def test_sync_failure_cleans_only_this_operations_staging_file(self) -> None:
        other_staging = self.root / ".iip-backup-other.tmp"
        self.write(other_staging, b"another-operation")
        with patch.object(crypto.os, "fsync", side_effect=OSError("secret-device-name")):
            with self.assertRaises(crypto.BackupCryptoError) as error:
                self.encrypt()
        self.assertNotIn("secret-device-name", str(error.exception))
        self.assertFalse(self.encrypted.exists())
        self.assertEqual(list(self.root.glob(".iip-backup-*.tmp")), [other_staging])
        self.assertEqual(other_staging.read_bytes(), b"another-operation")

    def test_postpublication_sync_failure_cleans_own_destination(self) -> None:
        real_fsync = os.fsync

        def failed_directory_sync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("device-detail")
            real_fsync(descriptor)

        with patch.object(crypto.os, "fsync", side_effect=failed_directory_sync):
            with self.assertRaises(crypto.BackupCryptoError):
                self.encrypt()
        self.assertFalse(self.encrypted.exists())
        self.no_staging()

    def test_source_mutation_fails_instead_of_publishing_inconsistent_snapshot(self) -> None:
        real_cipher = crypto.Cipher

        def changed_source(*args: object, **kwargs: object) -> object:
            self.write(self.source, b"changed" + self.payload)
            return real_cipher(*args, **kwargs)

        with patch.object(crypto, "Cipher", side_effect=changed_source):
            with self.assertRaisesRegex(crypto.BackupCryptoError, "source.changed"):
                self.encrypt()
        self.assertFalse(self.encrypted.exists())
        self.no_staging()

    def test_authenticated_source_rewrite_is_rejected_before_plaintext_publication(self) -> None:
        self.encrypt()
        original = self.encrypted.read_bytes()
        original_stat = self.encrypted.stat()
        real_cipher = crypto.Cipher

        def rewritten_source(*args: object, **kwargs: object) -> object:
            # Authentication still succeeds for these identical bytes, so the
            # source consistency check must independently reject the rewrite.
            self.write(self.encrypted, original)
            os.utime(self.encrypted, ns=(original_stat.st_atime_ns,
                                        original_stat.st_mtime_ns + 1_000_000_000))
            return real_cipher(*args, **kwargs)

        with patch.object(crypto, "Cipher", side_effect=rewritten_source):
            with self.assertRaisesRegex(crypto.BackupCryptoError, "source.changed"):
                crypto.decrypt_file(self.encrypted, self.restored, self.key)
        self.assertFalse(self.restored.exists())
        self.no_staging()

    def test_errors_and_operations_emit_no_input_values(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        secret_path = self.root / "sensitive-customer-name"
        with redirect_stdout(output), redirect_stderr(errors):
            self.encrypt()
            with self.assertRaises(crypto.BackupCryptoError) as error:
                crypto.decrypt_file(secret_path, self.restored, self.key)
        self.assertNotIn("sensitive-customer-name", str(error.exception))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
