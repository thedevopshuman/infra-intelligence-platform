"""Exact private certificate-profile checks for offline trust lifecycle tooling."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID, ObjectIdentifier

from scripts import community_transport as transport


class CommunityTransportDirectoryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.directory = self.state / "transport"
        generated = []
        original = ec.generate_private_key

        def generate(curve):
            key = original(curve)
            generated.append(key)
            return key

        with patch.object(transport.ec, "generate_private_key", side_effect=generate):
            transport.write_transport(self.state)
        self.ca_key = generated[0]  # Held in test memory only, never persisted by the writer.

    def certificate(self, name="ca"):
        return x509.load_pem_x509_certificate((self.directory / f"{name}.crt").read_bytes())

    def resign(self, name, *, replacements=None, extra=(), public_key=None,
               subject=None, issuer=None, not_before=None, not_after=None,
               signer=None, algorithm=None):
        current = self.certificate(name)
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject or current.subject)
            .issuer_name(issuer or current.issuer)
            .public_key(public_key or current.public_key())
            .serial_number(current.serial_number)
            .not_valid_before(not_before or current.not_valid_before_utc)
            .not_valid_after(not_after or current.not_valid_after_utc)
        )
        for extension in current.extensions:
            value, critical = (replacements or {}).get(extension.oid, (extension.value, extension.critical))
            if value is not None:
                builder = builder.add_extension(value, critical)
        for value, critical in extra:
            builder = builder.add_extension(value, critical)
        certificate = builder.sign(signer or self.ca_key, algorithm or hashes.SHA256())
        (self.directory / f"{name}.crt").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

    def invalid(self, *, require_current=False):
        with self.assertRaisesRegex(ValueError, "^community.transport.invalid-or-expired$") as error:
            transport.validate_transport_directory(self.directory, require_current=require_current)
        self.assertIsNone(error.exception.__cause__)

    def test_summary_is_only_the_intersection_and_public_ca_der_digest(self) -> None:
        summary = transport.validate_transport_directory(self.directory)
        certificates = [self.certificate(), *(self.certificate(name) for name in transport._LEAF_IDENTITIES)]
        self.assertEqual(summary, {
            "notBefore": max(cert.not_valid_before_utc for cert in certificates).isoformat().replace("+00:00", "Z"),
            "notAfter": min(cert.not_valid_after_utc for cert in certificates).isoformat().replace("+00:00", "Z"),
            "caSha256": hashlib.sha256(self.certificate().public_bytes(serialization.Encoding.DER)).hexdigest(),
        })
        self.assertIsNone(transport.validate_transport(self.state))

    def test_summary_uses_latest_start_and_earliest_expiry_across_distinct_leaf_windows(self) -> None:
        current = self.certificate("collector")
        later = current.not_valid_before_utc + timedelta(seconds=30)
        earlier = current.not_valid_after_utc - timedelta(days=7)
        self.resign("collector", not_before=later)
        self.resign("postgres", not_after=earlier)
        summary = transport.validate_transport_directory(self.directory)
        self.assertEqual(summary["notBefore"], later.isoformat().replace("+00:00", "Z"))
        self.assertEqual(summary["notAfter"], earlier.isoformat().replace("+00:00", "Z"))

    def test_expired_or_future_material_has_structural_validation_without_startup_authority(self) -> None:
        initial = transport.validate_transport_directory(self.directory)
        leaf = self.certificate("collector")
        for now in (leaf.not_valid_after_utc + timedelta(days=3), leaf.not_valid_before_utc - timedelta(seconds=1)):
            with self.subTest(now=now):
                self.assertEqual(transport.validate_transport_directory(self.directory, require_current=False, now=now), initial)
                with self.assertRaises(ValueError):
                    transport.validate_transport_directory(self.directory, now=now)
        with self.assertRaises(ValueError):
            transport.validate_transport_directory(self.directory, now=leaf.not_valid_after_utc)

    def test_naive_time_or_non_boolean_admission_flag_is_rejected(self) -> None:
        for kwargs in ({"now": datetime.now()}, {"require_current": 0}, {"require_current": "false"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                transport.validate_transport_directory(self.directory, **kwargs)

    def test_ca_path_length_must_be_exactly_zero(self) -> None:
        self.resign("ca", replacements={ExtensionOID.BASIC_CONSTRAINTS: (x509.BasicConstraints(True, None), True)})
        self.invalid()

    def test_ca_key_usage_must_not_grant_leaf_signing_purpose(self) -> None:
        self.resign("ca", replacements={ExtensionOID.KEY_USAGE: (
            x509.KeyUsage(True, False, False, False, False, True, True, False, False), True,
        )})
        self.invalid()

    def test_ca_requires_certificate_signing_key_usage(self) -> None:
        self.resign("ca", replacements={ExtensionOID.KEY_USAGE: (
            x509.KeyUsage(False, False, False, False, False, False, True, False, False), True,
        )})
        self.invalid()

    def test_leaf_cannot_be_a_ca(self) -> None:
        self.resign("collector", replacements={ExtensionOID.BASIC_CONSTRAINTS: (x509.BasicConstraints(True, 0), True)})
        self.invalid()

    def test_leaf_key_usage_must_be_exact_digital_signature_only(self) -> None:
        self.resign("collector", replacements={ExtensionOID.KEY_USAGE: (
            x509.KeyUsage(True, False, True, False, False, False, False, False, False), True,
        )})
        self.invalid()

    def test_critical_extension_flags_are_enforced(self) -> None:
        self.resign("collector", replacements={ExtensionOID.BASIC_CONSTRAINTS: (x509.BasicConstraints(False, None), False)})
        self.invalid()

    def test_unknown_extension_is_rejected_even_when_noncritical(self) -> None:
        self.resign("collector", extra=[(x509.UnrecognizedExtension(ObjectIdentifier("1.2.3.4.5"), b"ignored"), False)])
        self.invalid()

    def test_missing_extension_is_rejected(self) -> None:
        self.resign("collector", replacements={ExtensionOID.KEY_USAGE: (None, False)})
        self.invalid()

    def test_duplicate_san_identity_is_not_normalized_away(self) -> None:
        names = list(self.certificate("collector").extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
        self.resign("collector", replacements={ExtensionOID.SUBJECT_ALTERNATIVE_NAME: (x509.SubjectAlternativeName(names + names), False)})
        self.invalid()

    def test_additional_eku_is_rejected(self) -> None:
        self.resign("collector", replacements={ExtensionOID.EXTENDED_KEY_USAGE: (
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]), False,
        )})
        self.invalid()

    def test_subject_must_match_exact_leaf_role(self) -> None:
        self.resign("collector", subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "other-client")]))
        self.invalid()

    def test_subject_and_authority_key_identifiers_are_not_ignored(self) -> None:
        original = (self.directory / "collector.crt").read_bytes()
        for oid, value in (
            (ExtensionOID.SUBJECT_KEY_IDENTIFIER, x509.SubjectKeyIdentifier(b"x" * 20)),
            (ExtensionOID.AUTHORITY_KEY_IDENTIFIER, x509.AuthorityKeyIdentifier(b"x" * 20, None, None)),
        ):
            with self.subTest(oid=oid):
                (self.directory / "collector.crt").write_bytes(original)
                self.resign("collector", replacements={oid: (value, False)})
                self.invalid()

    def test_validity_outside_ca_window_is_structurally_invalid(self) -> None:
        self.resign("collector", not_after=self.certificate().not_valid_after_utc + timedelta(days=1))
        self.invalid()

    def test_valid_signature_from_unrelated_key_is_rejected(self) -> None:
        self.resign("collector", signer=ec.generate_private_key(ec.SECP256R1()))
        self.invalid()

    def test_non_profile_signature_hash_is_rejected(self) -> None:
        self.resign("collector", algorithm=hashes.SHA384())
        self.invalid()

    def test_even_a_matching_leaf_keypair_must_use_p256(self) -> None:
        key = ec.generate_private_key(ec.SECP384R1())
        self.resign("collector", public_key=key.public_key(), replacements={ExtensionOID.SUBJECT_KEY_IDENTIFIER: (
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False,
        )})
        (self.directory / "collector.key").write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
        self.invalid()

    def test_valid_reissued_chain_with_non_p256_ca_is_rejected(self) -> None:
        key = ec.generate_private_key(ec.SECP384R1())
        authority = x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key())
        self.resign("ca", public_key=key.public_key(), signer=key, replacements={
            ExtensionOID.SUBJECT_KEY_IDENTIFIER: (x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False),
            ExtensionOID.AUTHORITY_KEY_IDENTIFIER: (authority, False),
        })
        for name in transport._LEAF_IDENTITIES:
            self.resign(name, signer=key, replacements={ExtensionOID.AUTHORITY_KEY_IDENTIFIER: (authority, False)})
            self.certificate(name).verify_directly_issued_by(self.certificate())
        self.certificate().verify_directly_issued_by(self.certificate())
        self.invalid()

    def test_leaf_private_keys_must_be_independent_of_other_roles_and_ca(self) -> None:
        certificate = (self.directory / "collector.crt").read_bytes()
        for key in (
            self.ca_key,
            serialization.load_pem_private_key((self.directory / "receiver-health.key").read_bytes(), password=None),
        ):
            with self.subTest(ca=key is self.ca_key):
                (self.directory / "collector.crt").write_bytes(certificate)
                self.resign("collector", public_key=key.public_key(), replacements={ExtensionOID.SUBJECT_KEY_IDENTIFIER: (
                    x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False,
                )})
                (self.directory / "collector.key").write_bytes(key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
                ))
                self.invalid()

    def test_extra_pem_blocks_or_trailing_data_are_rejected(self) -> None:
        for filename in ("ca.crt", "collector.crt", "collector.key"):
            path = self.directory / filename
            original = path.read_bytes()
            for extra in (original, b"private-extra-data", b"\n"):
                with self.subTest(filename=filename, extra_size=len(extra)):
                    path.write_bytes(original + extra)
                    self.invalid()
                    path.write_bytes(original)

    def test_hard_linked_secret_is_rejected(self) -> None:
        os.link(self.directory / "collector.key", self.state / "elsewhere.key")
        self.invalid()

    def test_symlinked_directory_or_file_is_rejected(self) -> None:
        link = self.state / "linked-transport"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            transport.validate_transport_directory(link, require_current=False)
        target = self.directory / "collector.key"
        target.unlink()
        target.symlink_to(self.directory / "receiver-health.key")
        self.invalid()

    def test_extra_or_oversized_material_is_rejected(self) -> None:
        extra = self.directory / "ca.key"
        extra.write_text("must-not-be-persisted", encoding="utf-8")
        self.invalid()
        extra.unlink()
        (self.directory / "collector.key").write_bytes(b"x" * 65537)
        self.invalid()

    def test_nonregular_fifo_is_rejected_without_waiting_for_a_writer(self) -> None:
        target = self.directory / "collector.key"
        target.unlink()
        os.mkfifo(target, mode=0o600)
        self.invalid()

    def test_writer_fsyncs_every_private_file_and_both_directory_entries(self) -> None:
        other = self.state / "new-state"
        other.mkdir(mode=0o700)
        events = []
        original = os.fsync

        def fsync(descriptor):
            info = os.fstat(descriptor)
            events.append((stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), info.st_nlink))
            original(descriptor)

        with patch.object(transport.os, "fsync", side_effect=fsync):
            transport.write_transport(other)
        self.assertEqual(events[:len(transport.TRANSPORT_FILES)], [(stat.S_IFREG, 0o600, 1)] * len(transport.TRANSPORT_FILES))
        self.assertEqual(len(events), len(transport.TRANSPORT_FILES) + 2)
        self.assertEqual([(kind, mode) for kind, mode, _ in events[-2:]], [(stat.S_IFDIR, 0o700)] * 2)


if __name__ == "__main__":
    unittest.main()
